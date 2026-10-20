# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``provider='auto'`` 自动选源（能力面 + 运行时健康反馈）。

provider-first 不变量：一次请求绑定一个 Provider，跨源只在**显式**策略下发生。
本文件锁死这条策略的四条边界：

1. ``auto`` 是策略名，不是 Provider——别名解析不适用，``prefer`` 里放它要报错；
2. 候选顺序 = operational 能力面 + ``routing.auto_providers`` 偏好 + 健康反馈沉底；
3. 健康反馈只由真实请求结果驱动（成功清零 / 失败记时），TTL 过后自动复位；
4. 每个结果仍带 Provenance（``requested_provider='auto'`` + ``fallback``），
   钉死 Provider 的请求永远不经过这里。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atst.client.api import Client
from atst.config.schema import Config, RoutingConfig
from atst.domain.models import Quote
from atst.errors import AllSourcesExhausted, ValidationError
from atst.providers import PROVIDERS
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.runtime import autoselect
from atst.runtime.autoselect import (
    AUTO_PROVIDER,
    ProviderHealthStore,
    auto_candidates,
    is_auto_provider,
    provider_health,
    resolve_auto_provider,
)
from atst.runtime.kernel import UnifiedRuntime
from atst.runtime.orchestration import AutoPolicy, ProviderOrchestrator

QUOTE_CANDIDATES = PROVIDERS.available_providers("quotes")


@pytest.fixture(autouse=True)
def clean_health() -> None:
    """进程级健康存储是单例：每个用例前后都清空，避免顺序耦合。"""

    provider_health().clear()
    yield
    provider_health().clear()


def _result(provider: str) -> QueryResult[list[Quote]]:
    plan = QueryPlanner().compile(
        QuerySpec.build("quotes", symbols="sh600519", provider=provider, currentness="live")
    )
    return QueryResult.from_plan(
        [Quote(code="600519", price=1.0)], plan=plan, provenance=Provenance.direct(plan)
    )


# --------------------------------------------------------------------------- #
# 策略名识别
# --------------------------------------------------------------------------- #
class TestIsAutoProvider:
    @pytest.mark.parametrize("value", ["auto", "AUTO", " Auto ", "aUtO"])
    def test_recognizes_the_strategy_name(self, value: str) -> None:
        assert is_auto_provider(value) is True

    @pytest.mark.parametrize("value", [None, "", "tdx", "autox", "au"])
    def test_everything_else_is_a_concrete_provider(self, value: str | None) -> None:
        assert is_auto_provider(value) is False


# --------------------------------------------------------------------------- #
# 健康反馈（TTL 缓存）
# --------------------------------------------------------------------------- #
class TestProviderHealthStore:
    def test_failure_degrades_until_ttl_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = ProviderHealthStore()
        clock = {"now": 1_000}
        monkeypatch.setattr(autoselect.time, "monotonic_ns", lambda: clock["now"])

        store.record_failure("tdx", code="E2040")
        assert store.is_degraded("tdx", ttl_ns=100) is True
        # 窗口内换个大小写也一样：key 走 normalize_provider_id
        assert store.is_degraded("TDX", ttl_ns=100) is True

        clock["now"] = 1_000 + 101
        assert store.is_degraded("tdx", ttl_ns=100) is False
        # 过期即复位，不是"读的时候算一次但留着标记"
        assert store.snapshot() == {}

    def test_ttl_zero_disables_feedback(self) -> None:
        store = ProviderHealthStore()
        store.record_failure("tdx", code="E2040")
        assert store.is_degraded("tdx", ttl_ns=0) is False

    def test_success_clears_the_failure(self) -> None:
        store = ProviderHealthStore()
        store.record_failure("tdx", code="E2040")
        store.record_success("tdx")
        assert store.is_degraded("tdx", ttl_ns=10**12) is False
        assert store.snapshot() == {}

    def test_snapshot_is_diagnostic_and_sorted(self) -> None:
        store = ProviderHealthStore()
        store.record_failure("tencent", code="E3040")
        store.record_failure("tdx", code="E2040")
        assert [key for key in store.snapshot()] == ["tdx", "tencent"]
        assert store.snapshot()["tdx"]["code"] == "E2040"


