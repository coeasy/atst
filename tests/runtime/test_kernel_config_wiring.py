"""v17 Phase 6：配置面必须真实贯通到单一内核执行链（F-16）。

回归的是"写了 TOML 不生效"这一 P0 谎话：``atst.toml`` / 环境变量里的执行
参数必须原样出现在 ``Client()`` 的规划器与执行器上；显式构造参数优先于配置；
配置里的键必须一路到达传输层构造参数。

第 24 步把同一判据从"配置面"扩展到"计划面"：``deadline_ms`` 折进 ``plan.budget``
之后必须真的约束每一跳的超时（F-48），而分页只允许一份实现（F-49）。第 25 步把尺子
反过来量计划自己：``QueryPlan`` 的每个字段、``ExecutionBudget`` 的每个方法都必须有
读取点，注册表不得再声称无人执行的批量上限（F-50）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from atst import Client
from atst.config.loader import reset_config
from atst.config.schema import DEFAULT_CONFIG
from atst.runtime.executor import DirectProviderExecutor
from atst.transport.pool import pool_settings_from_config

_TOML = """
[core]
default_provider = "tencent"
timeout = 7.5
max_retries = 1
vipdoc_root = "D:/tdx/vipdoc"

[hosts]
servers = [["119.147.212.81", 443]]
slots_per_host = 6

[rate_limit]
continuous = 11

[security]
use_tls = true
"""


@pytest.fixture(autouse=True)
def _isolated_process_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个用例前后清空进程级配置，并隔离项目/用户/系统配置发现。"""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATST_CONFIG_FILE", raising=False)
    reset_config()
    yield
    reset_config()


def _write_project_config(tmp_path: Path) -> None:
    (tmp_path / "atst.toml").write_text(_TOML, encoding="utf-8")


def test_project_toml_drives_the_kernel() -> None:
    _write_project_config(Path.cwd())

    client = Client()
    try:
        assert client.runtime.planner.default_provider == "tencent"
        assert client.runtime.executor.timeout == 7.5
        assert client.runtime.executor.vipdoc_root == "D:/tdx/vipdoc"
        assert client.runtime.executor.hosts == [["119.147.212.81", 443]]
    finally:
        client.close()


def test_wired_config_reaches_the_transport_layer() -> None:
    _write_project_config(Path.cwd())

    client = Client()
    try:
        settings = pool_settings_from_config(client.runtime.config)
    finally:
        client.close()

    assert settings["max_retries"] == 1
    assert settings["slots_per_host"] == 6
    assert settings["use_tls"] is True
    assert settings["rate_limiter"].snapshot()["continuous"].rate == 11


def test_environment_variable_overrides_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project_config(Path.cwd())
    monkeypatch.setenv("ATST_CORE_TIMEOUT", "2.5")

    client = Client()
    try:
        assert client.runtime.executor.timeout == 2.5
    finally:
        client.close()


def test_explicit_constructor_arguments_win_over_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project_config(Path.cwd())
    monkeypatch.setenv("ATST_CONFIG_FILE", str(Path.cwd() / "atst.toml"))

    client = Client(timeout=1.25, default_provider="sina", vipdoc_root="E:/other")
    try:
        assert client.runtime.executor.timeout == 1.25
        assert client.runtime.planner.default_provider == "sina"
        assert client.runtime.executor.vipdoc_root == "E:/other"
    finally:
        client.close()


def test_executor_forwards_configured_pool_settings_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(hosts: Any = None, **kwargs: Any) -> object:
        captured["hosts"] = hosts
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("atst.client.TdxClient", fake_client)
    cfg = DEFAULT_CONFIG.with_overrides(
        hosts={"slots_per_host": 5},
        security={"use_tls": True},
    )

    DirectProviderExecutor(timeout=4.0, hosts=[["1.2.3.4", 7709]], config=cfg)._tdx_client(4.0)

    assert captured == {
        "hosts": [["1.2.3.4", 7709]],
        "timeout": 4.0,
        "heartbeat_interval": cfg.core.heartbeat_interval,
        "max_retries": cfg.core.max_retries,
        "slots_per_host": 5,
        "use_tls": True,
        "rate_limiter": captured["rate_limiter"],
    }


