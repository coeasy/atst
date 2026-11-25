# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""百度/基金/指数成分/联想/问财会话 Mixin 离线单测（覆盖率补强）。

会话层只做「构造源 → 透传参数 → finally 关闭」的编排，因此这里用假适配器
类验证三件事：客户端注入的是进程级共享 client、语义参数被正确翻译/透传、
上游异常穿透时 ``close()`` 仍然执行。另有 ``search_symbols`` 的市场过滤与
``valuation_history`` 的指标去重两处真实本地逻辑。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources._session_baidu import BaiduSessionMixin

pytestmark = pytest.mark.unit


class _Fake:
    """通用假适配器：记录调用、按类级 ``results`` 回放结果或异常。"""

    results: dict[str, Any] = {}
    default_result: Any = []

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.client = client
        self.kwargs = kwargs
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            handler = type(self).results.get(name, type(self).default_result)
            if isinstance(handler, Exception):
                raise handler
            if callable(handler):
                return handler(*args, **kwargs)
            return handler

        return _call


def _install(
    monkeypatch: pytest.MonkeyPatch, target: str, attr: str, **results: Any
) -> type[_Fake]:
    fake_cls = type(f"_Fake{attr}", (_Fake,), {"results": results})
    monkeypatch.setattr(f"{target}.{attr}", fake_cls)
    return fake_cls


@pytest.fixture
def sentinel() -> Any:
    return object()


@pytest.fixture(autouse=True)
def _shared(monkeypatch: pytest.MonkeyPatch, sentinel: Any) -> None:
    monkeypatch.setattr("atst.sources._session_baidu._shared_http", lambda: sentinel)


def test_baidu_kline_records_constructor_and_call(
    monkeypatch: pytest.MonkeyPatch, sentinel: Any
) -> None:
    instances: list[_Fake] = []

    class Fake(_Fake):
        def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
            super().__init__(client=client, **kwargs)
            instances.append(self)

        def fetch_kline(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(("fetch_kline", (symbol,), kwargs))
            return [{"symbol": symbol}]

    monkeypatch.setattr("atst.sources.baidu.adapters.BaiduSource", Fake)
    out = BaiduSessionMixin.baidu_kline("600519", period="1w", count=10, end_time=123)
    assert out == [{"symbol": "600519"}]
    inst = instances[0]
    assert inst.client is sentinel
    assert inst.calls == [
        ("fetch_kline", ("600519",), {"period": "week", "count": 10, "end_time": 123})
    ]
    assert inst.closed is True


def test_baidu_minute_ticks_quote_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    instances: list[_Fake] = []

    class Fake(_Fake):
        def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
            super().__init__(client=client, **kwargs)
            instances.append(self)

        def fetch_minute(self, symbol: str) -> list[str]:
            self.calls.append(("fetch_minute", (symbol,), {}))
            return ["m"]

        def fetch_ticks(self, symbol: str, *, limit: int = 200) -> list[str]:
            self.calls.append(("fetch_ticks", (symbol,), {"limit": limit}))
            return ["t"]

        def fetch_quote(self, symbol: str) -> dict[str, Any]:
            self.calls.append(("fetch_quote", (symbol,), {}))
            return {"q": 1}

    monkeypatch.setattr("atst.sources.baidu.adapters.BaiduSource", Fake)
    assert BaiduSessionMixin.baidu_minute("600519") == ["m"]
    assert BaiduSessionMixin.baidu_ticks("600519", limit=5) == ["t"]
    assert BaiduSessionMixin.baidu_quote("600519") == {"q": 1}
    assert [inst.closed for inst in instances] == [True, True, True]
    assert instances[1].calls == [("fetch_ticks", ("600519",), {"limit": 5})]


def test_baidu_valuation_history_dedupes_indicators(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str]] = []

    def fetch_valuation_history(
        symbol: str, *, indicator: str, period: str
    ) -> list[dict[str, Any]]:
        seen.append((symbol, indicator, period))
        return [{"indicator": indicator}]

    _install(
        monkeypatch,
        "atst.sources.baidu.adapters",
        "BaiduSource",
        fetch_valuation_history=fetch_valuation_history,
    )
    out = BaiduSessionMixin.baidu_valuation_history(
        "sh600519", indicators=("总市值", "总市值", "市净率"), period="近三年"
    )
    assert list(out) == ["总市值", "市净率"]
    assert out["总市值"] == [{"indicator": "总市值"}]
    assert seen == [
        ("sh600519", "总市值", "近三年"),
        ("sh600519", "市净率", "近三年"),
    ]