# --------------------------------------------------------------------------- #
# 候选排序
# --------------------------------------------------------------------------- #
class TestAutoCandidates:
    def test_registry_order_without_preference(self) -> None:
        assert auto_candidates("quotes") == QUOTE_CANDIDATES

    def test_preference_moves_a_provider_to_the_front(self) -> None:
        ordered = auto_candidates("quotes", prefer=("sina",))
        assert ordered[0] == "sina"
        # 没被点名的仍在，按注册表顺序接在后面——不会因为没被点名就消失
        assert set(ordered) == set(QUOTE_CANDIDATES)
        assert ordered[1:] == tuple(p for p in QUOTE_CANDIDATES if p != "sina")

    def test_unknown_capability_has_no_candidates(self) -> None:
        assert auto_candidates("no-such-capability") == ()

    def test_exclude_removes_operational_providers(self) -> None:
        ordered = auto_candidates("bars", exclude={"local_vipdoc"})
        assert "local_vipdoc" not in ordered
        assert "local_vipdoc" in PROVIDERS.available_providers("bars")

    def test_degraded_provider_sinks_but_stays_a_candidate(self) -> None:
        provider_health().record_failure("tdx", code="E2040")
        ordered = auto_candidates("quotes", ttl_ns=10**12)
        assert ordered == tuple(p for p in QUOTE_CANDIDATES if p != "tdx") + ("tdx",)
        # 相对次序不变：tdx 只是沉底，其余顺序原样
        assert list(ordered[:-1]) == [p for p in QUOTE_CANDIDATES if p != "tdx"]

    def test_unknown_preference_fails_instead_of_being_dropped(self) -> None:
        """拼错的偏好名必须报错：静默丢弃＝选源顺序悄悄退回登记序。"""

        with pytest.raises(ValidationError) as caught:
            auto_candidates("quotes", prefer=("txd",))
        assert caught.value.context["unknown_providers"] == ["txd"]

    def test_preference_duplicates_collapse(self) -> None:
        assert auto_candidates("quotes", prefer=("sina", "sina"))[0] == "sina"
        assert len(auto_candidates("quotes", prefer=("sina", "sina"))) == len(QUOTE_CANDIDATES)


class TestResolveAutoProvider:
    def test_returns_the_first_candidate(self) -> None:
        assert resolve_auto_provider("quotes") == QUOTE_CANDIDATES[0]
        assert resolve_auto_provider("quotes", prefer=("baidu",)) == "baidu"

    def test_no_candidate_falls_back_to_the_declared_default(self) -> None:
        """没有 operational 候选时不抛：退回声明口径，让该 Provider 自己 fail-fast。"""

        assert resolve_auto_provider("no-such-capability", default="sina") == "sina"


# --------------------------------------------------------------------------- #
# AutoPolicy
# --------------------------------------------------------------------------- #
class TestAutoPolicy:
    def test_prefer_is_normalized(self) -> None:
        assert AutoPolicy.build(prefer=["tencent", "tencent", " sina "]).prefer == (
            "tencent",
            "sina",
        )

    def test_nested_auto_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutoPolicy.build(prefer=["tdx", "auto"])


# --------------------------------------------------------------------------- #
# 逐跳执行
# --------------------------------------------------------------------------- #
class TestExecuteAuto:
    def test_first_candidate_serves_without_fallback(self) -> None:
        class Runtime:
            def execute(self, spec):
                assert spec.provider == QUOTE_CANDIDATES[0]
                return _result(spec.provider)

        output = ProviderOrchestrator(Runtime()).execute_auto(
            QuerySpec.build("quotes", symbols="sh600519"), policy=AutoPolicy()
        )
        assert [(item.provider, item.status) for item in output.attempts] == [
            (QUOTE_CANDIDATES[0], "ok")
        ]
        assert output.result.meta.provenance.requested_provider == AUTO_PROVIDER
        assert output.result.meta.provenance.fallback is False

    def test_failed_candidate_is_recorded_and_the_next_one_serves(self) -> None:
        seen: list[str] = []

        class Runtime:
            def execute(self, spec):
                seen.append(spec.provider)
                if spec.provider == "tdx":
                    raise ValidationError("tdx unavailable", context={"provider": "tdx"})
                return _result(spec.provider)

        output = ProviderOrchestrator(Runtime()).execute_auto(
            QuerySpec.build("quotes", symbols="sh600519"),
            policy=AutoPolicy.build(prefer=["tdx", "sina"]),
            ttl_ns=10**12,
        )
        assert seen == ["tdx", "sina"]
        assert output.result.meta.provider == "sina"
        assert output.result.meta.provenance.requested_provider == AUTO_PROVIDER
        assert output.result.meta.provenance.fallback is True
        # 真实失败喂给了健康存储：下一次 auto 请求会把 tdx 沉底
        assert provider_health().is_degraded("tdx", ttl_ns=10**12) is True
        assert auto_candidates("quotes", prefer=("tdx",), ttl_ns=10**12)[0] != "tdx"

    def test_all_candidates_failing_is_one_explicit_error(self) -> None:
        class Runtime:
            def execute(self, spec):
                raise ValidationError(f"{spec.provider} down", context={"provider": spec.provider})

        with pytest.raises(AllSourcesExhausted) as caught:
            ProviderOrchestrator(Runtime()).execute_auto(
                QuerySpec.build("quotes", symbols="sh600519"), policy=AutoPolicy()
            )
        context = caught.value.context
        assert context["requested_provider"] == AUTO_PROVIDER
        assert context["fallback"] is True
        assert context["providers"] == list(QUOTE_CANDIDATES)
        assert len(context["errors"]) == len(QUOTE_CANDIDATES)


