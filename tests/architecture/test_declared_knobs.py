# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""声明出来的旋钮必须真的被拧动（V18 第 3–4 轮）。

四格同族形状，都是"文档/注册面对读者做了一个代码里没有的承诺"：

* ``Parameters`` 段点名一个入参，函数体从头到尾没读它——调用方拧了个空开关，
  比根本没有这个开关更糟（他以为已经关掉前缀归一了）；
* 进程级惰性单例自称"线程安全"，实际是无锁 check-then-act；
* "共 N 个（见 :data:`X`）"里的 N 与被点名集合的真值不符；
* CLI 面 ``add_argument`` 注册一个 ``--flag``，处理链路里没有人读它的 dest（第 4 轮）。

四者都能用 AST + 现算真值判掉，因此这里是**派生**判据而不是抄来的名单。
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "tstdx"

# --------------------------------------------------------------------------- #
# 判据一：文档承诺的入参，函数体必须读它
# --------------------------------------------------------------------------- #

_SECTION = re.compile(
    r"^(Parameters|Args|Arguments|参数|Returns|Raises|Yields|Notes|"
    r"Example|Examples|Attributes|Warnings|See Also|References)\b",
    re.I,
)
_PARAM_ITEM = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")


def _documented_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """numpydoc ``Parameters`` 段里点名的参数名。"""
    doc = ast.get_docstring(fn)
    if not doc:
        return set()
    names: set[str] = set()
    inside = False
    for line in doc.splitlines():
        stripped = line.strip()
        if _SECTION.match(stripped):
            inside = stripped.lower().startswith(("parameters", "args", "arguments", "参数"))
            continue
        if inside and (m := _PARAM_ITEM.match(stripped)):
            names.add(m.group(1))
    return names


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """协议 / 抽象 / ``@overload`` 桩不构成承诺（它本来就还没实现）。"""
    body = [n for n in fn.body if not _is_inert(n)]
    if not body or all(isinstance(n, ast.Pass) for n in body):
        return True
    return len(body) == 1 and _raises_not_implemented(body[0])


def _is_inert(node: ast.stmt) -> bool:
    """docstring、``...``、``pass`` 这类"什么都没做"的语句。"""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
        return False
    return node.value.value is Ellipsis or isinstance(node.value.value, str)


def _raises_not_implemented(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Name)
        and node.exc.id == "NotImplementedError"
    )


def _phantom_params(source: str) -> tuple[list[str], int]:
    """返回 ``(缺陷清单, 带参数文档的函数个数)``。"""
    tree = ast.parse(source)
    offenders: list[str] = []
    scanned = 0
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        args = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)]
        documented = _documented_params(fn) & set(args)
        if not documented:
            continue
        scanned += 1
        if _is_stub(fn):
            continue
        loaded = {
            n.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store)
        }
        for name in sorted(documented - loaded):
            offenders.append(f"{fn.name}(): 文档承诺入参 `{name}`，函数体从未读取它")
    return offenders, scanned


@pytest.fixture(scope="module")
def phantom_census() -> tuple[list[str], int]:
    offenders: list[str] = []
    scanned = 0
    for path in sorted(PKG.rglob("*.py")):
        found, count = _phantom_params(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(ROOT)}: {o}" for o in found]
        scanned += count
    return offenders, scanned


def test_no_documented_parameter_is_ignored_by_its_body(
    phantom_census: tuple[list[str], int],
) -> None:
    offenders, scanned = phantom_census
    assert scanned > 100, f"全包只扫到 {scanned} 个带参数文档的函数，判据自身失效"
    assert offenders == [], "\n".join(offenders)


