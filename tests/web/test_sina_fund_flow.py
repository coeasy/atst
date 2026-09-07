"""新浪资金流历史源测试（S5）：个股 ssl_qsfx_zjlrqs / 板块 ssl_bkzj_zjlrqs。

罐头样本为 2026-09-02 live 抓取的真实响应（字段口径以此为准）。
"""

from __future__ import annotations

import json

import pytest

from tstdx.errors import TdxError
from tstdx.web.facade import WebQuoteSession
from tstdx.web.fundflow import SinaFundFlowSource

pytestmark = pytest.mark.unit

# 2026-09-02 live 抓取：daima=sh600519
_STOCK_RAW = [
    {
        "opendate": "2026-09-01",
        "trade": "1299.5000",
        "changeratio": "-0.0000153903",
        "turnover": "25.9163",
        "netamount": "-308661986.4400",
        "ratioamount": "-0.0733546",
        "r0_net": "-115750054.9700",
        "r0_ratio": "-0.02750840",
        "r0x_ratio": "-90.0321",
        "cnt_r0x_ratio": "-2",
        "cate_ra": "0.0797202",
        "cate_na": "1400012001.8600",
    },
    {
        "opendate": "2026-08-31",
        "trade": "1289.1400",
        "changeratio": "-0.00636658",
        "turnover": "16.8226",
        "netamount": "-319719173.0300",
        "ratioamount": "-0.117769",
        "r0_net": "-291246626.2000",
        "r0_ratio": "-0.1072809",
        "r0x_ratio": "-93.3962",
        "cnt_r0x_ratio": "-1",
        "cate_ra": "-0.112094",
        "cate_na": "-1362340842.7700",
    },
]

# 2026-09-02 live 抓取：bankuai=new_dzxx
_BOARD_RAW = [
    {
        "opendate": "2026-09-01",
        "avg_price": "15.2527",
        "avg_changeratio": "-0.00518595",
        "turnover": "381.188",
        "netamount": "-1751388845.6000",
        "ratioamount": "-0.0100353",
        "r0_net": "-738114931.0000",
        "r0_ratio": "-0.00422933",
        "r0x_ratio": "-140.801",
        "cnt_r0x_ratio": "-1",
    }
]


def _src_with(monkeypatch: pytest.MonkeyPatch, payload: object) -> SinaFundFlowSource:
    """构造源并把 HTTP 层替换为固定文本。"""
    src = SinaFundFlowSource()
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(src, "_request_text", lambda url, **kw: text)
    return src


# -- 个股 ------------------------------------------------------------------- #
def test_stock_flow_maps_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src_with(monkeypatch, _STOCK_RAW)
    rows = src.fetch_stock_flow("sh600519", size=2)
    assert len(rows) == 2
    r = rows[0]
    assert r["date"] == "2026-09-01"
    assert r["close"] == 1299.5  # 元，无缩放
    assert r["net_amount"] == pytest.approx(-308_661_986.44)
    assert r["super_large_net"] == pytest.approx(-115_750_054.97)
    assert r["industry_net"] == pytest.approx(1_400_012_001.86)


def test_stock_flow_percentage_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """新浪 changeratio 族为**小数**，须 ×100 转百分数（与东财 f3 口径一致）。"""
    src = _src_with(monkeypatch, _STOCK_RAW)
    r = src.fetch_stock_flow("600519", size=1)[0]
    # -0.0000153903 → -0.001539%
    assert r["change_pct"] == pytest.approx(-0.001539, abs=1e-6)
    # -0.0733546 → -7.33546%
    assert r["net_ratio"] == pytest.approx(-7.33546, abs=1e-5)
    assert r["super_large_ratio"] == pytest.approx(-2.75084, abs=1e-5)