# --------------------------------------------------------------------------- #
# 内核侧：auto 只在显式形态下生效
# --------------------------------------------------------------------------- #
class TestPlannerAutoResolution:
    def test_explicit_auto_compiles_to_a_concrete_provider(self) -> None:
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols="sh600519", provider="auto")
        )
        assert plan.provider in QUOTE_CANDIDATES
        assert plan.provider != AUTO_PROVIDER

    def test_auto_default_resolves_only_when_provider_is_omitted(self) -> None:
        planner = QueryPlanner(default_provider="auto", auto_prefer=("sina",))
        resolved = planner.compile(QuerySpec.build("quotes", symbols="sh600519"))
        assert resolved.provider == "sina"
        # 钉死 Provider 的请求永远不经过自动选源
        pinned = planner.compile(
            QuerySpec.build("quotes", symbols="sh600519", provider="eastmoney")
        )
        assert pinned.provider == "eastmoney"

    def test_health_feedback_moves_the_resolved_provider(self) -> None:
        planner = QueryPlanner(default_provider="auto", auto_ttl_ns=10**12)
        assert planner.compile(QuerySpec.build("quotes", symbols="sh600519")).provider == "tdx"
        provider_health().record_failure("tdx", code="E2040")
        assert planner.compile(QuerySpec.build("quotes", symbols="sh600519")).provider != "tdx"