def test_the_phantom_ruler_sees_a_planted_phantom() -> None:
    """正控：尺子必须认得它要抓的形状，否则"零缺陷"只是没在看。"""
    offenders, scanned = _phantom_params(
        '''
def good(a, *, b=1):
    """用掉两个入参。

    Parameters
    ----------
    a:
        用掉。
    b:
        也用掉。
    """
    return a + b


def bad(a, *, knob=False):
    """声称 knob 改变行为。

    Parameters
    ----------
    a:
        用掉。
    knob:
        没人读。
    """
    return a


def stub(a, *, also_unused=None):
    """协议桩。

    Parameters
    ----------
    a:
        未实现。
    also_unused:
        未实现。
    """
    raise NotImplementedError
''',
    )
    assert scanned == 3, scanned
    assert offenders == ["bad(): 文档承诺入参 `knob`，函数体从未读取它"], offenders


# --------------------------------------------------------------------------- #
# 判据二：自称线程安全的进程级惰性单例必须真的持锁
# --------------------------------------------------------------------------- #

_LOCK_EVIDENCE = re.compile(
    r"\.acquire\(\)|threading\.Lock\(|\bRLock\(|with\s+\w*lock\w*\b",
    re.I,
)


def _lazy_singleton_defects(source: str) -> list[str]:
    """模块级 ``_SHARED_*`` 容器上的 check-then-act，且函数自称线程安全。"""
    tree = ast.parse(source)
    shared = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for t in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(t, ast.Name) and t.id.startswith("_SHARED_")
    }
    offenders: list[str] = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        if "线程安全" not in (ast.get_docstring(fn) or ""):
            continue
        if not any(isinstance(n, ast.Name) and n.id in shared for n in ast.walk(fn)):
            continue
        seg = ast.get_source_segment(source, fn) or ""
        if not _LOCK_EVIDENCE.search(seg):
            offenders.append(f"{fn.name}(): 声称线程安全，但对进程级容器的取或建没有持锁")
    return offenders


@pytest.mark.parametrize(
    "rel",
    ["tstdx/web/_session_market.py", "tstdx/web/_base_http.py", "tstdx/transport/sniff.py"],
)
def test_process_level_singletons_that_claim_thread_safety_are_locked(rel: str) -> None:
    source = (ROOT / rel).read_text(encoding="utf-8")
    offenders = _lazy_singleton_defects(source)
    assert offenders == [], f"{rel}: {offenders}"


def test_the_lock_ruler_itself_sees_an_unlocked_singleton() -> None:
    """正控：把锁摘掉必须红，且带锁的那半必须绿。"""
    offenders = _lazy_singleton_defects(
        '''
import threading

_SHARED_THING: list = []
_LOCK = threading.Lock()


def shared_unlocked():
    """进程级共享对象（线程安全惰性单例）。"""
    if not _SHARED_THING:
        _SHARED_THING.append(object())
    return _SHARED_THING[0]


def shared_locked():
    """进程级共享对象（线程安全惰性单例）。"""
    with _LOCK:
        if not _SHARED_THING:
            _SHARED_THING.append(object())
        return _SHARED_THING[0]
''',
    )
    assert offenders == ["shared_unlocked(): 声称线程安全，但对进程级容器的取或建没有持锁"], (
        offenders
    )


