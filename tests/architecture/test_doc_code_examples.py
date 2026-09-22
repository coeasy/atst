"""活文档围栏代码块的调用级门禁（V18 第 15 轮）。

既有文档判据读的是**反引号里的点号链**与 CLI 示例，围栏 ```` ```python ```` 代码块
无人执行也无人解析——于是 ``docs/cookbook/04_streaming.md`` 能把 ``StreamEngine``
写成 ``queue_size=``（真名 ``max_queue``）、把 ``ReconnectPolicy`` 写成
``max_retries/backoff_base/backoff_max``（真名 ``base/cap/max_attempts``），
``docs/cookbook/01_bulk_kline.md`` 能 ``TdxClient(pool_size=4)``。照抄即 ``TypeError``，
而 14 轮门禁一路绿灯。这一格把这些块当代码对待：块里从 ``tstdx`` 导入的名字、
由 ``tstdx`` 构造出来的对象，其**属性存在性**与**入参形状**一律现读 ``inspect``。

与兄弟判据的分工：``test_doc_code_consistency`` 管 import 是否解析、点号链是否落位、
CLI 示例是否跑得起来；本文件管**已经解析得到的名字被怎么用**。
扫描面复用它的 ``active_docs()`` / ``fenced_code()``，不另抄一份名单——归档与 ADR
因此同样豁免。

射程之外的形状（刻意不做，避免把伪代码判成谎话）：
- 块内未绑定的名字（``bars = client.bars(...)`` 而该块没有 ``client = ...``）——
  没有类型来源就不猜；
- 形参里带 ``**kwargs`` 的转发口——签名层看不到下游，一律放行。唯一的例外是
  ``TdxClient`` / ``AsyncTdxClient`` 的 ``**pool_kwargs``：那条转发在
  :data:`_FORWARDED_KWARGS` 里点了名，入参形状按下游连接池的真签名判，配对关系由
  :func:`test_the_pool_forwarding_pairing_still_holds` 现读源码守住。
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import re
from pathlib import Path
from typing import Any

from tests.architecture.test_doc_code_consistency import ROOT, active_docs

TSTDX_ROOT = ROOT / "tstdx"

#: 围栏语言标记 → 按 python 处理。兄弟判据的 ``fenced_code()`` 只回代码体、拿不到
#: 语言标记，而这里必须区分 ````` python 与 ````` bash/yaml`````，故自开一个带语言的扫描器。
_FENCE = re.compile(r"^(?:```|~~~)([\w+-]*)[ \t]*\n(.*?)^\s*(?:```|~~~)[ \t]*$", re.S | re.M)


def _module_of(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.ImportFrom):
        return node.module
    return None


def _python_blocks(path: Path) -> list[tuple[int, str, ast.Module | None]]:
    """``[(块首行, 原文, 解析树)]``：只收声明为 python（或裸围栏）且含 tstdx 的块。"""
    text = path.read_text(encoding="utf-8")
    out: list[tuple[int, str, ast.Module | None]] = []
    for match in _FENCE.finditer(text):
        lang, body = match.group(1).lower(), match.group(2)
        if lang not in {"", "python", "py", "python3"}:
            continue
        if "tstdx" not in body:
            continue
        start = text[: match.start()].count("\n") + 2  # 跳过围栏行
        try:
            tree: ast.Module | None = ast.parse(body)
        except SyntaxError:
            tree = None  # 伪代码块：交给兄弟判据按行取 import
        out.append((start, body, tree))
    return out


def _resolve(module: str, attr: str | None = None) -> Any:
    obj: Any = importlib.import_module(module)
    if attr is not None:
        obj = getattr(obj, attr)
    return obj


class _Env:
    """块内可确定的 ``名字 -> 真对象`` 绑定。

    ``instances`` 记录"由构造调用绑出来的名字"（``client = TdxClient(...)``）：
    这类名字的方法访问是**绑定**的，签名里的 ``self`` 不是要文档填的位置参。
    """

    def __init__(self, tree: ast.Module) -> None:
        self.bindings: dict[str, Any] = {}
        self.instances: set[str] = set()
        self._collect(tree)

    def _assign_target(self, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name):
            return
        cls = self._called_class(value)
        if cls is not None:
            self.bindings[target.id] = cls
            self.instances.add(target.id)

    def _called_class(self, value: ast.expr) -> Any:
        """``x = Thing(...)`` / ``x = Thing.attr(...)`` 里能确定返回类型来源的构造。"""
        if not isinstance(value, ast.Call):
            return None
        func = value.func
        obj: Any = None
        if isinstance(func, ast.Name):
            obj = self.bindings.get(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = self.bindings.get(func.value.id)
            obj = getattr(base, func.attr, None) if base is not None else None
        return obj if isinstance(obj, type) else None

    def _collect(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("tstdx"):
                        continue
                    key = alias.asname or alias.name
                    try:
                        self.bindings[key] = importlib.import_module(alias.name)
                    except Exception:  # noqa: BLE001 - 兄弟判据负责报 import 假名
                        continue
            elif isinstance(node, ast.ImportFrom):
                module = _module_of(node)
                if module is None or not module.startswith("tstdx"):
                    continue
                for alias in node.names:
                    key = alias.asname or alias.name
                    try:
                        self.bindings[key] = _resolve(module, alias.name)
                    except Exception:  # noqa: BLE001
                        continue
            elif isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call) and len(node.targets) == 1:
                    self._assign_target(node.targets[0], node.value)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is not None:
                        self._assign_target(item.optional_vars, item.context_expr)


def _attr_chain(node: ast.Attribute, env: _Env) -> tuple[Any, str, int, str] | None:
    """``a.b.c`` 中 ``a`` 已在环境里时，返回 ``(父对象, 属性名, 行号, 根名字)``。"""
    base: ast.expr = node.value
    chain: list[str] = [node.attr]
    while isinstance(base, ast.Attribute):
        chain.append(base.attr)
        base = base.value
    if not isinstance(base, ast.Name):
        return None
    root = env.bindings.get(base.id)
    if root is None:
        return None
    obj = root
    for attr in reversed(chain[:-1]):
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj, node.attr, node.lineno, base.id


@functools.cache
def _violations() -> list[str]:
    rows: list[str] = []
    for path in active_docs():
        relative = path.relative_to(ROOT).as_posix()
        for start, _block, tree in _python_blocks(path):
            if tree is None:
                continue
            env = _Env(tree)
            if not env.bindings:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    found = _attr_chain(node, env)
                    if found is None:
                        continue
                    obj, attr, lineno, _ = found
                    if not hasattr(obj, attr):
                        rows.append(
                            f"{relative}:{start + lineno - 1} missing_attr "
                            f"{getattr(obj, '__qualname__', obj)!r} 没有 {attr!r}"
                        )
                elif isinstance(node, ast.Call):
                    rows.extend(_call_violations(relative, start, node, env))
    return sorted(set(rows))


_TSTDX_ROOT = TSTDX_ROOT
#: 客户端构造口的 ``**pool_kwargs`` 原样交给连接池，签名层看不见下游——于是
#: ``TdxClient(pool_size=4)``（真名 ``slots_per_host``）能从尺子下走过去，
#: 第 15 轮变异 M3 量的正是这一格（红=0）。这里把那一跳的下游点名，入参形状按
#: 下游真签名判；配对关系不靠注释维持，由下面的现读源码判据守着。
_FORWARDED_KWARGS: dict[str, str] = {
    "TdxClient": "tstdx.transport.pool.ConnectionPool",
    "AsyncTdxClient": "tstdx.transport.async_.AsyncConnectionPool",
}


@functools.cache
def _downstream_kwargs(qualname: str) -> frozenset[str] | None:
    """转发口下游的具名入参；``None`` = 这一格不判（下游自己也是开放签名）。"""
    target = _FORWARDED_KWARGS.get(qualname)
    if target is None:
        return None
    module, _, name = target.rpartition(".")
    try:
        sig = inspect.signature(_resolve(module, name))
    except (ImportError, AttributeError, TypeError, ValueError):
        return None  # 兄弟判据负责报不存在的名字
    params = [p for p in sig.parameters.values() if p.name != "self"]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return frozenset(p.name for p in params)


def _call_violations(relative: str, start: int, node: ast.Call, env: _Env) -> list[str]:
    func: Any = None
    bound_to_instance = False
    if isinstance(node.func, ast.Name):
        func = env.bindings.get(node.func.id)
    elif isinstance(node.func, ast.Attribute):
        found = _attr_chain(node.func, env)
        if found is not None:
            obj, attr, _, root = found
            func = getattr(obj, attr, None)
            bound_to_instance = root in env.instances
    if not callable(func) or isinstance(func, type) and func.__module__ == "builtins":
        return []
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):  # C 实现：只判存在性（上面已判）
        return []
    params = list(sig.parameters.values())
    if bound_to_instance and params and params[0].name in {"self", "cls"}:
        params = params[1:]  # 构造出来的对象：self 不是文档要填的位置参
    has_var_kwarg = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    names = {p.name for p in params}
    label = getattr(func, "__qualname__", func)
    downstream = _downstream_kwargs(label) if has_var_kwarg else None
    rows: list[str] = []
    if not has_var_kwarg or downstream is not None:
        allowed = names | (downstream or frozenset())
        for kw in node.keywords:
            if kw.arg is None or kw.arg in allowed:
                continue
            rows.append(
                f"{relative}:{start + kw.lineno - 1} phantom_kwarg "
                f"{label!r} 不接受 {kw.arg!r}（真实入参 {sorted(allowed)}）"
            )
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_var_positional = any(p.kind is p.VAR_POSITIONAL for p in params)
    supplied = {kw.arg for kw in node.keywords if kw.arg is not None}
    given = len(node.args)
    missing = [
        p.name
        for index, p in enumerate(positional)
        if p.default is p.empty and index >= given and p.name not in supplied
    ]
    where = f"{relative}:{start + node.lineno - 1}"
    if not has_var_positional and given > len(positional):
        rows.append(f"{where} arity {label!r} 最多收 {len(positional)} 个位置参，给了 {given} 个")
    elif missing:
        rows.append(f"{where} missing_arg {label!r} 的必填入参没有给到：{', '.join(missing)}")
    return rows


def test_live_doc_code_blocks_reference_real_api() -> None:
    """活文档 python 块里，从 tstdx 拿到的名字其属性与入参必须是真的。"""
    assert not _violations(), "活文档代码块引用了不存在的 API：\n" + "\n".join(_violations())


def test_gate_scans_a_meaningful_number_of_blocks() -> None:
    """自洁：扫描面不能空转——至少量到 20 个含 tstdx 绑定的代码块。"""
    scanned = 0
    for path in active_docs():
        for _start, _block, tree in _python_blocks(path):
            if tree is not None and _Env(tree).bindings:
                scanned += 1
    assert scanned >= 20, f"围栏代码块扫描面萎缩（只量到 {scanned} 个）"


#: 当年漏掉的那四类写法，原样塞回来当正控（模块 docstring 记着它们的出处）。
_PLANTED = """
from tstdx.streaming.engine import StreamEngine
from tstdx.streaming.base import ReconnectPolicy
from tstdx.client import TdxClient
engine = StreamEngine(queue_size=10)
policy = ReconnectPolicy(max_retries=3)
client = TdxClient()
wrong_pool = TdxClient(pool_size=4)
right_pool = TdxClient(slots_per_host=4)
client.nosuchmethod()
"""


def test_the_ruler_reports_the_calls_it_let_through_before() -> None:
    """正控：幻影 kwarg 与不存在的方法名必须各报一处，否则"零违规"是空转报出来的。

    刻意含 ``TdxClient()`` 与 ``TdxClient(slots_per_host=4)`` 两个合法构造作反面对照——
    判据若恒报，这里也会红。第三发幻影 kwarg 是 ``TdxClient(pool_size=4)``：它从
    ``**pool_kwargs`` 转发洞里走，正是第 15 轮变异 M3 量到的那一格。
    """
    tree = ast.parse(_PLANTED)
    env = _Env(tree)
    assert env.bindings, "正控块里连绑定都没建立，形状变了"
    rows: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            rows.extend(_call_violations("planted.md", 1, node, env))
        elif isinstance(node, ast.Attribute):
            found = _attr_chain(node, env)
            if found is not None and not hasattr(found[0], found[1]):
                rows.append(f"missing_attr {found[0]!r} 没有 {found[1]!r}")
    kinds = [row.split(maxsplit=1)[1].split()[0] for row in rows]
    assert "phantom_kwarg" in kinds and "missing_arg" in kinds, rows
    phantom = [row for row in rows if "phantom_kwarg" in row]
    assert len(phantom) == 3, rows  # queue_size、max_retries、pool_size；两处 TdxClient 构造都合法
    assert any("queue_size" in row for row in phantom), phantom
    assert any("max_retries" in row for row in phantom), phantom
    assert any("pool_size" in row for row in phantom), phantom
    assert not any("不接受 'slots_per_host'" in row for row in rows), rows
    assert any("nosuchmethod" in row for row in rows), rows
    assert any("missing_attr" in row and "TdxClient" in row for row in rows), rows


def test_the_pool_forwarding_pairing_still_holds() -> None:
    """配对哨兵：``**pool_kwargs`` 那一跳确实交给了 :data:`_FORWARDED_KWARGS` 点名的构造口。

    本文件唯一一条读源码文本的规则——它不重跑入参形状，只钉住配对关系本身：点名的
    下游构造口要真的出现在客户端源码里，且真的收到 ``**pool_kwargs``。下游一旦换成
    开放签名，:func:`_downstream_kwargs` 便回 ``None``、整条规则无声失效——那里不报的，
    这里必须报。
    """
    for qualname, target in _FORWARDED_KWARGS.items():
        callsite = target.rpartition(".")[2]
        origin = inspect.getsourcefile(_resolve("tstdx.client", qualname))
        assert origin is not None, f"{qualname} 的源码文件都定位不到，配对无从可验"
        source = Path(origin).resolve()
        relative = source.relative_to(ROOT).as_posix()
        text = source.read_text(encoding="utf-8")
        assert f"{callsite}(" in text, (
            f"{relative} 不再直接构造 {callsite} —— 转发已改向，"
            f"_FORWARDED_KWARGS[{qualname!r}] 得重指"
        )
        assert "**pool_kwargs" in text, f"{relative} 已不转发 **pool_kwargs，白名单该收回去"
        downstream = _downstream_kwargs(qualname)
        assert downstream is not None, (
            f"{qualname} 的转发洞又开了：{relative} 里 {callsite}( 与 **pool_kwargs 都在，"
            f"但下游 {target} 的签名被判成开放（_downstream_kwargs 回 None）——"
            "幻影 kwarg 从此隐身，配对必须重指到看得见形状的那一格"
        )
        assert "slots_per_host" in downstream, downstream
        assert "pool_size" not in downstream, downstream