def test_injected_executor_keeps_the_kernel_config_for_provenance() -> None:
    """注入假执行面时不得触碰网络配置解析，但配置仍是内核的一部分。"""

    class _Fake:
        def execute(self, plan: Any) -> Any:  # pragma: no cover - 断言用不到
            raise AssertionError("not called")

    client = Client(executor=_Fake())
    try:
        assert client.runtime.config.core.timeout == DEFAULT_CONFIG.core.timeout
        assert client.runtime.executor is not None
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# ``deadline_ms`` 必须真的约束执行面（F-48）
#
# ``QueryPlanner`` 把 ``deadline_ms`` 折算成 ``plan.budget``，而折算完之后执行面
# 一次都没有读过它——与已删除的 ``max_age`` 同形的幻影旋钮，只是这次连"新鲜度"
# 的托词都没有。下面三条钉住：预算收紧时 socket 超时跟着收紧、默认路径逐字节
# 不变、预算已耗尽时在触网之前就失败。
# --------------------------------------------------------------------------- #


class _RecordingTdxClient:
    """假 ``TdxClient``：记录构造参数，不触网。"""

    instances: list[_RecordingTdxClient] = []

    def __init__(self, hosts: Any = None, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _RecordingTdxClient.instances.append(self)

    def __enter__(self) -> _RecordingTdxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def bars(self, symbol: str, **kwargs: Any) -> list[Any]:  # noqa: ARG002
        return []


@pytest.fixture
def fake_tdx_client(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingTdxClient]:
    _RecordingTdxClient.instances.clear()
    monkeypatch.setattr("atst.client.TdxClient", _RecordingTdxClient)
    return _RecordingTdxClient


def _bars_plan(deadline_ms: int) -> Any:
    from atst.query import QueryPlanner, QuerySpec

    return QueryPlanner().compile(
        QuerySpec.build("bars", symbols="sh600519", count=10, deadline_ms=deadline_ms)
    )


def test_tighter_deadline_bounds_the_socket_timeout(fake_tdx_client: Any) -> None:
    """调用方给 250ms 预算，传输层就不该拿到 30 秒 socket 超时。"""

    executor = DirectProviderExecutor(timeout=30.0, hosts=[["1.2.3.4", 7709]])
    executor._tdx_bars(_bars_plan(250))

    (client,) = fake_tdx_client.instances
    assert 0 < client.kwargs["timeout"] <= 0.25


def test_default_deadline_leaves_the_configured_timeout_intact(fake_tdx_client: Any) -> None:
    """默认 ``deadline_ms`` 与默认 ``core.timeout`` 同为 5 秒：取小即不变。"""

    executor = DirectProviderExecutor(timeout=5.0, hosts=[["1.2.3.4", 7709]])
    executor._tdx_bars(_bars_plan(5000))

    (client,) = fake_tdx_client.instances
    hop = client.kwargs["timeout"]
    #: 取小的另一侧是**单调时钟剩余量**，它必然比 5.0 少几微秒，逐位相等是把计时噪声
    #: 写成契约（该断言在 `524c687` 之后从未通过）。判据保持本步真正要钉的那件事：
    #: 默认预算下超时既没有被缩短到可观察的量级，也绝不超过配置值。
    assert 4.9 < hop <= 5.0


def test_web_route_also_gets_the_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算不只管 TDX 套接字：Web 直调面的 ``timeout`` 同样取小。

    五个 ``_web_*`` / ``_*_bars`` 直调执行器各自构造 HTTP 源，任何一处漏接都会让
    调用方的 deadline 在该 Provider 上失效——变异验证里这正是唯一逃过判据的一格。
    第 48 步把 quotes 一跳从公开便捷函数改成直接构造源，判据的对象也随之从"函数入参"
    变成"构造参数"；取小的那把尺子没变。
    """

    captured: list[dict[str, Any]] = []

    class _FakeSource:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.append(kwargs)

        def close(self) -> None:
            return None

        def fetch(self, symbols: Any) -> list[Any]:  # noqa: ARG002
            return []

        def fetch_bars(self, symbol: str, **kwargs: Any) -> list[Any]:  # noqa: ARG002
            return []

    import atst.web

    monkeypatch.setattr(atst.web, "create_source", _FakeSource)
    executor = DirectProviderExecutor(timeout=30.0)
    executor._tencent_bars(_bars_plan(250))
    executor._web_quotes(_bars_plan(250))  # quotes 走同一预算读数

    assert len(captured) == 2, f"两跳各该只构造一个源，实际 {len(captured)} 个：{captured}"
    for kwargs in captured:
        assert 0 < kwargs["timeout"] <= 0.25


def test_the_configured_timeout_is_bounded_in_exactly_one_place() -> None:
    """``self.timeout`` 只允许在 ``_hop_timeout`` 里被读——其余都是未取预算的跳。

    逐函数写断言永远追不上"新增一个直调执行器"（变异验证里漏掉的那一格正是这个），
    所以判据落在形状上：配置值在执行面上只有一个出口，就是那个 ``min()``。
    """

    import ast

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "atst" / "runtime" / "executor.py").read_text(encoding="utf-8"))

    def raw_reads(func: ast.FunctionDef) -> list[ast.Attribute]:
        return [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Attribute)
            and node.attr == "timeout"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Load)
        ]

    functions = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert any(node.name == "_hop_timeout" for node in functions), (
        "_hop_timeout 不见了，判据自身失效"
    )
    leaks = {
        func.name: len(raw_reads(func))
        for func in functions
        if func.name != "_hop_timeout" and raw_reads(func)
    }
    assert leaks == {}, f"这些跳把裸配置超时递给了传输层，deadline 预算对其无效：{leaks}"


def test_exhausted_deadline_fails_before_any_io(fake_tdx_client: Any) -> None:
    """预算已耗尽：当场 ReadTimeout，且一条连接都不许建立。"""

    from atst.errors import ReadTimeout

    plan = _bars_plan(50)
    plan.budget.deadline_ns = time.monotonic_ns() - 1
    executor = DirectProviderExecutor(timeout=30.0, hosts=[["1.2.3.4", 7709]])

    with pytest.raises(ReadTimeout, match="deadline 已耗尽"):
        executor._tdx_bars(plan)
    assert fake_tdx_client.instances == []


# --------------------------------------------------------------------------- #
# 字段侧判据的补集：读的人必须在执行面，而不是在规划器里（F-48）
# --------------------------------------------------------------------------- #

#: 不进执行面的字段与其**可核验**的理由；每条都由下面的反向核验撑着，
#: 理由失效即红——豁免表不是免检通道（``_QUERY_SPEC_STORE_ONLY_FIELDS`` 同形）。
#: ``currentness`` 曾在此列（"运行期校验器尚不存在"），第 41 步接线后它已由
#: ``atst/runtime/freshness.py`` 直接读取，豁免因此撤销：字段该受判据管。
_FIELD_EXEMPTIONS = {
    "options_json": "存储形态：执行面读的是解码后的 ``spec.options`` 袋",
    "schema_version": "契约版本戳：进 fingerprint 与 wire，不是执行输入",
}


def _execution_face_reads() -> tuple[dict[str, set[str]], set[str]]:
    """``QuerySpec`` 字段与 ``QueryPlan`` 字段在 ``atst/query.py`` **之外**的读取处。

    规划器自身的读取不算：它把值折进 plan/fingerprint 只是搬运，没人按这个值行动。
    """

    import ast
    import dataclasses

    from atst.query import QueryPlan, QuerySpec

    spec_fields = {item.name for item in dataclasses.fields(QuerySpec)}
    plan_fields = {item.name for item in dataclasses.fields(QueryPlan)}
    root = Path(__file__).resolve().parents[2]
    spec_reads: dict[str, set[str]] = {name: set() for name in spec_fields}
    plan_reads: set[str] = set()
    for path in sorted((root / "atst").rglob("*.py")):
        if path.name == "query.py":
            continue  # 规划器把值折进 plan/fingerprint 不等于有人按它行动
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            on_plan_obj = (isinstance(owner, ast.Name) and owner.id == "plan") or (
                isinstance(owner, ast.Attribute) and owner.attr in {"spec", "plan"}
            )
            if not on_plan_obj:
                continue
            if node.attr in spec_fields:
                spec_reads[node.attr].add(str(path.relative_to(root)))
            if node.attr in plan_fields:
                plan_reads.add(node.attr)
    return spec_reads, plan_reads


def _spec_field_carriers() -> dict[str, set[str]]:
    """``QueryPlanner`` 把哪些 spec 字段折进了哪个 ``QueryPlan`` 字段（取自 AST）。"""

    import ast

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "atst" / "query.py").read_text(encoding="utf-8"))
    construction = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "QueryPlan"
        ),
        None,
    )
    assert construction is not None, "query.py 里找不到 QueryPlan(...) 构造，判据自身失效"
    carriers: dict[str, set[str]] = {}
    for keyword in construction.keywords:
        folded = {
            sub.attr
            for sub in ast.walk(keyword.value)
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id in {"spec", "normalized"}
        }
        for field in folded:
            carriers.setdefault(field, set()).add(str(keyword.arg))
    return carriers


def test_every_query_spec_field_reaches_the_execution_face() -> None:
    """F-43 的门禁只看"有没有人读"，规划器自读也算——``deadline_ms`` 正因此漏网。

    补集判据：字段要么被执行面直接读到，要么被规划器折进某个 ``QueryPlan`` 字段、
    而那个字段被执行面读到（``deadline_ms`` → ``plan.budget`` 就是这条路）。折叠关系
    取自 ``QueryPlan(...)`` 构造处的 AST，不另抄一份字段清单。
    """

    spec_reads, plan_reads = _execution_face_reads()
    carriers = _spec_field_carriers()
    assert any(spec_reads.values()) or plan_reads, "执行面读取扫描一条都没命中，说明它自身失效了"
    phantoms = sorted(
        name
        for name, files in spec_reads.items()
        if not files
        and not (carriers.get(name, set()) & plan_reads)
        and name not in _FIELD_EXEMPTIONS
    )
    assert phantoms == [], f"QuerySpec 字段止步于规划器（幻影旋钮）：{phantoms}"


def test_deadline_ms_reaches_the_transport_layer() -> None:
    """``deadline_ms`` 的通路必须仍是 ``spec → plan.budget → 执行面``。

    只把字段列进豁免表就能骗过上一条判据，所以这里点名核验它走的确实是预算那条路。
    """

    spec_reads, plan_reads = _execution_face_reads()
    assert "deadline_ms" not in spec_reads or "budget" in plan_reads
    assert "budget" in _spec_field_carriers().get("deadline_ms", set())
    assert "budget" in plan_reads, "执行面不再读 plan.budget（deadline_ms 退回幻影旋钮）"


def test_query_spec_field_exemptions_still_hold() -> None:
    """每条豁免都要在源码里留着可核验的形状，否则豁免本身失效。

    统一规则：被豁免的字段必须是**数据身份/存储形态**——即被 ``QuerySpec.options``
    （袋的解码处）或 ``QueryPlanner._payload``（数据身份的分子）读到。哪天它连身份
    都不进了，说明它已彻底死亡，应当删除而不是继续豁免。
    """

    import ast

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "atst" / "query.py").read_text(encoding="utf-8"))
    assert _FIELD_EXEMPTIONS, "豁免表为空，本用例失去意义"

    def read_inside(target: str) -> set[str]:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == target:
                return {
                    sub.attr
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in {"self", "spec"}
                }
        raise AssertionError(f"query.py 里找不到 {target}()，豁免前提已失效")

    bag = read_inside("options")
    identity = read_inside("_payload")
    stale = sorted(name for name in _FIELD_EXEMPTIONS if name not in bag | identity)
    assert stale == [], f"豁免字段既不进 options 袋也不进数据身份，应删除而非豁免：{stale}"


# --------------------------------------------------------------------------- #
# 计划面：plan 上的每个字段、预算上的每个方法都必须有人按它行动（F-50）
# --------------------------------------------------------------------------- #


def test_every_query_plan_field_is_read_by_the_execution_face() -> None:
    """``QueryPlan`` 曾带着四个无人读取的字段：``deadline_ms``、``batch_limit``、
    ``live_channel``、``local_channel``。规划器把 channel 的事实另抄一份到 plan，抄本
    就成了第二真相源——而执行面从来按 ``plan.provider``/``plan.channel`` 查注册表。

    这是 ``test_every_query_spec_field_reaches_the_execution_face`` 的对偶：同一把 AST
    尺子，量的对象从入参换成计划。字段清单取自 dataclass 本身，不另抄一份。
    """

    import dataclasses

    from atst.query import QueryPlan

    _, plan_reads = _execution_face_reads()
    fields = {item.name for item in dataclasses.fields(QueryPlan)}
    assert plan_reads & fields, "计划面读取扫描一条都没命中，说明它自身失效了"
    orphans = sorted(fields - plan_reads)
    assert orphans == [], f"QueryPlan 字段止步于规划器（幻影副本）：{orphans}"


def test_execution_budget_has_no_unexercised_member() -> None:
    """第 24 步接上 deadline 时，同一对象另一侧的尝试记账从来没有一个调用点。

    ``begin_attempt()``/``max_attempts``/``attempts`` 比一个幻影入参更隐蔽：它带着锁
    和计数，读代码的人会以为"执行次数预算"是通的。判据落在形状上——每个公开实例方法
    都必须经由 ``budget`` / ``plan.budget`` 被执行面读到。构造子 ``from_deadline_ms``
    例外，它是 ``compile()`` 唯一的入口，正是"折进对象"的那一步本身。
    """

    import ast
    import inspect

    from atst.query import ExecutionBudget

    root = Path(__file__).resolve().parents[2]
    reads: set[str] = set()
    for path in sorted((root / "atst").rglob("*.py")):
        if path.name == "query.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            on_budget = (isinstance(owner, ast.Name) and owner.id == "budget") or (
                isinstance(owner, ast.Attribute) and owner.attr == "budget"
            )
            if on_budget:
                reads.add(node.attr)
    assert reads, "预算读取扫描一条都没命中，说明它自身失效了"

    methods = {
        name
        for name, member in inspect.getmembers(ExecutionBudget, callable)
        if not name.startswith("_") and getattr(member, "__self__", None) is not ExecutionBudget
    }
    assert "from_deadline_ms" not in methods, "from_deadline_ms 不再是类方法，例外前提失效"
    unexercised = sorted(methods - reads)
    assert unexercised == [], f"ExecutionBudget 成员无调用点（死掉的运行时状态）：{unexercised}"


# --------------------------------------------------------------------------- #
# 分页只有一份实现（F-49）
# --------------------------------------------------------------------------- #


def test_security_list_all_is_not_a_second_pagination_implementation() -> None:
    """``security_list_all`` 曾自带 ``while True`` 游标循环：无页数上限、空首页静默。

    它现在绑到 ``TdxClient.export_security_list``——页数上限、短页判据与空首页告警
    都只有那一处（F-45 的判据因此自动覆盖这条能力）。
    """

    import ast

    from atst.catalog.capability import binding_for

    meta = binding_for("tdx", "quotation", "security_list_all")
    assert (meta.backend, meta.method) == ("tdx_client", "export_security_list")

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "atst" / "runtime" / "executor.py").read_text(encoding="utf-8"))
    composed = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_composed_call"
    )
    loops = [node for node in ast.walk(composed) if isinstance(node, ast.While)]
    assert loops == [], "composed 面重新出现了游标分页循环（第二份实现回来了）"