def test_stock_flow_symbol_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """符号归一收口到 domain.symbol：多种书写变种都拼成 sh600519。"""
    seen: list[str] = []

    def _spy(url: str, **kw: object) -> str:
        seen.append(url)
        return "[]"

    src = SinaFundFlowSource()
    monkeypatch.setattr(src, "_request_text", _spy)
    for sym in ("600519", "sh600519", "sh.600519", "600519.SH", "SH600519"):
        src.fetch_stock_flow(sym, size=1)
    assert len(seen) == 5
    assert all("daima=sh600519" in u for u in seen)


def test_stock_flow_pagination_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """必须带 page/num/sort——否则接口忽略分页返回全历史（约 1.1 MB）。"""
    seen: list[str] = []
    src = SinaFundFlowSource()
    monkeypatch.setattr(src, "_request_text", lambda url, **kw: seen.append(url) or "[]")
    src.fetch_stock_flow("sh600519", page=3, size=7)
    assert "page=3" in seen[0] and "num=7" in seen[0] and "sort=opendate" in seen[0]


# -- 板块 ------------------------------------------------------------------- #
def test_board_flow_maps_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src_with(monkeypatch, _BOARD_RAW)
    rows = src.fetch_board_flow("new_dzxx", size=1)
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-09-01"
    assert r["avg_price"] == pytest.approx(15.2527)
    assert r["avg_change_pct"] == pytest.approx(-0.518595, abs=1e-5)
    assert r["net_amount"] == pytest.approx(-1_751_388_845.6)
    # 板块行不应带个股专属字段
    assert "close" not in r and "industry_net" not in r


def test_board_flow_uses_board_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    src = SinaFundFlowSource()
    monkeypatch.setattr(src, "_request_text", lambda url, **kw: seen.append(url) or "[]")
    src.fetch_board_flow("new_dzxx")
    assert "ssl_bkzj_zjlrqs" in seen[0] and "bankuai=new_dzxx" in seen[0]


# -- 边界与错误分诊 --------------------------------------------------------- #
@pytest.mark.parametrize("empty", ["", "null", "[]", "   "])
def test_empty_responses_return_empty(
    monkeypatch: pytest.MonkeyPatch,
    empty: str,
) -> None:
    """板块代码不存在时接口返回 ``null``/``[]``——应为空列表而非抛错。"""
    src = _src_with(monkeypatch, empty)
    assert src.fetch_board_flow("new_notexist") == []


def test_error_payload_raises_deprecated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新浪错误体 ``{"__ERROR":1,...}`` 视为接口下线——与基类策略一致。"""
    from tstdx.errors import SourceDeprecated

    src = _src_with(monkeypatch, '{"__ERROR":1,"__ERRORMSG":"Input error"}')
    with pytest.raises(SourceDeprecated):
        src.fetch_stock_flow("sh600519")


def test_turnover_raw_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """turnover 口径官方未公示（个股/板块量纲不同），原值透出不换算。"""
    src = _src_with(monkeypatch, _STOCK_RAW)
    assert src.fetch_stock_flow("600519")[0]["turnover_raw"] == pytest.approx(25.9163)


# -- live 冒烟（网络护栏：不可达即 skip） ------------------------------------ #
@pytest.mark.network
def test_stock_flow_live_structure() -> None:
    try:
        rows = WebQuoteSession.sina_fund_flow("sh600519", size=3)
    except (TdxError, OSError, TimeoutError) as exc:
        pytest.skip(f"新浪网络不可达或受限：{exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        for key in ("date", "close", "net_amount", "net_ratio"):
            assert key in r
        # 金额应为「元」量级（非万元/亿元）
        assert abs(r["net_amount"]) > 1000


@pytest.mark.network
def test_board_flow_live_structure() -> None:
    try:
        rows = WebQuoteSession.sina_board_fund_flow("new_dzxx", size=2)
    except (TdxError, OSError, TimeoutError) as exc:
        pytest.skip(f"新浪网络不可达或受限：{exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")
    assert isinstance(rows, list)
    if rows:
        assert "avg_price" in rows[0] and "net_amount" in rows[0]
