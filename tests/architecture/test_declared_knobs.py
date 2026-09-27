# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""声明出来的旋钮必须真的被拧动（V18 第 3–5 轮）。

五格同族形状，都是"文档/注册面对读者做了一个代码里没有的承诺"：

* ``Parameters`` 段点名一个入参，函数体从头到尾没读它——调用方拧了个空开关，
  比根本没有这个开关更糟（他以为已经关掉前缀归一了）；
* 进程级惰性单例自称"线程安全"，实际是无锁 check-then-act；
* "共 N 个（见 :data:`X`）"里的 N 与被点名集合的真值不符；
* CLI 面 ``add_argument`` 注册一个 ``--flag``，处理链路里没有人读它的 dest（第 4 轮）；
* wire 面（HTTP 路由签名、MCP ``inputSchema``）公开声明一个字段，处理函数却不读它（第 5 轮）。

五者都能用 AST + 现算真值判掉，因此这里是**派生**判据而不是抄来的名单。
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import inspect
import re
import sys
import textwrap
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "atst"

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
    ["atst/web/_session_market.py", "atst/web/_base_http.py", "atst/transport/sniff.py"],
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
    from atst.web import _session_market
    from atst.web import base as web_base

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
    findings, checked = _count_findings("atst.no_such_module_anywhere", source)
    assert checked == 0
    assert len(findings) == 1 and "无法复核" in findings[0], findings
    # 反面对照：没有计数声明的文件确实该静默通过，否则整包会被噪声淹没
    assert _count_findings("atst.no_such_module_anywhere", "一句话，没有指针。\n") == ([], 0)


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


# --------------------------------------------------------------------------- #
# 判据五：wire 面上声明的入参，处理函数必须读它（第 5 轮）
# --------------------------------------------------------------------------- #

#: HTTP 路由与 MCP 请求 schema 的共同形状：**声明即被接受**。FastAPI 把路由签名的形参公开成
#: 查询串参数，``wire_fields`` 又拿同一份签名 / 同一份 ``inputSchema`` 当拒绝白名单——于是加进
#: 签名（或 schema）却无人读的字段能顺利通过闸口，调用方那边就成了"我传了，它没理我"。比 CLI
#: 那一格更隐蔽：CLI 的 ``--help`` 至少还在手边，wire 面上它看起来就是一个生效的开关。
#:
#: 与 ``tests/runtime/test_wire_declared_fields.py``（F-47）的分工：那一份管**反方向**——
#: 请求里出现了未声明的字段必须当场被拒，并核对 body/params 白名单与分派读取点的差；
#: 本格管"声明了的字段有没有真的被读"。两面合起来才是那句"要么被读走，要么被拒"。
HTTP_MODULE = PKG / "integration" / "runtime_http.py"
_ROUTE_DECOS = ("app.get(", "app.post(", "app.put(", "app.delete(")
#: 只有这些变量名上的字符串取值算"读了请求体的这个键"。理由与判据四相同：声明侧的
#: ``_str_prop("...")`` 与 ``{"type": "string"}`` 都不是这种形状，注册无法自证为被读取。
_BODY_DICT_NAMES = frozenset({"args", "params", "payload", "data", "options"})

_PLANTED_HTTP_SOURCE = """
def create_app():
    @app.get("/v13/sentinel")
    def sentinel_route(symbol: str, sentinel_never_read: str = "", period: str = "day"):
        return {"symbol": symbol, "period": period}

    return app
"""


