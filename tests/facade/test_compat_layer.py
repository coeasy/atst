# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""facade 三门面（binary/bridge/market）兼容层冒烟测试（§4 库内孤儿治理）。

三类门面定位为「竞品吸收兼容层」（见各模块 docstring 顶部定位行），本文件
为每类补 ≥3 条冒烟：构造、方法委托参数传递、close/上下文管理。

注入策略（按各构造实际支持面）：

* :class:`~tstdx.facade.binary.BinaryClient` / :class:`~tstdx.facade.bridge.BridgeClient`
  为**惰性**构造（``_active_client`` 首次访问才建 TdxClient）→ 直接注入
  ``_client`` 属性即为本文件使用的稳定 seam；
* :class:`~tstdx.facade.market.HqClient` / ``ExHqClient`` / ``OptionClient``
  在 ``__init__`` **急切**构造底层客户端 → monkeypatch ``tstdx.client``
  命名空间中的对应客户端类。

全部离线，不触网。
"""

from __future__ import annotations

import pytest

from tstdx.errors import CompatibilityWarning


class FakeTdx:
    """假 TdxClient：记录委托调用参数（含上下文协议）。"""

    def __init__(self, **kw: object) -> None:
        self.kw = kw
        self.calls: list[tuple] = []
        self.closed = False

    def __enter__(self) -> FakeTdx:
        self.calls.append(("enter",))
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self.closed = True

    def bars(self, symbol, *, period="day", count=80, start=0, as_format="dict"):  # noqa: ANN001
        self.calls.append(("bars", symbol, period, count, start, as_format))
        return [{"symbol": symbol}]

    def quotes(self, symbols, *, as_format="dict"):  # noqa: ANN001
        self.calls.append(("quotes", list(symbols), as_format))
        return [{"code": s} for s in symbols]

    def minute_today(self, symbol):  # noqa: ANN001
        self.calls.append(("minute", symbol))
        return [{"t": 1}, {"t": 2}]

    def trade_today(self, symbol, start=0, count=0):  # noqa: ANN001
        self.calls.append(("trade", symbol, start, count))
        return [{"p": 1}]

    def security_count(self, market=0):  # noqa: ANN001
        self.calls.append(("count", market))
        return 10


# --------------------------------------------------------------------------- #
# binary.BinaryClient
# --------------------------------------------------------------------------- #
class TestBinaryClient:
    def test_construct_lazy_and_connected_semantics(self) -> None:
        from tstdx.facade.binary import BinaryClient

        b = BinaryClient()
        assert b._client is None
        assert b.connected is False  # 客户端对象未创建
        fake = FakeTdx()
        b._client = fake  # 稳定注入 seam（惰性属性）
        assert b.connected is True  # 客户端对象已创建（非已建连）

    def test_bars_delegates_with_args(self) -> None:
        from tstdx.facade.binary import BinaryClient

        b = BinaryClient()
        fake = FakeTdx()
        b._client = fake
        out = b.bars("sh600519", period="5min", count=9, start=2)
        assert out == [{"symbol": "sh600519"}]
        assert fake.calls[0] == ("bars", "sh600519", "5min", 9, 2, "dict")

    def test_quotes_minute_trade_delegate(self) -> None:
        from tstdx.facade.binary import BinaryClient

        b = BinaryClient()
        fake = FakeTdx()
        b._client = fake
        assert b.quotes(["sh600519", "sz000001"])[1]["code"] == "sz000001"
        assert b.minute("sh600519", count=1) == [{"t": 1}]  # 单只切片
        assert b.trade_details("sh600519") == [{"p": 1}]
        assert ("quotes", ["sh600519", "sz000001"], "dict") in fake.calls
        assert ("minute", "sh600519") in fake.calls
        assert ("trade", "sh600519", 0, 0) in fake.calls

    def test_close_and_context_manager(self) -> None:
        from tstdx.facade.binary import BinaryClient

        b = BinaryClient()
        fake = FakeTdx()
        b._client = fake
        with b as ctx:
            assert ctx is b
        assert fake.closed is True
        assert b._client is None and b.connected is False

    def test_connected_docstring_locks_semantics(self) -> None:
        from tstdx.facade.binary import BinaryClient

        assert "非已建连" in (BinaryClient.connected.__doc__ or "")


# --------------------------------------------------------------------------- #
# bridge.BridgeClient
# --------------------------------------------------------------------------- #
class TestBridgeClient:
    def test_construct_lazy_and_inject(self) -> None:
        from tstdx.facade.bridge import BridgeClient

        b = BridgeClient()
        assert b._client is None and b.connected is False
        fake = FakeTdx()
        b._client = fake
        assert b.connected is True

    def test_bars_period_alias_mapping(self) -> None:
        from tstdx.facade.bridge import BridgeClient

        b = BridgeClient()
        fake = FakeTdx()
        b._client = fake
        b.bars("sh600519", period="5m", count=7)
        assert fake.calls[0] == ("bars", "sh600519", "5min", 7, 0, "dict")

    def test_unknown_period_alias_warns_and_falls_back_to_day(self) -> None:
        """行为锁：未知 period 别名 → CompatibilityWarning + 回退 day。"""
        from tstdx.facade.bridge import BridgeClient

        b = BridgeClient()
        fake = FakeTdx()
        b._client = fake
        with pytest.warns(CompatibilityWarning, match="未知"):
            b.bars("sh600519", period="not-a-period")
        assert fake.calls[0][2] == "day"

    def test_quotes_single_vs_batch_and_minute(self) -> None:
        from tstdx.facade.bridge import BridgeClient

        b = BridgeClient()
        fake = FakeTdx()
        b._client = fake
        assert b.quotes("sh600519") == {"code": "sh600519"}  # 单只 → dict
        rows = b.quotes(["sh600519", "sz000001"])  # 批量 → list
        assert isinstance(rows, list) and len(rows) == 2
        assert b.minute("sh600519")[1] == {"t": 2}

    def test_close_and_context_manager(self) -> None:
        from tstdx.facade.bridge import BridgeClient

        b = BridgeClient()
        fake = FakeTdx()
        b._client = fake
        with b as ctx:
            assert ctx is b
        assert fake.closed is True and b._client is None

    def test_connected_docstring_locks_semantics(self) -> None:
        from tstdx.facade.bridge import BridgeClient

        assert "非已建连" in (BridgeClient.connected.__doc__ or "")


# --------------------------------------------------------------------------- #
# market.HqClient / ExHqClient / OptionClient（急切构造 → monkeypatch 类）
# --------------------------------------------------------------------------- #
class TestHqClient:
    def test_construct_forwards_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import HqClient

        created: list[dict] = []
        fake = FakeTdx()

        def factory(**kw: object) -> FakeTdx:
            created.append(dict(kw))
            return fake

        monkeypatch.setattr(client_mod, "TdxClient", factory)
        hq = HqClient(timeout=3.0)
        assert created == [{"timeout": 3.0}]
        assert hq._client is fake

    def test_bars_offset_limit_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import HqClient

        fake = FakeTdx()
        monkeypatch.setattr(client_mod, "TdxClient", lambda **kw: fake)
        hq = HqClient()
        assert hq.bars("sh600519", offset=5, limit=7)[0]["symbol"] == "sh600519"
        assert fake.calls[0] == ("bars", "sh600519", "day", 7, 5, "dict")

    def test_quotes_and_security_count_delegate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import HqClient

        fake = FakeTdx()
        monkeypatch.setattr(client_mod, "TdxClient", lambda **kw: fake)
        hq = HqClient()
        assert hq.quotes(["sh600519"])[0]["code"] == "sh600519"
        assert hq.security_count(1) == 10
        assert ("count", 1) in fake.calls

    def test_close_and_context_manager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import HqClient

        fake = FakeTdx()
        monkeypatch.setattr(client_mod, "TdxClient", lambda **kw: fake)
        hq = HqClient()
        with hq as ctx:
            assert ctx is hq
            assert fake.closed is False  # 上下文内未关闭
        assert fake.closed is True


class TestExAndOptionClients:
    def test_ex_hq_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import ExHqClient

        fake = FakeTdx()
        fake.ex_bars = (  # type: ignore[method-assign]
            lambda symbol, *, period="day", count=320, start=0, as_format="dict": [{"s": symbol}]
        )
        fake.ex_quote = lambda symbol, as_format="dict": {"code": symbol}  # type: ignore[method-assign]
        monkeypatch.setattr(client_mod, "ExMarketClient", lambda **kw: fake)
        ex = ExHqClient()
        assert ex.ex_bars("hk00700") == [{"s": "hk00700"}]
        assert ex.ex_quotes("hk00700") == [{"code": "hk00700"}]
        ex.close()
        assert fake.closed is True

    def test_option_client_delegates_and_closes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import OptionClient

        fake = FakeTdx()
        fake.goods_bars = (  # type: ignore[method-assign]
            lambda symbol, *, period="day", count=320, start=0, as_format="dict": [{"s": symbol}]
        )
        fake.goods_quote = lambda symbol, as_format="dict": {"code": symbol}  # type: ignore[method-assign]
        monkeypatch.setattr(client_mod, "GoodsClient", lambda **kw: fake)
        oc = OptionClient()
        assert oc.goods_bars("m2509") == [{"s": "m2509"}]
        assert oc.goods_quotes("m2509") == [{"code": "m2509"}]
        oc.close()
        assert fake.closed is True

    def test_market_client_factory_routing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.client as client_mod
        from tstdx.facade.market import HqClient, market_client

        monkeypatch.setattr(client_mod, "TdxClient", lambda **kw: FakeTdx())
        assert isinstance(market_client("std"), HqClient)
        assert isinstance(market_client("standard"), HqClient)
        with pytest.warns(CompatibilityWarning, match="未知"):
            assert isinstance(market_client("nope"), HqClient)  # 回退 std
