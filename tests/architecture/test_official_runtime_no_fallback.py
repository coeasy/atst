from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import Any, NoReturn

import pytest

from tstdx.query import EXECUTED_OPTIONS, REJECTED_OPTIONS, QuerySpec

ROOT = Path(__file__).resolve().parents[2]

#: v16 canonical runtime: Client -> QueryPlan -> zero-cache kernel plus the
#: canonical integration surfaces. Deleted legacy layers (v12 facade / service /
#: sources / cache) cannot appear here; the invariant is that the canonical
#: runtime never routes through an aggregate web fallback engine.
OFFICIAL_RUNTIME = [
    ROOT / "tstdx" / "client" / "api.py",
    ROOT / "tstdx" / "runtime" / "kernel.py",
    ROOT / "tstdx" / "query.py",
    ROOT / "tstdx" / "runtime" / "executor.py",
    ROOT / "tstdx" / "runtime" / "orchestration.py",
    ROOT / "tstdx" / "catalog" / "provider_bindings.py",
    ROOT / "tstdx" / "catalog" / "capability.py",
    ROOT / "tstdx" / "batch.py",
    ROOT / "tstdx" / "streaming" / "base.py",
    ROOT / "tstdx" / "providers" / "__init__.py",
    ROOT / "tstdx" / "integration" / "__init__.py",
    ROOT / "tstdx" / "integration" / "runtime_http.py",
    ROOT / "tstdx" / "integration" / "runtime_ws.py",
    ROOT / "tstdx" / "integration" / "runtime_ws_server.py",
    ROOT / "tstdx" / "integration" / "runtime_tasks.py",
    ROOT / "tstdx" / "integration" / "serialization.py",
    ROOT / "tstdx" / "integration" / "mcp" / "_server.py",
    ROOT / "tstdx" / "cli" / "__init__.py",
    ROOT / "tstdx" / "cli" / "runtime_commands.py",
]

#: The aggregate web router and its fallback-order literal must never be reachable
#: from the canonical runtime. ``WebQuoteSession`` (an exact Provider adapter) and
#: ``AllSourcesExhausted`` (an error type) are deliberately *not* forbidden: v15
#: reuses both, and only *aggregate routing* is the architecture violation.
FORBIDDEN_NAMES = {
    "WebQuoteClient",
    "DEFAULT_FALLBACK_ORDER",
    "SourceManager",
    "SourceRegistry",
}

#: Call-shaped tokens that only an aggregate web router would emit.
FORBIDDEN_CALLS = ("WebQuoteClient(", "web_session(")

#: QuerySpec fields whose only reader is a property inside the spec itself.
#: Each entry is re-verified by ``test_query_spec_store_only_fields_are_still_reached``.
_QUERY_SPEC_STORE_ONLY_FIELDS = frozenset({"options_json"})


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_official_runtime_inventory_points_at_real_files() -> None:
    """``OFFICIAL_RUNTIME`` 是手抄清单，所以它会与磁盘脱节——本步就是被这件事发现的。

    第 45 步按 F-18 裁决 (b) 删除 ``tstdx/providers/http.py`` 之前，这张清单里躺着它的
    一行，而三条判据都以 ``FileNotFoundError`` 崩在 ``read_text`` 里：读者看到的是
    "测试环境坏了"，不是"一份清单过期了"。清单里每一个死路径都等于把那格判据悄悄
    停摆，所以漂移必须先以一条人读的消息暴露，而不是以异常形式撞进来。
    """
    missing = [str(path.relative_to(ROOT)) for path in OFFICIAL_RUNTIME if not path.is_file()]
    assert missing == [], f"OFFICIAL_RUNTIME 指向不存在的文件，清单已与磁盘脱节：{missing}"
    assert len(OFFICIAL_RUNTIME) >= 15, (
        f"清单只剩 {len(OFFICIAL_RUNTIME)} 项，三条判据的覆盖面正在塌"
    )


def test_official_runtime_does_not_import_legacy_fallback_engine() -> None:
    offenders: list[str] = []
    for path in OFFICIAL_RUNTIME:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = sorted(_imported_names(tree) & FORBIDDEN_NAMES)
        if bad:
            offenders.append(f"{path.relative_to(ROOT)} -> {bad}")
    assert offenders == []


