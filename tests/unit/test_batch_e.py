# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""批次 E 可即启项测试：E1 并发批采 / E3 代码表导出。

全部离线（monkeypatch 单只通路），零网络。
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit

from atst.client import TdxClient  # noqa: E402
from atst.errors import ValidationError  # noqa: E402


# --------------------------------------------------------------------------- #
# E1 quotes_concurrent
# --------------------------------------------------------------------------- #
class TestQuotesConcurrent:
    @pytest.fixture()
    def client(self) -> TdxClient:
        return TdxClient()

    def test_order_preserved(self, client: TdxClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """结果保序：与输入 symbols 一一对应（并发不乱序）。"""

        def fake_quotes(symbols, *, as_format="dict", _collect=None):
            s = symbols[0]
            return [{"code": s, "price": float(len(s))}]

        monkeypatch.setattr(client, "quotes", fake_quotes)
        syms = [f"sh60{i:04d}" for i in range(30)]
        out = client.quotes_concurrent(syms, workers=8)
        assert [q["code"] for q in out] == syms

    def test_failures_skipped_and_recorded(
        self, client: TdxClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """单只失败不影响其余，且记入 last_errors。"""

        def flaky_quotes(symbols, *, as_format="dict", _collect=None):
            if symbols[0].endswith("0003"):
                raise TimeoutError("simulated")
            return [{"code": symbols[0]}]

        monkeypatch.setattr(client, "quotes", flaky_quotes)
        syms = ["sh600001", "sh600003", "sh600005"]
        out = client.quotes_concurrent(syms, workers=4)
        assert len(out) == 2
        assert all(q["code"] != "sh600003" for q in out)
        assert [sym for sym, _ in client.last_errors] == ["sh600003"]

    def test_concurrent_errors_not_evaporated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C3 回归：并发两只坏一只好，last_errors 恰含两条错误（不再被
        并发 quotes() 的每调用重置蒸发）。"""
        from atst.errors import ConnectionFailed

        client = TdxClient()
        bad = {"sh600001", "sh600002"}

        def fake_quotes(symbols, *, as_format="dict", _collect=None):
            sym = symbols[0]
            if sym in bad:
                raise ConnectionFailed(f"simulated outage: {sym}")
            return [{"code": sym}]

        monkeypatch.setattr(client, "quotes", fake_quotes)
        out = client.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=3)
        assert [q["code"] for q in out] == ["sh600003"]
        assert len(client.last_errors) == 2
        assert {sym for sym, _ in client.last_errors} == bad
        assert all(isinstance(exc, ConnectionFailed) for _, exc in client.last_errors)

    def test_empty_input(self, client: TdxClient) -> None:
        assert client.quotes_concurrent([]) == []

    def test_workers_cap(self, client: TdxClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """workers 不超过任务数；越界输入 fail-closed（不静默夹取）。"""
        monkeypatch.setattr(
            client,
            "quotes",
            lambda symbols, *, as_format="dict", _collect=None: [{"code": symbols[0]}],
        )
        # 上限：worker 数按任务数收敛（8 > 2），调用照常成功。
        out = client.quotes_concurrent(["sh600000", "sz000001"], workers=8)
        assert len(out) == 2
        # 下限：越界 workers 由 ``_require_int`` 直接拒绝，不静默夹取到 1。
        # 该 fail-closed 契约由 tests/test_client_batch_input_contract.py 与
        # tests/client/test_async_quotes_concurrent_parity.py 共同钉死。
        for bad in (0, -1, 65):
            with pytest.raises(ValidationError, match="workers"):
                client.quotes_concurrent(["sh600000", "sz000001"], workers=bad)


class TestAsyncQuotesConcurrent:
    def test_async_mirror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atst.client import AsyncTdxClient

        client = AsyncTdxClient()

        async def fake_quotes(symbols, *, as_format="dict", _collect=None):
            if symbols[0] == "sh600002":
                raise TimeoutError("sim")
            return [{"code": symbols[0]}]

        monkeypatch.setattr(client, "quotes", fake_quotes)

        async def run() -> list:
            return await client.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=2)

        out = asyncio.run(run())
        assert [q["code"] for q in out] == ["sh600001", "sh600003"]

    def test_async_errors_not_evaporated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C3 异步镜像：并发失败标的的错误一次性落入 last_errors。"""
        from atst.client import AsyncTdxClient
        from atst.errors import ConnectionFailed

        client = AsyncTdxClient()
        bad = {"sh600001", "sh600002"}

        async def fake_quotes(symbols, *, as_format="dict", _collect=None):
            sym = symbols[0]
            if sym in bad:
                raise ConnectionFailed(f"simulated outage: {sym}")
            return [{"code": sym}]

        monkeypatch.setattr(client, "quotes", fake_quotes)

        async def run() -> list:
            return await client.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=3)

        out = asyncio.run(run())
        assert [q["code"] for q in out] == ["sh600003"]
        assert {sym for sym, _ in client.last_errors} == bad


# --------------------------------------------------------------------------- #
# E3 export_security_list
# --------------------------------------------------------------------------- #
class TestExportSecurityList:
    @pytest.fixture()
    def client(self) -> TdxClient:
        return TdxClient()

    def test_pages_until_short_page(
        self, client: TdxClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """短页（<1000）即停；start 前进量 = 已收行数。"""
        pages: dict[int, list] = {
            0: [{"code": f"60{i:04d}"} for i in range(1000)],
            1000: [{"code": f"61{i:04d}"} for i in range(600)],
        }
        calls: list[int] = []

        def fake_security_list(market: int, start: int = 0):
            calls.append(start)
            return pages.get(start, [])

        monkeypatch.setattr(client, "security_list", fake_security_list)
        out = client.export_security_list(1)
        assert len(out) == 1600
        assert calls == [0, 1000]

    def test_empty_first_page(self, client: TdxClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """空首页导出为空，但必须留痕（F-45：此前与"市场无标的"共用同一静默路径）。"""
        monkeypatch.setattr(client, "security_list", lambda m, start=0: [])
        with pytest.warns(UserWarning, match="首页即空响应"):
            assert client.export_security_list(0) == []

    def test_max_pages_guard(self, client: TdxClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """满页永不短页时受 max_pages 上限保护，并告警可能截断（F1）。"""
        monkeypatch.setattr(
            client,
            "security_list",
            lambda m, start=0: [{"code": f"X{start:04d}"} for _ in range(1000)],
        )
        with pytest.warns(UserWarning, match="截断"):
            out = client.export_security_list(0, max_pages=3)
        assert len(out) == 3000

    def test_market_prefix(self, client: TdxClient, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []

        def fake_security_list(market: int, start: int = 0):
            seen.append(market)
            return []

        monkeypatch.setattr(client, "security_list", fake_security_list)
        client.export_security_list("sz")
        assert seen == [0]