# --------------------------------------------------------------------------- #
# Client 侧：批量入口与单只入口同语义
# --------------------------------------------------------------------------- #
class _FailThenServe:
    """按 Provider 名字失败的记录型执行器（不发网络）。"""

    def __init__(self, *, failing: str = "tdx") -> None:
        self.failing = failing
        self.plans: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, plan: Any) -> QueryResult[Any]:
        self.plans.append((plan.provider, tuple(plan.spec.symbols)))
        if plan.provider == self.failing:
            raise ValidationError(f"{plan.provider} down", context={"provider": plan.provider})
        return QueryResult.from_plan(
            [{"code": plan.spec.symbols[0], "price": 1.0}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )


def _client(executor: Any, *, default_provider: str = "tdx") -> Any:
    return Client(runtime=UnifiedRuntime(executor=executor, default_provider=default_provider))


class TestClientAutoSurfaces:
    def test_quotes_batch_auto_walks_each_symbol_like_quotes(self) -> None:
        """``quotes_batch(provider="auto")`` 必须与 ``quotes`` 同语义。

        旧行为：批量入口直接把 ``auto`` 交给内核编译成"第一跳"，于是同一只标的
        在批量里失败就进 ``errors``、在单只上却能换源——同一个 ``auto`` 两种行为。
        """

        executor = _FailThenServe(failing="tdx")
        client = _client(executor)
        batch = client.quotes_batch(["sh600519", "sz000001"], provider="auto")

        # 第一只：tdx 失败 → 第二候选接住；第二只：tdx 已被健康反馈沉底，直接从
        # 第二候选起步（同一批内也能看到"刚刚失败过"生效）
        assert [provider for provider, _ in executor.plans] == [
            "tdx",
            QUOTE_CANDIDATES[1],
            QUOTE_CANDIDATES[1],
        ]
        assert [symbols for _, symbols in executor.plans] == [
            ("sh600519",),
            ("sh600519",),
            ("sz000001",),
        ]
        assert batch.status_for("sh600519") == "ok"
        assert batch.status_for("sz000001") == "ok"
        assert batch.partial is False
        # 两只都标明"这是 auto 出的数"；第一只换过源（fallback=True），第二只
        # 起手就是当时的第一候选（fallback=False）——逐项 provenance 各自诚实
        assert {
            symbol: item.value.meta.provenance.requested_provider
            for symbol, item in batch.items.items()
        } == {"sh600519": AUTO_PROVIDER, "sz000001": AUTO_PROVIDER}
        assert batch.items["sh600519"].value.meta.provenance.fallback is True
        assert batch.items["sz000001"].value.meta.provenance.fallback is False

    def test_quotes_batch_auto_reports_per_symbol_failure(self) -> None:
        """全候选失败的标的进 ``errors``，其余标的照常出数（三态审计不被破坏）。"""

        class _FailsOnlyForSz(_FailThenServe):
            def execute(self, plan: Any) -> QueryResult[Any]:
                if plan.spec.symbols[0] == "sz000001":
                    self.plans.append((plan.provider, tuple(plan.spec.symbols)))
                    raise ValidationError("down", context={"provider": plan.provider})
                return super().execute(plan)

        executor = _FailsOnlyForSz(failing="tdx")
        client = _client(executor)
        batch = client.quotes_batch(["sh600519", "sz000001"], provider="auto")

        assert batch.status_for("sh600519") == "ok"
        assert batch.status_for("sz000001") == "failed"
        assert "sz000001" in batch.errors
        assert batch.partial is True

    def test_quotes_batch_with_a_pinned_provider_takes_no_walk(self) -> None:
        executor = _FailThenServe(failing="tdx")
        client = _client(executor)
        batch = client.quotes_batch(["sh600519"], provider="sina")

        assert [(provider, symbols) for provider, symbols in executor.plans] == [
            ("sina", ("sh600519",))
        ]
        assert batch.status_for("sh600519") == "ok"
        assert batch.items["sh600519"].value.meta.provenance.requested_provider == "sina"

    def test_quotes_batch_follows_an_auto_default(self) -> None:
        """``core.default_provider="auto"`` 时批量入口同样走候选链。"""

        executor = _FailThenServe(failing="tdx")
        client = _client(executor, default_provider="auto")
        client.quotes_batch(["sh600519"])

        assert [provider for provider, _ in executor.plans] == ["tdx", QUOTE_CANDIDATES[1]]


# --------------------------------------------------------------------------- #
# 观测面：降级名单必须看得见
# --------------------------------------------------------------------------- #
def _health_client(*, ttl: float = 90.0) -> Client:
    """显式给 ``Config()``：不读开发者机器上的用户配置，TTL 才是确定值。"""

    return Client(
        runtime=UnifiedRuntime(
            config=Config(routing=RoutingConfig(auto_health_ttl=ttl)),
            executor=_FailThenServe(),
        )
    )


class TestProviderHealthStatus:
    def test_defaults_to_empty(self) -> None:
        assert _health_client().provider_health_status() == {}

    def test_reports_the_code_and_age_of_a_recent_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"now": 5_000_000_000}
        monkeypatch.setattr(autoselect.time, "monotonic_ns", lambda: clock["now"])
        client = _health_client()

        provider_health().record_failure("tdx", code="E2040")
        clock["now"] += 1_500_000_000

        assert client.provider_health_status() == {"tdx": {"code": "E2040", "age_s": 1.5}}

    def test_expired_marks_are_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """过期项在**读取时**复位——观测面报的名单与 ``auto`` 排序用的是同一份。"""

        clock = {"now": 0}
        monkeypatch.setattr(autoselect.time, "monotonic_ns", lambda: clock["now"])
        client = _health_client(ttl=90.0)

        provider_health().record_failure("tdx", code="E2040")
        clock["now"] = 91_000_000_000

        assert client.provider_health_status() == {}
        assert provider_health().snapshot() == {}

    def test_success_removes_the_provider(self) -> None:
        client = _health_client()
        provider_health().record_failure("tdx", code="E2040")
        assert "tdx" in client.provider_health_status()

        provider_health().record_success("tdx")
        assert client.provider_health_status() == {}

    def test_ttl_zero_reports_nothing(self) -> None:
        """健康反馈关闭时没有任何 Provider 会被降级，报一份名单就是凭空造机制。"""

        client = _health_client(ttl=0.0)
        provider_health().record_failure("tdx", code="E2040")

        assert client.provider_health_status() == {}

    def test_http_and_ws_health_faces_report_the_same_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两面同一读数：名单与 TTL 都不许各自为政。"""

        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient as _HttpClient

        from atst.integration.runtime_http import create_runtime_app
        from atst.integration.runtime_ws import RuntimeJsonRpcHandler

        #: 时钟钉死：两次读取的 ``age_s`` 才可能逐位相等（否则一次跨毫秒就抖）。
        monkeypatch.setattr(autoselect.time, "monotonic_ns", lambda: 1_000_000_000)
        client = _health_client()
        provider_health().record_failure("tdx", code="E2040")

        with _HttpClient(create_runtime_app(client)) as http:
            over_http = http.get("/v13/runtime/health").json()["provider_health"]

        handler = RuntimeJsonRpcHandler(client)
        raw = handler.handle_message(
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": "runtime.health", "params": {}})
        )
        assert raw is not None
        over_ws = json.loads(raw)["result"]["provider_health"]

        assert over_http == over_ws == {"tdx": {"code": "E2040", "age_s": 0.0}}