def test_official_runtime_has_no_cross_provider_fallback_literal() -> None:
    """Catch accidental reintroduction of the old fallback list/pipeline."""
    suspicious: list[str] = []
    patterns = (
        "tdx, web, reader, cache",
        "tdx -> web",
        "tdx->web",
        "fallback_to_web=true",
        "default_fallback_order",
    )
    for path in OFFICIAL_RUNTIME:
        text = path.read_text(encoding="utf-8").lower()
        for pattern in patterns:
            if pattern in text:
                suspicious.append(f"{path.relative_to(ROOT)}:{pattern}")
    assert suspicious == []


def test_official_runtime_never_calls_legacy_aggregate_web_client() -> None:
    """Exact Provider adapters may live in tstdx.web; aggregate routing may not."""
    suspicious: list[str] = []
    for path in OFFICIAL_RUNTIME:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_CALLS:
            if token in text:
                suspicious.append(f"{path.relative_to(ROOT)}:{token}")
    assert suspicious == []


def test_kernels_web_hop_uses_exactly_the_source_the_plan_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内核的 web 一跳必须恰好构造 `plan.provider` 一个源，且绝不碰有序降级客户端。

    上一条判据读的是字面量：`executor.py` 里没有 `WebQuoteClient(` 就通过。它看不见
    改道——`tstdx.web.get_quotes` 这个公开便捷函数在 `source` 缺省时会 new 一个
    `WebQuoteClient`，按 `web.enabled_sources` 顺序换源直到有一家成功，而调用它不需要
    任何被禁的字面量（第 48 步之前内核正是走这条路）。所以本判据用真实求值封住两条路：
    把那个多源客户端换成"一被构造就抛"，再拿 binding 表里每一个派发 `_web_quotes` 的
    Provider 各跑一跳，要求构造的源名 == `plan.provider`、符号归一化后原样送达。
    """
    import tstdx.web
    from tstdx.query import QueryPlanner, QuerySpec
    from tstdx.runtime.executor import DIRECT_BINDINGS, DirectProviderExecutor

    def _never_a_fallback_chain(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("内核的 web 一跳落进了有序多源降级链")

    hops: list[tuple[str, list[str]]] = []

    class _RecordingSource:
        def __init__(self, name: str) -> None:
            self._name = name

        def fetch(self, symbols: list[str]) -> list[Any]:
            hops.append((self._name, list(symbols)))
            return list(symbols)

        def close(self) -> None:
            return None

    def fake_create_source(name: str, **kwargs: Any) -> _RecordingSource:
        return _RecordingSource(name)

    monkeypatch.setattr(tstdx.web, "WebQuoteClient", _never_a_fallback_chain)
    monkeypatch.setattr(tstdx.web, "create_source", fake_create_source)

    web_quote_providers = sorted(
        {b.provider for b in DIRECT_BINDINGS if b.executor_name == "_web_quotes"}
    )
    assert web_quote_providers, "没有 binding 派发 _web_quotes，本判据的覆盖面塌成了空转"

    executor = DirectProviderExecutor(timeout=5.0)
    for provider in web_quote_providers:
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols=["sh600519", "600519"], provider=provider)
        )
        assert plan.provider == provider, f"规划器把 {provider} 换成了 {plan.provider}"
        executor._web_quotes(plan)

    assert hops == [(p, ["sh600519", "sh600519"]) for p in web_quote_providers], (
        f"web 一跳的取数源不唯一、不是 plan.provider 或符号没原样到达：{hops}"
    )


def test_package_defines_no_data_cache_layer() -> None:
    """「零缓存」是运行期事实，所以它必须可门禁，而不是只写在 README 与 docstring 里。

    ``CapitalChangeCache`` 是 Phase 2 删缓存层后留下的孤儿：它自带"命中即跳过
    0x0010 网络与解析"的 TTL + 落盘语义，却在 ``tstdx/`` 里没有任何调用方，
    只有它自己的单测在测它——一个能跳过数据源的形状留在包里，下次接线只需一行。
    纯函数记忆化（``functools.lru_cache``）不在此列：它不省掉任何一次网络请求。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = getattr(node, "name", None) or ""
            if "cache" not in name.lower():
                continue
            relative = str(path.relative_to(ROOT))
            is_function = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if isinstance(node, ast.ClassDef):
                offenders.append(f"{relative}:class {name}")
            elif is_function and name.startswith("get_"):
                offenders.append(f"{relative}:def {name}()")
    assert offenders == []