def _route_handlers(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """被 ``app.get/post/put/delete`` 装饰的那几个函数（FastAPI 路由）。"""
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decos = [ast.unparse(d) for d in node.decorator_list]
        if any(d.startswith(_ROUTE_DECOS) for d in decos):
            out.append(node)
    return out


def _params_of(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [a.arg for a in fn.args.args if a.arg not in {"self", "cls"}]


def _body_reads(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """函数体里以**加载**方式出现的名字——形参被用过才算被读。"""
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _http_scan(source: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """源码 -> (每个路由的形参集, 每个路由的读取集)。"""
    declared: dict[str, set[str]] = {}
    reads: dict[str, set[str]] = {}
    for fn in _route_handlers(ast.parse(source)):
        declared[fn.name] = set(_params_of(fn))
        reads[fn.name] = _body_reads(fn)
    return declared, reads


def _dict_keys(fn) -> set[str]:
    """handler 函数体里 ``<请求体字典>.get("K")`` / ``<请求体字典>["K"]`` 的键集合。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        const: ast.Constant | None = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id.lower() in _BODY_DICT_NAMES
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            const = node.args[0]
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id.lower() in _BODY_DICT_NAMES
            and isinstance(node.slice, ast.Constant)
        ):
            const = node.slice
        if const is not None and isinstance(const.value, str):
            keys.add(const.value)
    return keys


def _wire_unread(declared: dict[str, set[str]], reads: dict[str, set[str]]) -> list[str]:
    """两面共用的谓词：声明了却没进读取集的 ``位置.字段``。"""
    return sorted(
        f"{where}.{field}"
        for where, fields in declared.items()
        for field in fields
        if field not in reads.get(where, set())
    )


def test_every_http_route_param_is_read_by_the_handler() -> None:
    """HTTP 是对外 10 格路由的用户面：签名里每个形参都会被公开成查询参数，必须有人读。"""
    declared, reads = _http_scan(HTTP_MODULE.read_text(encoding="utf-8"))
    total = sum(len(v) for v in declared.values())
    assert total >= 20, f"HTTP 面只解析出 {total} 个路由形参，判据自身失明"
    unread = _wire_unread(declared, reads)
    assert not unread, "这些路由形参被公开声明却无人读取（传了等于没传）：" + ", ".join(unread)


def test_every_mcp_declared_property_is_read_by_its_handler() -> None:
    """MCP 的 ``inputSchema`` 既对外声明又是拒绝白名单：声明的属性必须真的进执行链路。"""
    from atst.integration.mcp._tools_spec import TOOLS

    assert len(TOOLS) >= 5, f"MCP 工具清单只读到 {len(TOOLS)} 张，判据自身失明"
    declared = {t.name: set(t.inputSchema.get("properties", {})) for t in TOOLS}
    reads = {t.name: _dict_keys(t.handler) for t in TOOLS}
    total = sum(len(v) for v in declared.values())
    assert total >= 20, f"MCP 面只解析出 {total} 个声明属性，判据自身失明"
    unread = _wire_unread(declared, reads)
    assert not unread, "这些 MCP 属性被 schema 声明却无人读取：" + ", ".join(unread)


def test_the_wire_ruler_sees_planted_unread_fields() -> None:
    """正控：两面各自塞一个"声明了没人读"的字段，谓词必须当场点名它。"""
    declared, reads = _http_scan(_PLANTED_HTTP_SOURCE)
    assert declared == {"sentinel_route": {"symbol", "sentinel_never_read", "period"}}, declared
    assert _wire_unread(declared, reads) == ["sentinel_route.sentinel_never_read"]
    # MCP 侧同一谓词：只报没读的那个，读了的一个都不许报
    assert _wire_unread(
        {"get_bars": {"symbol", "period", "sentinel_never_read"}},
        {"get_bars": {"symbol", "period"}},
    ) == ["get_bars.sentinel_never_read"]
    # 反面对照：全读时谓词必须为空，否则"零缺陷"是恒报而不是看出来的
    assert _wire_unread({"get_bars": {"symbol"}}, {"get_bars": {"symbol"}}) == []


# --------------------------------------------------------------------------- #
# 判据六：同一个旋钮，每张面递给解析器的值必须解得开、且解成同一个答案（第 13 轮）
# --------------------------------------------------------------------------- #

#: 2026-09-22（周二，A 股上午盘中）实测：`Client.security_count(market=0)` 给出 24296，
#: 而 `atst security-count`（`--market` 声明成字符串、缺省 `"0"`）与
#: `GET /v13/security/count`（路由签名 `market: str = "0"`）当场
#: `E3040 未知标准市场 '0'；可选 sz/sh/bj 或 0/1/2`——**消息点名的写法正是代码拒绝的写法**，
#: 三张面连自己声明的缺省值都解不开。前五格判据全都看不见它：字段被声明了（判据三）、
#: 也被读走了（判据五），读它的那个函数却只认三种前缀。"声明与执行闭合"缺的正是这一维：
#: **声明的形状必须落在解析器真的接受的值域里**。

CLI_MODULE = PKG / "cli" / "parser.py"
WS_MODULE = PKG / "integration" / "runtime_ws.py"
#: 形状上就不该被解开的写法（与值域无关，所以市场表怎么长它们都还是死的）。
_SHAPE_DEAD_MARKET_TEXTS = ("", " ", "00", "0x0", "sh1", "市场", "true", "-1", "+1", "1.0")


def _unacceptable_market_values() -> list[object]:
    """该被拒的市场写法：形状死的若干种，加上当下值域之外的两种写法。

    越界值是**算出来**的而不是抄来的：抄一个 `3` 进名单，等市场表真长出 id 3 时这条
    判据就会把合法值当成缺陷。
    """
    from atst.client.core import _PREFIX_MARKET

    beyond = max(_PREFIX_MARKET.values()) + 1
    return [*_SHAPE_DEAD_MARKET_TEXTS, str(beyond), beyond]


def _handed_value(type_text: str, default_node: ast.expr) -> object | None:
    """一张面**实际递下去**的那个值：声明了 ``type=int``（或注解 ``int``）就先按它过一遍。"""
    if not isinstance(default_node, ast.Constant):
        return None
    value = default_node.value
    caster = {"int": int, "str": str, "float": float, "bool": bool}.get(type_text)
    return caster(value) if caster is not None else value


def _cli_market_values(source: str) -> dict[str, object]:
    """CLI 面：每个 ``market`` 入参声明 -> 该面会递下去的那个值。"""
    out: dict[str, object] = {}
    for node in ast.walk(ast.parse(source)):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "add_argument"
            or not node.args
        ):
            continue
        flag = node.args[0]
        if not (isinstance(flag, ast.Constant) and isinstance(flag.value, str)):
            continue
        if flag.value.lstrip("-") != "market":
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if "default" not in keywords:
            continue
        type_node = keywords.get("type")
        value = _handed_value(
            type_node.id if isinstance(type_node, ast.Name) else "", keywords["default"]
        )
        if value is not None:
            out[f"cli:{flag.value}@{node.lineno}"] = value
    return out


def _arg_defaults(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, tuple[str, ast.expr]]:
    """形参名 -> (注解文本, 缺省表达式)；FastAPI 按注解把查询串转成该类型。"""
    defaults = fn.args.defaults or []
    positional = [a for a in fn.args.args if a.arg not in {"self", "cls"}]
    offset = len(positional) - len(defaults)
    return {
        arg.arg: (ast.unparse(arg.annotation) if arg.annotation is not None else "", default)
        for arg, default in zip(positional[offset:], defaults, strict=True)
    }


def _http_market_values(source: str) -> dict[str, object]:
    """HTTP 面：路由签名里 ``market`` 的缺省，就是该面收不到参数时递下去的值。"""
    out: dict[str, object] = {}
    for fn in _route_handlers(ast.parse(source)):
        for name, (annotation, default) in _arg_defaults(fn).items():
            if name != "market":
                continue
            value = _handed_value(annotation, default)
            if value is not None:
                out[f"http:{fn.name}@{fn.lineno}"] = value
    return out


def _ws_market_values(source: str) -> dict[str, object]:
    """WS 面：``params.get("market", 缺省)`` 里那个缺省，是该面收不到键时递下去的值。"""
    out: dict[str, object] = {}
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "market"
        ):
            value = _handed_value("", node.args[1])
            if value is not None:
                out[f"ws:default@{node.lineno}"] = value
    return out


def _library_market_values() -> dict[str, object]:
    """库面：``Client`` 上那两个公开方法的声明缺省。"""
    from inspect import signature

    from atst import Client

    out: dict[str, object] = {}
    for name in ("security_count", "security_list"):
        parameter = signature(getattr(Client, name)).parameters.get("market")
        if parameter is not None and parameter.default is not inspect.Parameter.empty:
            out[f"library:{name}"] = parameter.default
    return out


def _market_face_values() -> dict[str, object]:
    """四张泛型面加库面，在 ``market`` 这个旋钮上各自会递下去的那个值。"""
    out: dict[str, object] = {}
    out.update(_cli_market_values(CLI_MODULE.read_text(encoding="utf-8")))
    out.update(_http_market_values(HTTP_MODULE.read_text(encoding="utf-8")))
    out.update(_ws_market_values(WS_MODULE.read_text(encoding="utf-8")))
    out.update(_library_market_values())
    return out


def _mcp_market_schemas() -> dict[str, str]:
    """MCP 面：每张工具的 ``inputSchema`` 里 ``market`` 声明的 JSON 类型。"""
    from atst.integration.mcp._tools_spec import TOOLS

    out: dict[str, str] = {}
    for tool in TOOLS:
        schema = tool.inputSchema.get("properties", {}).get("market")
        if isinstance(schema, dict) and isinstance(schema.get("type"), str):
            out[tool.name] = schema["type"]
    return out


def test_every_market_face_hands_the_parser_a_value_it_accepts() -> None:
    """同一份声明表派生出来的面，不许在同一个旋钮上给出四种不同下场。"""
    from atst.client.core import _standard_market_id

    sites = _market_face_values()
    assert len(sites) >= 6, f"市场旋钮只解析出 {len(sites)} 处声明，判据自身失明：{sorted(sites)}"
    answers: dict[object, list[str]] = {}
    for label, value in sorted(sites.items()):
        try:
            decoded = _standard_market_id(value)
        except Exception as exc:  # noqa: BLE001 - 要点名是哪张面解不开
            raise AssertionError(
                f"{label} 递给市场解析器的 {value!r} 解不开（{type(exc).__name__}: {exc}）"
            ) from exc
        answers.setdefault(decoded, []).append(f"{label}={value!r}")
    joined = " | ".join(f"{key}: {sites_}" for key, sites_ in sorted(answers.items()))
    assert len(answers) == 1, f"同一个缺省市场，各张面解成了不同 id：{joined}"


def test_a_string_declared_market_face_can_name_every_market() -> None:
    """凡把这个旋钮声明成字符串的面，都必须能表达每一个市场——否则它只在拒自己的用户。"""
    from atst.client.core import _PREFIX_MARKET, _standard_market_id

    string_sites = sorted(
        label for label, value in _market_face_values().items() if isinstance(value, str)
    )
    assert string_sites, "没有任何面把 market 声明成字符串，判据自身失明"
    for market_id in sorted(set(_PREFIX_MARKET.values())):
        text = str(market_id)
        assert _standard_market_id(text) == market_id, (
            f"{string_sites} 把这些旋钮声明成字符串，{text!r} 是它们能写出的数字形式，"
            "而解析器拒了它"
        )
        #: 数字写法与 int 写法必须同答案：上下限一旦是从表里抄的而不是派生的，
        #: 就会长出"文本能解、整数不能解"这种半死不活的市场。
        assert _standard_market_id(market_id) == market_id, (
            f"市场 {market_id} 的 int 写法被拒了，而它的文本写法 {text!r} 能解开"
        )


def test_every_string_typed_market_tool_can_name_every_market() -> None:
    """MCP 的 schema 声明 ``string`` 就是"按字符串给我"的承诺：那数字写法必须是活的。"""
    from atst.client.core import _PREFIX_MARKET, _standard_market_id

    schemas = _mcp_market_schemas()
    assert schemas, "MCP 面没有解析出任何 market 声明，判据自身失明"
    for tool, json_type in sorted(schemas.items()):
        if json_type != "string":
            continue
        for market_id in sorted(set(_PREFIX_MARKET.values())):
            assert _standard_market_id(str(market_id)) == market_id, (
                f"MCP 工具 {tool} 把 market 声明成 string，数字写法 {str(market_id)!r} 却是死的"
            )


def test_the_market_message_only_advertises_what_the_parser_accepts() -> None:
    """那条消息点名了两组词，两词都必须真能解开——否则它在教用户走一条死路。"""
    from atst.client.core import _PREFIX_MARKET, _standard_market_id
    from atst.errors import ValidationError

    with pytest.raises(ValidationError) as raised:
        _standard_market_id("这一格故意不存在")
    message = raised.value.message
    advertised = re.search(r"可选 (\S+) 或 (\S+)", message)
    assert advertised, f"消息不再点名可选值，本判据就看不到值域了：{message!r}"
    prefixes, digits = (set(group.split("/")) for group in advertised.groups())
    assert prefixes == set(_PREFIX_MARKET), f"消息点名的前缀与表不符：{sorted(prefixes)}"
    assert digits == {str(value) for value in _PREFIX_MARKET.values()}, (
        f"消息点名的数字写法与表不符：{sorted(digits)}"
    )
    for text, expected in _PREFIX_MARKET.items():
        assert _standard_market_id(text) == expected
    for text in digits:
        assert _standard_market_id(text) == int(text)
    values = _unacceptable_market_values()
    assert values, "越界值算不出来，本判据的负例那一半是空的"
    for value in values:
        try:
            _standard_market_id(value)
        except ValidationError:
            continue
        raise AssertionError(f"{value!r} 本该被市场解析器拒掉，却解开了")


def test_the_market_ruler_sees_a_planted_face_value() -> None:
    """正控：三张面的声明形状都要解析得出来，坏值要被认出、好值不许误报。"""
    planted_http = (
        'def create_app():\n    @app.get("/v13/sentinel")\n'
        '    def sentinel(market: str = "nope"):\n        return market\n\n    return app\n'
    )
    assert _http_market_values(planted_http) == {"http:sentinel@3": "nope"}
    planted_cli = (
        'p.add_argument("--market", default="0")\n'
        'p.add_argument("--market", type=int, default=0)\n'
        'p.add_argument("--market")\n'
    )
    assert _cli_market_values(planted_cli) == {"cli:--market@1": "0", "cli:--market@2": 0}
    assert _ws_market_values('market = params.get("market", 0)\n') == {"ws:default@1": 0}
    from atst.client.core import _standard_market_id

    assert {_standard_market_id(value) for value in ("0", "sz", "1", "sh", 2, "bj")} == {0, 1, 2}


# --------------------------------------------------------------------------- #
# 判据六：抄袭审计那份"预期外部依赖"名单必须等于真实 import 的外部根（第 23 轮）
# --------------------------------------------------------------------------- #


def _external_import_roots(package: Path) -> set[str]:
    """``package`` 子树里真实 import 到的外部顶层包名。

    口径与 :func:`atst.tools.check_originality._analyze_patterns` 同一层：AST 遍历、
    相对 import 跳过、顶层名 = 点号前第一段；这里额外滤掉标准库与被审包自身，
    剩下的才该进"预期外部依赖"白名单。
    """
    roots: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                root = name.partition(".")[0]
                if root and root not in sys.stdlib_module_names and root != package.name:
                    roots.add(root)
    return roots


def test_originality_external_import_whitelist_matches_reality() -> None:
    """白名单两头都要对账：多出来的是幻影声明，缺进去的是无人登记的真实依赖。

    第 23 轮清幻影 extra 时量的就是这两头：``pydantic`` / ``mcp`` 全仓 0 处 import，
    而 ``websockets`` / ``zstandard`` / ``tomli`` / ``typing_extensions`` 四处真实
    import 从未登记——于是 ``check_originality`` 的 ``unknown external import`` 普查
    长期挂着 5 条噪声，而它恰恰是"发现第六个外部依赖"的那只手。
    """
    from atst.tools.check_originality import KNOWN_EXTERNAL_IMPORTS

    real = _external_import_roots(PKG)
    assert real, "外部依赖普查为空，判据本身失效"
    assert set(KNOWN_EXTERNAL_IMPORTS) == real, (
        "atst/tools/check_originality.py 的预期外部依赖与 atst/ 的真实 import 分叉："
        f"多 {sorted(set(KNOWN_EXTERNAL_IMPORTS) - real)}"
        f" 缺 {sorted(real - set(KNOWN_EXTERNAL_IMPORTS))}"
    )


def test_the_external_import_ruler_sees_planted_roots(tmp_path: Path) -> None:
    """正控：普查必须认出植入的外部根，且不许把标准库/自身包算进去。"""
    pkg = tmp_path / "plantedpkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("import json\nfrom .sub import a\n", encoding="utf-8")
    (pkg / "sub.py").write_text(
        "import numpy.linalg\nfrom sqlalchemy import Engine\nimport plantedpkg.other\n",
        encoding="utf-8",
    )
    assert _external_import_roots(pkg) == {"numpy", "sqlalchemy"}
