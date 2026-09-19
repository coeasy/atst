# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F-45 的收口判据：结果携带的数据完整性瑕疵必须逐面到达调用方。

三条链各自把守一段：

1. :mod:`tstdx.diagnostics` 是唯一发射口，一次查询一份收集器；
2. 执行器把收集器装进 ``QueryResult.meta.warnings``，``strict`` 决定它是告警还是失败；
3. ``serialize_result`` 把它送上 HTTP / WS / MCP 的 wire。

每条判据都配一条"扫不到东西就自杀"的自检，避免门禁自身失效。
"""

from __future__ import annotations

import dataclasses
import threading
import warnings
from pathlib import Path
from typing import Any

import pytest

from tstdx.diagnostics import ResultWarning, WarningCode, record_warning, warning_sink
from tstdx.errors import TruncatedDataError, ValidationError
from tstdx.integration.serialization import serialize_result
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult, ResultMeta
from tstdx.runtime.executor import DirectProviderExecutor

ROOT = Path(__file__).resolve().parents[2]


def _plan(capability: str = "quotes", **options: Any) -> Any:
    spec = QuerySpec.build(
        capability,
        symbols=["sh600519"] if capability == "quotes" else "sh600519",
        provider="tdx",
        count=10 if capability == "bars" else 0,
        options=options or None,
    )
    return QueryPlanner().compile(spec)


class TestEmitPoint:
    def test_record_warning_reaches_active_sink_and_stderr(self) -> None:
        with (
            warning_sink() as collected,
            pytest.warns(UserWarning, match="bars_anchor_drift"),
        ):
            record_warning(WarningCode.BARS_ANCHOR_DRIFT, "实取 3 根 < 请求 10 根")
        assert [item.code for item in collected] == [WarningCode.BARS_ANCHOR_DRIFT]
        assert collected[0].message.startswith("实取 3 根")

    def test_warning_outside_any_query_still_reaches_stderr(self) -> None:
        """没有收集器时不抛、不吞：CLI 与 import 期告警的既有行为逐字不变。"""
        with pytest.warns(UserWarning, match="decode_caveat"):
            record_warning(WarningCode.DECODE_CAVEAT, "声明 5 条实收 3 条")

    def test_stderr_false_records_the_fact_without_nagging_the_process(self) -> None:
        with (
            warning_sink() as collected,
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error")
            record_warning(
                WarningCode.CALENDAR_YEAR_UNCOVERED, "2099 年按无节假日处理", stderr=False
            )
        assert [item.code for item in collected] == [WarningCode.CALENDAR_YEAR_UNCOVERED]

    def test_sinks_do_not_leak_across_threads(self) -> None:
        """并发服务面共用一个进程：收集器串台就等于把 A 的瑕疵记在 B 的结果上。"""
        seen: dict[str, list[WarningCode]] = {}

        def worker(name: str, code: WarningCode) -> None:
            with warning_sink() as collected:
                for _ in range(50):
                    record_warning(code, name, stderr=False)
            seen[name] = [item.code for item in collected]

        threads = [
            threading.Thread(target=worker, args=("a", WarningCode.BARS_ANCHOR_DRIFT)),
            threading.Thread(target=worker, args=("b", WarningCode.BARS_EMPTY_FIRST_PAGE)),
        ]
        for item in threads:
            item.start()
        for item in threads:
            item.join()
        assert seen["a"] == [WarningCode.BARS_ANCHOR_DRIFT] * 50
        assert seen["b"] == [WarningCode.BARS_EMPTY_FIRST_PAGE] * 50

    def test_nested_sink_is_restored_on_exit(self) -> None:
        with warning_sink() as outer:
            record_warning(WarningCode.DECODE_CAVEAT, "外层", stderr=False)
        assert len(outer) == 1
        with warning_sink() as inner:
            record_warning(WarningCode.DECODE_CAVEAT, "内层", stderr=False)
        assert len(inner) == 1 and len(outer) == 1

    def test_unknown_code_cannot_be_invented_at_call_time(self) -> None:
        with pytest.raises(TypeError, match="WarningCode"):
            record_warning("made_up_caveat", "绕过声明表的发射")  # type: ignore[arg-type]

    def test_result_warning_rejects_undeclared_code_and_blank_message(self) -> None:
        with pytest.raises(ValueError, match="WarningCode"):
            ResultWarning(code="bars_anchor_drift", message="字符串不是代码")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            ResultWarning(code=WarningCode.BARS_ANCHOR_DRIFT, message="   ")


class TestExecutorCarriesCaveats:
    def test_caveat_recorded_during_execution_rides_the_result(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor()
        monkeypatch.setattr(
            executor,
            "_tdx_quotes",
            lambda plan: record_warning(WarningCode.BARS_EMPTY_FIRST_PAGE, "空桩", stderr=False),
        )
        result = executor.execute(_plan())
        assert [item.code.value for item in result.meta.warnings] == ["bars_empty_first_page"]
        assert result.meta.provenance.cache_tier is None

    def test_clean_result_carries_an_empty_tuple_not_a_missing_field(
        self, monkeypatch: Any
    ) -> None:
        executor = DirectProviderExecutor()
        monkeypatch.setattr(executor, "_tdx_quotes", lambda plan: [{"last_price": 1.0}])
        assert executor.execute(_plan()).meta.warnings == ()

    def test_strict_turns_a_caveat_into_a_failure(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor()
        monkeypatch.setattr(
            executor,
            "_tdx_quotes",
            lambda plan: record_warning(
                WarningCode.WEB_TENCENT_AMOUNT_ALL_ZERO, "全 0", stderr=False
            ),
        )
        with pytest.raises(TruncatedDataError) as exc_info:
            executor.execute(_plan(strict=True))
        assert exc_info.value.context["codes"] == ["web_tencent_amount_all_zero"]
        assert exc_info.value.context["fallback"] is False

    def test_strict_false_keeps_the_long_standing_warn_and_return_shape(
        self, monkeypatch: Any
    ) -> None:
        executor = DirectProviderExecutor()
        monkeypatch.setattr(
            executor,
            "_tdx_quotes",
            lambda plan: record_warning(WarningCode.SECURITY_LIST_PAGE_LIMIT, "截断", stderr=False),
        )
        result = executor.execute(_plan(strict=False))
        assert [item.code for item in result.meta.warnings] == [
            WarningCode.SECURITY_LIST_PAGE_LIMIT
        ]

    def test_non_bool_strict_fails_before_any_provider_io(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor()
        calls: list[str] = []
        monkeypatch.setattr(executor, "_tdx_quotes", lambda plan: calls.append("io"))
        with pytest.raises(ValidationError, match="必须是 bool"):
            executor.execute(_plan(strict="yes"))
        assert calls == []


class TestWireReachesEveryFace:
    def _sample(self) -> QueryResult[Any]:
        plan = _plan()
        provenance = Provenance.direct(plan)
        return QueryResult.from_plan(
            [],
            plan=plan,
            provenance=provenance,
            warnings=[ResultWarning(code=WarningCode.BARS_EMPTY_FIRST_PAGE, message="首页 0 根")],
        )

    def test_wire_meta_covers_every_result_meta_field(self) -> None:
        """结果侧的镜像判据：``ResultMeta`` 加一个字段，wire 就必须跟着有键。

        请求侧的"已声明入参必须到达执行面"已在第 23 步把守；这里是反方向——
        已声明的**结果事实**不许在出口被逐字段手写清单漏掉。
        """
        fields = {item.name for item in dataclasses.fields(ResultMeta)}
        assert len(fields) >= 6, f"字段扫描只看到 {sorted(fields)}，判据自身失效"
        payload = serialize_result(self._sample())["meta"]
        missing = sorted(fields - set(payload))
        extra = sorted(set(payload) - fields)
        assert missing == [] and extra == []

    def test_caveat_is_jsonable_on_the_wire(self) -> None:
        payload = serialize_result(self._sample())["meta"]["warnings"]
        assert payload == [{"code": "bars_empty_first_page", "message": "首页 0 根"}]

    @pytest.mark.parametrize(
        "module",
        [
            "tstdx/integration/runtime_http.py",
            "tstdx/integration/runtime_ws.py",
            "tstdx/integration/mcp/_tools_impl.py",
            "tstdx/cli/runtime_commands.py",
        ],
    )
    def test_every_exit_uses_the_shared_serializer(self, module: str) -> None:
        """任何一面自写出口字典，就是下一个漏掉 ``warnings`` 的地方。"""
        source = (ROOT / module).read_text(encoding="utf-8")
        assert "serialize_result" in source, f"{module} 不再经共享序列化出口"