def test_pypi_description_claims_no_caching() -> None:
    """发布元数据是对外承诺：删掉缓存层后它仍写着 "semantic caching"。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^description\s*=\s*\"([^\"]*)\"", text, flags=re.M)
    assert match is not None, "pyproject 里没有可解析的 description，守卫自身失效"
    description = match.group(1)
    assert "cach" not in description.lower(), f"PyPI 描述仍在宣称缓存：{description}"


#: 允许出现在生产 docstring/注释里的缓存词根形状，逐条给出口径来源。
#: `max_age` 事件（F-43）的教训：代码删干净后，散文里的缓存口径还能再活几个月。
_PROSE_ALLOW_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?:^|\W)no\b[^\n]{0,60}cach", "英文否定句：no … cache"),
    (r"零缓存|zero-cache|不做结果缓存|不引入任何缓存", "对'无缓存'的否定陈述"),
    (r"不存在.{0,30}缓存|缓存.{0,12}不存在", "对'无缓存'的否定陈述"),
    (r"never.{0,30}cach|without cach|live(?:s|ing)? elsewhere", "明示'不在此处/不做'的英文句"),
    (r"cache_tier", "Provenance 上恒为 None 的证明字段"),
    (r"hq_cache|__pycache__", "外部程序/解释器的磁盘路径字面量"),
    (r"LRU cache|functools", "纯函数记忆化，不省掉任何一次数据请求"),
    (r"RankingStore|host:port keyed cache|disk cache", "主站排名持久化，非数据缓存"),
    (r"结果缓存", "tools/ 内的进程内解析复用"),
    (r"服务端缓存|上游缓存", "对端自己的缓存窗口，属外部事实描述"),
)

_PROSE_VOCAB = re.compile(r"cache|caching|缓存|single.?flight", re.I)


def _production_prose_hits() -> list[tuple[str, str]]:
    """Every cache-shaped phrase in production docstrings and comments."""

    hits: set[tuple[str, str]] = set()
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") and _PROSE_VOCAB.search(stripped):
                hits.add((relative, stripped))
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            doc = ast.get_docstring(node, clean=False)
            if not doc:
                continue
            hits.update(
                (relative, stripped)
                for raw in doc.splitlines()
                if (stripped := raw.strip()) and _PROSE_VOCAB.search(stripped)
            )
    return sorted(hits)


def test_production_prose_never_claims_a_data_cache() -> None:
    """「零缓存」要连散文一起门禁：缓存层删掉后，注释还在替它说话。

    第 18 步删的是代码形状；本门禁管的是 ``QueryFingerprint`` 曾写着
    "used by cache/single-flight layers"、``period.py`` 曾写着 "and cache lookup"
    ——读者照注释理解系统，注释指向不存在的层就是假事实。
    """
    hits = _production_prose_hits()
    assert hits, "扫描器一条都没命中，说明它自身失效了"
    offenders = [
        f"{relative}: {line}"
        for relative, line in hits
        if not any(re.search(pattern, line, re.I) for pattern, _ in _PROSE_ALLOW_PATTERNS)
    ]
    assert offenders == []


def test_pure_function_memoization_claim_is_true() -> None:
    """允许 "LRU cache" 这个词的唯一前提是：那个文件里真的挂着 ``lru_cache``。"""
    for relative, line in _production_prose_hits():
        if "LRU" not in line:
            continue
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "lru_cache" in source, f"{relative} 宣称 LRU 记忆化却没有 lru_cache"


def test_every_query_spec_field_is_consumed() -> None:
    """``QuerySpec`` 的每个字段都必须被执行面读到——否则它就是幻影开关。

    ``max_age`` 是缓存层的新鲜度上界，Phase 2 删缓存后它只剩五张入口签名：
    Client / CLI / HTTP / WS / MCP 都收，内核里零消费者，调用方设置它只会得到
    "已经生效"的错觉。本门禁不看具体字段名，只问"有没有人在 QuerySpec 之外读它"。
    """

    consumed = _query_spec_non_self_readers()
    assert any(consumed.values()), "字段消费扫描一条都没命中，说明它自身失效了"
    phantoms = sorted(
        name
        for name, files in consumed.items()
        if not files and name not in _QUERY_SPEC_STORE_ONLY_FIELDS
    )
    assert phantoms == [], f"QuerySpec 字段无人消费（幻影开关）：{phantoms}"


def test_query_spec_store_only_fields_are_still_reached() -> None:
    """豁免表不是免检通道：被豁免的字段必须真的经由属性袋进入执行面。

    ``options_json`` 是存储形态，执行面读的是解码后的 ``spec.options``；豁免它的前提
    是那条链仍在，属性一旦改名本门禁立刻红。
    """
    source = (ROOT / "tstdx" / "query.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    decoded = {
        node.attr
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "loads"
        for arg in call.args
        for node in ast.walk(arg)
        if isinstance(node, ast.Attribute)
    }
    for field in _QUERY_SPEC_STORE_ONLY_FIELDS:
        assert field in decoded, f"{field} 已不再被 json.loads 解码，豁免不再成立"
        assert f"self.{field}" in source, f"{field} 没有被任何属性读取"
    assert "plan.spec.options" in (ROOT / "tstdx" / "runtime" / "executor.py").read_text(
        encoding="utf-8"
    ), "options 袋不再是执行面输入"


def test_option_bag_keys_are_executed_or_rejected() -> None:
    """``options`` 袋不是 ``max_age`` 的备用入口：写进袋里的键必须被执行面读到或当场拒绝。

    字段侧由 ``test_every_query_spec_field_is_consumed`` 看住，而 v13 把
    ``allow_partial`` / ``allow_stale`` 从一等字段降级成袋里的字符串键，正好从那条判据
    下面走过——``allow_partial`` 的实际效果就是"quotes 上设了它什么也不改变"。
    判据覆盖两处写入点：``build()`` 内部的 ergonomic 折叠，与调用方在生产代码里
    直接传 ``options={...}`` 的字面量键（``client/api.py`` 的泛化调用面就走这条路）。
    第三段判据管的是**读取侧的账本**：``normalized()`` 按 ``EXECUTED_OPTIONS`` 白名单
    fail-closed，所以那份名单必须与实际读取点严格相等——名单里多一个键就是"接受却无人读"
    （正是本步要消灭的形状），少一个键则会当场拒掉一个真实生效的入参。
    """
    folded, flags = _build_option_folds()
    injected, build_sites = _injected_option_keys()
    executed = _executed_option_keys()
    assert executed, "执行面一个 option 键都没读到，说明消费扫描自身失效了"
    assert build_sites, "生产代码里一个 QuerySpec.build 调用点都没找到，说明调用点扫描自身失效"
    silent = sorted((folded | injected) - set(REJECTED_OPTIONS) - executed)
    assert silent == [], f"构造期折叠了无人消费的 option 键（幻影开关）：{silent}"
    dropped = sorted(flags - folded)
    assert dropped == [], f"build() 的 allow_* 形参没有落进袋里（收下即丢）：{dropped}"
    whitelist = set(EXECUTED_OPTIONS)
    assert whitelist == executed, (
        f"袋白名单与执行面实际读取点脱节：名单多 {sorted(whitelist - executed)}、"
        f"名单漏 {sorted(executed - whitelist)}"
    )
    assert whitelist.isdisjoint(REJECTED_OPTIONS), (
        f"同一个键既声明被执行面读取又被声明为策略禁用：{sorted(whitelist & set(REJECTED_OPTIONS))}"
    )


def _build_option_folds() -> tuple[set[str], set[str]]:
    """Keys ``QuerySpec.build`` folds into the bag, plus its ``allow_*`` parameters."""

    tree = ast.parse((ROOT / "tstdx" / "query.py").read_text(encoding="utf-8"))
    build = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build"
    )
    folded = {
        node.slice.value
        for node in ast.walk(build)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    }
    flags = {
        arg.arg
        for arg in [*build.args.args, *build.args.kwonlyargs]
        if arg.arg.startswith("allow_")
    }
    return folded, flags


def _injected_option_keys() -> tuple[set[str], int]:
    """Literal ``options=`` keys passed at production ``.build(...)`` call sites."""

    keys: set[str] = set()
    sites = 0
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "build":
                continue
            sites += 1
            for keyword in node.keywords:
                if keyword.arg != "options" or not isinstance(keyword.value, ast.Dict):
                    continue
                keys.update(
                    item.value
                    for item in keyword.value.keys
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    return keys, sites


def _executed_option_keys() -> set[str]:
    """Keys the execution surface actually reads out of an ``options`` mapping."""

    keys: set[str] = set()
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        if path.name == "query.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "get" or not node.args:
                    continue
                owner = node.func.value
                target = node.args[0]
            elif isinstance(node, ast.Subscript):
                owner, target = node.value, node.slice
            else:
                continue
            if "options" not in ast.unparse(owner).lower():
                continue
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                keys.add(target.value)
    return keys


def _query_spec_non_self_readers() -> dict[str, set[str]]:
    """Map each QuerySpec field to the production files that read it off an object."""

    readers = {field.name: set[str]() for field in dataclasses.fields(QuerySpec)}
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in readers:
                continue
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id != "self":
                readers[node.attr].add(path.name)
    return readers
