# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""内核 ``quotes`` 一跳的失败形状（F-72 / V18-B1）。

``TdxClient.quotes`` 逐只隔离失败：默认（不传 ``_collect``）时那些失败只写进
``client.last_errors``——一个每次调用都被整体覆盖、内核从不读取的实例属性。于是
"8 台主机全连不上"在执行面上的形状与"这只代码没有行情"完全相同：``data=[]``、
``meta.warnings=()``、HTTP 200。三条离线判据把这条静默通道关掉：全败即抛、部分
失败随结果携带、``strict=True`` 能拒收一份不完整的答案。

假客户端在这里是**必要的**而非省事的：离线全量之所以 3 000 多项全绿仍照不到这个洞，
正是因为假客户端不会让所有主机同时失败（F-72 的取证段）。这里让假客户端按指令
全部失败，缺陷才有形状可测。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.diagnostics import WarningCode
from atst.errors import AllHostsUnreachable, ParseError, TruncatedDataError
from atst.query import QueryPlanner, QuerySpec
from atst.runtime.executor import DirectProviderExecutor

SYMBOLS = ["sh600519", "sz000001"]


def _plan(symbols: list[str] | None = None, **kwargs: Any) -> Any:
    return QueryPlanner().compile(
        QuerySpec.build(
            "quotes",
            symbols=list(SYMBOLS if symbols is None else symbols),
            provider="tdx",
            **kwargs,
        )
    )


def _row(code: str) -> dict[str, Any]:
    return {"code": code, "last_price": 10.0}


class _StubTdxClient:
    """按脚本成功/失败的 ``TdxClient`` 替身：失败一律经 ``_collect`` 投递。"""

    def __init__(self, rows: list[dict[str, Any]], failures: dict[str, BaseException]) -> None:
        self._rows = rows
        self._failures = failures

    def __enter__(self) -> _StubTdxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def quotes(
        self,
        symbols: Any,
        *,
        as_format: str = "dict",  # noqa: ARG002 - 替身只产 canonical dict
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> list[dict[str, Any]]:
        if _collect is None:
            # 真实客户端在没有失败袋时把失败写进 last_errors；替身没有那个属性，
            # 于是"内核不读侧信道"这件事一旦回退就会在这里变成异常。
            raise AssertionError("内核必须显式取走失败袋，不能依赖 last_errors 侧信道")
        _collect.extend((sym, self._failures[sym]) for sym in symbols if sym in self._failures)
        return list(self._rows)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Any:
    """把替身接到 ``_tdx_client`` 上，返回可配置的工厂。"""

    def _factory(
        rows: list[dict[str, Any]] | None = None,
        failures: dict[str, BaseException] | None = None,
    ) -> _StubTdxClient:
        client = _StubTdxClient(rows or [], failures or {})
        monkeypatch.setattr("atst.client.TdxClient", lambda *a, **k: client)
        return client

    return _factory


def _executor() -> DirectProviderExecutor:
    return DirectProviderExecutor(timeout=1.0, hosts=[["1.2.3.4", 7709]])


def test_total_failure_raises_instead_of_returning_an_empty_success(stub: Any) -> None:
    """全败不再是 ``data=[]`` 的成功：与 ``bars`` 同一条判据。"""

    unreachable = AllHostsUnreachable("所有主机不可达")
    stub(failures={sym: unreachable for sym in SYMBOLS})
    with pytest.raises(AllHostsUnreachable) as caught:
        _executor().execute(_plan())
    #: 异常确实经过了内核：provider/channel/capability 由 ``execute`` 补齐。
    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["capability"] == "quotes"


def test_partial_failure_rides_the_result_as_a_caveat(stub: Any) -> None:
    """部分失败：结果照常返回，但那几只缺失必须写在结果自己身上。"""

    stub(rows=[_row("sh600519")], failures={"sz000001": ParseError("0x0530 无解析结果")})
    result = _executor().execute(_plan())

    assert [item["code"] for item in result.data] == ["sh600519"]
    assert [item.code for item in result.meta.warnings] == [WarningCode.QUOTES_PARTIAL_FAILURE]
    message = result.meta.warnings[0].message
    assert "1/2" in message and "sz000001" in message and "ParseError" in message


def test_strict_rejects_a_partial_quotes_result(stub: Any) -> None:
    """``strict=True`` 必须能拒收"少了几只也照样 200"的结果。"""

    stub(rows=[_row("sh600519")], failures={"sz000001": ParseError("0x0530 无解析结果")})
    with pytest.raises(TruncatedDataError) as caught:
        _executor().execute(_plan(options={"strict": True}))
    assert caught.value.context["codes"] == ["quotes_partial_failure"]


def test_clean_batch_carries_no_caveat(stub: Any) -> None:
    stub(rows=[_row("sh600519"), _row("sz000001")])
    result = _executor().execute(_plan())

    assert result.meta.warnings == ()
    assert len(result.data) == 2


def test_single_symbol_total_failure_is_not_an_empty_success(stub: Any) -> None:
    """健康检查最常撞的那一格：一只标的、连不上，必须是一次失败而不是 ``data=[]``。"""

    stub(failures={"sh600519": AllHostsUnreachable("所有主机不可达")})
    with pytest.raises(AllHostsUnreachable):
        _executor().execute(_plan(symbols=["sh600519"]))