def test_fund_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.adapters_fund",
        "FundSource",
        fetch_nav_history=[{"date": "2026-11-20"}],
        fetch_estimate={"code": "161725"},
        fetch_fund_list=[{"code": "161725"}],
    )
    assert BaiduSessionMixin.fund_nav_history("161725", page_size=20, page_index=2) == [
        {"date": "2026-11-20"}
    ]
    assert BaiduSessionMixin.fund_estimate("161725") == {"code": "161725"}
    assert BaiduSessionMixin.fund_list() == [{"code": "161725"}]


def test_index_constituents_and_margin_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.eastmoney.adapters",
        "EastmoneyIndexConstituentsSource",
        fetch_constituents=[{"code": "600519"}],
    )
    _install(
        monkeypatch,
        "atst.sources.eastmoney.adapters",
        "EastmoneyMarginSource",
        fetch_margin=[{"code": "600519"}],
    )
    assert BaiduSessionMixin.index_constituents("sh000300") == [{"code": "600519"}]
    assert BaiduSessionMixin.margin("600519", days=3, page=2, size=50) == [{"code": "600519"}]


def test_suggest_and_search_symbols_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"code": "600519", "name": "贵州茅台", "market": "sh", "symbol": "sh600519"},
        {"code": "000001", "name": "平安银行", "market": "sz", "symbol": "sz000001"},
        {
            "code": "00700",
            "name": "腾讯",
            "market": "hk",
            "symbol": "hk00700",
            "display": "腾讯控股",
        },
    ]
    _install(monkeypatch, "atst.sources.sina.adapters", "SuggestSource", fetch_suggest=rows)

    assert BaiduSessionMixin.suggest("mt", limit=3) == rows
    # 指定市场：上游按 limit*4 拉取，本地过滤后再截断
    out = BaiduSessionMixin.search_symbols("x", limit=2, market="sh")
    assert out == [
        {
            "code": "600519",
            "name": "贵州茅台",
            "market": "sh",
            "symbol": "sh600519",
            "display": "贵州茅台(600519)",
        }
    ]
    # 已有 display 的行不被覆盖
    assert BaiduSessionMixin.search_symbols("x", limit=5, market="hk")[0]["display"] == "腾讯控股"
    # 无匹配市场返回空
    assert BaiduSessionMixin.search_symbols("x", market="bj") == []
    # 不指定市场时，凑够 limit 立即截断
    assert [r["code"] for r in BaiduSessionMixin.search_symbols("x", limit=2)] == [
        "600519",
        "000001",
    ]


def test_index_list_is_offline_catalog() -> None:
    """指数目录是本地静态数据，带 name/symbol/market/code 四列。"""
    rows = BaiduSessionMixin.index_list()
    assert {"name": "上证指数", "symbol": "sh000001", "market": "sh", "code": "000001"} in rows
    assert all(len(r["symbol"]) == 8 for r in rows)


def test_wencai_forwards_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    instances: list[_Fake] = []

    class Fake(_Fake):
        def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
            super().__init__(client=client, **kwargs)
            instances.append(self)

        def fetch_strategy(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(("fetch_strategy", (query,), kwargs))
            return [{"query": query}]

    monkeypatch.setattr("atst.sources.wencai.WencaiSource", Fake)
    out = BaiduSessionMixin.wencai("macd金叉", page=2, limit=30, cookie="hexin-v=1")
    assert out == [{"query": "macd金叉"}]
    assert instances[0].kwargs["cookie"] == "hexin-v=1"
    assert instances[0].calls == [("fetch_strategy", ("macd金叉",), {"page": 2, "limit": 30})]
    assert instances[0].closed is True


def test_upstream_error_propagates_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    instances: list[_Fake] = []

    class Fake(_Fake):
        def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
            super().__init__(client=client, **kwargs)
            instances.append(self)

        def fetch_quote(self, symbol: str) -> dict[str, Any]:
            raise WebSourceError("baidu upstream down", context={"source": "baidu"})

    monkeypatch.setattr("atst.sources.baidu.adapters.BaiduSource", Fake)
    with pytest.raises(WebSourceError, match="upstream down") as caught:
        BaiduSessionMixin.baidu_quote("600519")
    assert caught.value.code == "E7000"
    assert instances[0].closed is True


def test_empty_payload_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.eastmoney.adapters",
        "EastmoneyIndexConstituentsSource",
        fetch_constituents=[],
    )
    _install(monkeypatch, "atst.sources.adapters_fund", "FundSource", fetch_nav_history=[])
    assert BaiduSessionMixin.index_constituents("000300") == []
    assert BaiduSessionMixin.fund_nav_history("161725") == []