def test_shared_http_builds_exactly_one_client_under_concurrency() -> None:
    """行为证据：并发首建只造一个 client，且所有线程拿到同一个对象。

    判据用 barrier 逼出交错：无锁实现下 N 个线程能同时穿过 ``if not _SHARED_HTTP``，
    barrier 凑齐 ⇒ 造出 N 个 client；持锁实现里后到的线程必须等，barrier 超时。
    """
    from tstdx.web import _session_market
    from tstdx.web import base as web_base

    threads = 4
    barrier = threading.Barrier(threads)
    built: list[object] = []

    def slow_build() -> object:
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=0.3)  # 持锁时后到者等不到同伴——正是期望的串行化
        obj = object()
        built.append(obj)
        return obj

    saved_list = list(_session_market._SHARED_HTTP)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(web_base, "build_client", slow_build)
    try:
        _session_market._SHARED_HTTP.clear()
        results: list[object] = []
        lock = threading.Lock()

        def worker() -> None:
            got = _session_market.shared_http()
            with lock:
                results.append(got)

        pool = [threading.Thread(target=worker) for _ in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join(timeout=5)
        assert len(results) == threads, "有线程被锁外卡死，未拿到返回值"
        # 断言的是**构造次数**，不是"大家拿到同一个对象"：无锁实现里四个调用者都会
        # 读到 _SHARED_HTTP[0]，身份检查照样通过，而多造出来的三个 client 连同各自的
        # keep-alive 连接池一起被 append 进列表，再没人关过。
        assert len(built) == 1, f"并发首建造出 {len(built)} 个 client（其余连同连接池泄漏）"
        assert len({id(r) for r in results}) == 1, f"并发首建造出 {len(built)} 个 client"
    finally:
        _session_market._SHARED_HTTP[:] = saved_list
        monkey.undo()


# --------------------------------------------------------------------------- #
# 判据三：写进 docstring 的计数必须等于被点名集合的真值
# --------------------------------------------------------------------------- #

#: ``共 14 个（见 :data:`GLOBAL_CODES`）`` 的形状：计数与真值源同时出现，这句才允许
#: 被机器复核。只写数字不写来源的句子不在射程内——那种要靠把数字删掉来变诚实。
_COUNT_CLAIM = re.compile(r"(\d+)\s*个[^。\n]{0,24}:data:`(\w+)`")


def _count_findings(module: str, source: str) -> tuple[list[str], int]:
    """返回 ``(缺陷清单, 可复核的计数声明条数)``。"""
    claims = _COUNT_CLAIM.findall(source)
    if not claims:
        return [], 0
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - 导不进来就不能复核，而"无法复核"不许读成绿
        return [f"{module}: 有 {len(claims)} 条计数声明，但模块导入失败（{exc}）无法复核"], 0
    findings: list[str] = []
    checked = 0
    for claimed, target in claims:
        value = getattr(mod, target, None)
        if value is None:
            findings.append(f"{module}: 引用 :data:`{target}`，但模块里没有这个名字")
            continue
        try:
            real = len(value)
        except TypeError:
            findings.append(f"{module}: :data:`{target}` 没有长度，不该出现在计数句式里")
            continue
        checked += 1
        if int(claimed) != real:
            findings.append(f"{module}: 写着 {claimed} 个，{target} 现算为 {real} 个")
    return findings, checked


def _module_dotted(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_documented_counts_match_the_named_collection() -> None:
    checked = 0
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if ":data:`" not in source:
            continue
        found, hits = _count_findings(_module_dotted(path), source)
        checked += hits
        offenders += found
    assert checked > 0, "一条可复核的计数声明都没扫到，判据在空转"
    assert offenders == [], "\n".join(offenders)


def test_the_count_ruler_sees_a_planted_drift(tmp_path: Path) -> None:
    """正控：集合加长而句子没跟着改，必须红；改对了必须绿。"""
    module = "planted_count_probe"
    wrong = tmp_path / f"{module}.py"
    wrong.write_text(
        '"""外盘品种。\n\n实测可用品种共 2 个（见 :data:`CODES`）。\n"""\n\n'
        "CODES = {'a': 1, 'b': 2, 'c': 3}\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(module, wrong)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[module] = mod
    try:
        findings, checked = _count_findings(module, wrong.read_text(encoding="utf-8"))
        assert checked == 1, checked
        assert findings == [f"{module}: 写着 2 个，CODES 现算为 3 个"], findings
    finally:
        del sys.modules[module]


def test_an_uncheckable_count_claim_is_a_defect_not_a_pass() -> None:
    """ "无法复核"不许读成绿：导不进模块时，带计数声明的文件本身就是要报的缺陷。

    少了这一格，一条写在拼错模块名 / 循环导入文件里的假计数会连同它的证据一起消失，
    门禁仍然绿——这正是本轮要根除的"读起来像断言、实则无法反驳"的形状。
    """
    source = "实测可用品种共 99 个（见 :data:`CODES`）。\n"
    findings, checked = _count_findings("tstdx.no_such_module_anywhere", source)
    assert checked == 0
    assert len(findings) == 1 and "无法复核" in findings[0], findings
    # 反面对照：没有计数声明的文件确实该静默通过，否则整包会被噪声淹没
    assert _count_findings("tstdx.no_such_module_anywhere", "一句话，没有指针。\n") == ([], 0)


# --------------------------------------------------------------------------- #
# 判据四：CLI 面注册的每个 --flag，处理链路必须真的读它（第 4 轮）
# --------------------------------------------------------------------------- #

#: 命令行命名空间对象的常见变量名——只有它们的取值算"读取了这个入参"。
_NS_NAMES = frozenset({"args", "ns", "namespace", "parsed", "opts"})
CLI_DIR = PKG / "cli"


def _dests_of(tree: ast.AST) -> dict[str, list[str]]:
    """``add_argument`` 注册出的 ``dest -> 字面 flag 列表``（含 ``dest=`` 显式命名）。"""
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        literals = [
            a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        longs = [x for x in literals if x.startswith("--")]
        dest = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "dest"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ),
            None,
        )
        if dest is None:
            if not longs:
                continue  # 位置参数没有"拧了没反应"这一格
            dest = longs[0].lstrip("-").replace("-", "_")
        found.setdefault(dest, []).extend(longs)
    return found


def _is_namespace(name: ast.expr) -> bool:
    """这个表达式是不是"命令行命名空间"对象（``args``/``ns``/``*_args``…）。"""
    if not isinstance(name, ast.Name):
        return False
    host = name.id.lower()
    return host in _NS_NAMES or host.endswith("args")


def _reads_of(tree: ast.AST) -> set[str]:
    """对命名空间对象的属性读取（``args.x`` 与 ``getattr(args, "x")``）。

    口径故意严格：注册语句里 ``dest="x"`` 是关键字常量、``"--x"`` 是位置常量，两者都
    不是属性访问，因此**注册自身不可能自证为被读取**——这是本判据与"文本里搜到名字
    就算数"的关键差别，后者会让每条新 flag 天然通过。
    """
    reads: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_namespace(node.value):
            reads.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_namespace(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            reads.add(node.args[1].value)
    return reads


def test_every_registered_cli_flag_is_read_by_the_runtime() -> None:
    """CLI 是 31 个命令的用户面，这里量的是"用户拧的每个开关都接通"。"""
    dests: dict[str, list[str]] = {}
    reads: set[str] = set()
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reads |= _reads_of(tree)
        if path.parent.name == "cli":
            for dest, flags in _dests_of(tree).items():
                dests.setdefault(dest, []).extend(flags)
    assert len(dests) >= 40, f"CLI 面只解析出 {len(dests)} 个入参，判据自身失明"
    unread = sorted(set(dests) - reads)
    assert not unread, "这些 --flag 被注册却无人读取（用户拧了个空开关）：" + ", ".join(
        f"{d}({', '.join(sorted(set(dests[d]))) or d})" for d in unread
    )


def test_the_cli_ruler_sees_a_planted_unread_flag() -> None:
    """正控：同一套代码必须认得"注册了但没人读"的形状，否则上一条的零缺陷没分量。"""
    tree = ast.parse(
        """
import argparse

p = argparse.ArgumentParser()
p.add_argument("--sentinel-never-read", type=int, help="注册了没人读")
p.add_argument("--explicit", dest="explicit_dest", help="下面会读")
args = p.parse_args()
print(args.explicit_dest)
getattr(args, "another_one", None)
""",
    )
    assert set(_dests_of(tree)) == {"sentinel_never_read", "explicit_dest"}, _dests_of(tree)
    assert _reads_of(tree) == {"explicit_dest", "another_one"}, _reads_of(tree)
    assert sorted(set(_dests_of(tree)) - _reads_of(tree)) == ["sentinel_never_read"]
