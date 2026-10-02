# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""V6 L3 新增三个 Web Provider 的解析锁定（golden 样本 + 契约）。

样本来源：``tests/golden/v6/*.json``，2026-10-02 真机抓取后截断（只留前 3 条）
固化。截断而非整份落盘，是为了让 golden 稳定且可读——响应里的时间戳、热度值
会随时间变化，但**结构**与**字段映射**不会，本文件只断言后者。

不变量：

1. 三个源的解析器对真机样本产出预期结构与字段（改解析不改判据 = 当场红）。
2. 每个源的适配器都能进入 :mod:`atst.web` 的适配器注册表（``_ADAPTER_SPECS``
   与 ``KNOWN_SOURCES`` 双向闭合，由
   :mod:`tests.web.test_registry_consistency` 守着）。
3. 全市场类能力的契约形状：快讯不接受标的，公告/板块类标的可选。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atst.catalog.capability import validate_call
from atst.errors import ValidationError
from atst.web.cninfo.adapters import CninfoSource
from atst.web.ths.adapters import ThsSource
from atst.web.wallstreet.adapters import WallstreetSource

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "v6"


def _load(name: str) -> Any:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 巨潮资讯网
# --------------------------------------------------------------------------- #


def test_cninfo_parses_a_share_announcements() -> None:
    rows = CninfoSource._parse_payload(_load("cninfo_announcements"))
    assert rows, "golden 样本里有公告，解析后不该是空的"
    first = rows[0]
    assert first["symbol"] == "000001"
    assert first["name"] == "平安银行"
    assert first["title"]
    #: adjunctUrl 是相对路径，必须补全成可下载的绝对地址
    assert first["url"] == "http://static.cninfo.com.cn/finalpage/2026-09-30/1225587230.PDF"
    assert first["file_type"] == "PDF"
    assert first["source"] == "cninfo"
    #: 毫秒时间戳必须落成人类可读时间，不能把原始毫秒丢给调用方
    assert first["publish_time"].startswith("2026-09-30")


def test_cninfo_parses_hk_announcements() -> None:
    rows = CninfoSource._parse_payload(_load("cninfo_hk_announcements"))
    assert rows
    assert rows[0]["symbol"] == "02629"
    assert rows[0]["publish_time"].startswith("2026-10-01")


def test_cninfo_market_is_chosen_by_symbol_prefix() -> None:
    """column 由标的市场决定：沪→sse、深→szse、港→hke。"""
    from atst.web.cninfo.adapters import _market_of

    assert _market_of("sh600519") == "sh"
    assert _market_of("600519") == "sh"
    assert _market_of("sz000001") == "sz"
    assert _market_of("000001") == "sz"
    assert _market_of("hk00700") == "hk"
    assert _market_of(None) == ""


# --------------------------------------------------------------------------- #
# 同花顺
# --------------------------------------------------------------------------- #


def test_ths_parses_limit_pool() -> None:
    rows = ThsSource._parse_pool(_load("ths_limit_pool"))
    assert rows
    first = rows[0]
    #: market_id 17=沪、33=深，必须拼出带市场前缀的 symbol
    assert first["symbol"] == "sh600131"
    assert first["code"] == "600131"
    assert first["name"] == "国网信通"
    assert first["change_tag"] == "FIRST_LIMIT"


def test_ths_parses_block_top_and_flattens_members() -> None:
    blocks = ThsSource._parse_blocks(_load("ths_block_top"))
    assert blocks
    assert blocks[0]["code"] == "885431"
    assert blocks[0]["name"] == "新能源汽车"
    assert blocks[0]["limit_up_num"] == 10
    #: 成分股要带上板块归属，摊平后仍能反查它属于哪个板块
    assert blocks[0]["members"]
    assert blocks[0]["members"][0]["symbol"] == "sz002454"


def test_ths_parses_hot_rank() -> None:
    rows = ThsSource._parse_hot_rank(_load("ths_hot_rank"))
    assert rows
    assert rows[0]["rank"] == 1
    assert rows[0]["symbol"] == "sh601127"
    assert rows[0]["name"] == "赛力斯"
    assert rows[0]["hot_value"] == 165041.0
    assert "参股银行" in rows[0]["concepts"]


def test_ths_concept_members_filters_by_block_and_stock(monkeypatch) -> None:
    """``fetch_concept_members`` 的 symbol 分流：板块代码→只留该板块，
    个股代码→只留这只股，二者互斥、不可写成两道互相抵消的过滤（历史 bug：
    板块代码查询会被第二道按个股代码的过滤清空）。"""
    src = ThsSource()
    payload = _load("ths_block_top")
    monkeypatch.setattr(src, "_get", lambda _url: json.dumps(payload))
    block_code = payload["data"][0]["code"]
    stock_code = payload["data"][0]["stock_list"][0]["code"]

    by_block = src.fetch_concept_members(block_code)
    assert by_block, "板块代码查询不该返回空"
    assert all(r["block_code"] == block_code for r in by_block)

    by_stock = src.fetch_concept_members(stock_code)
    assert by_stock, "个股代码查询不该返回空"
    assert all(r["code"] == stock_code for r in by_stock)


def test_ths_concept_members_market_wide_is_capped_by_limit(monkeypatch) -> None:
    src = ThsSource()
    payload = _load("ths_block_top")
    monkeypatch.setattr(src, "_get", lambda _url: json.dumps(payload))
    rows = src.fetch_concept_members(None, limit=3)
    assert len(rows) <= 3


# --------------------------------------------------------------------------- #
# 华尔街见闻
# --------------------------------------------------------------------------- #


def test_wallstreet_parses_lives() -> None:
    rows = WallstreetSource._parse(_load("wallstreet_lives"))
    assert rows
    first = rows[0]
    assert first["text"], "content_text 缺失时应回退到剥标签后的 content"
    assert "<p>" not in first["text"], "正文里的 HTML 标签必须剥掉"
    assert first["publish_time"].startswith("2026-10-02")
    assert "global-channel" in first["channels"]


# --------------------------------------------------------------------------- #
# 契约形状：全市场能力不接受标的
# --------------------------------------------------------------------------- #


def test_breaking_news_rejects_a_symbol() -> None:
    """快讯没有按标的分流的频道，传 symbol 必须在契约层被拒，而不是静默忽略。"""
    with pytest.raises(ValidationError):
        validate_call("wallstreet", "catalog", "breaking_news", ("600519",), {})


def test_market_wide_capabilities_accept_no_symbol() -> None:
    validate_call("wallstreet", "catalog", "breaking_news", (), {"limit": 3})


def test_symbol_optional_capabilities_accept_both_shapes() -> None:
    for cap in ("announcements", "hk_announcements", "theme_attribution", "concept_members"):
        provider = "cninfo" if cap.endswith("announcements") else "ths"
        channel = "catalog"
        validate_call(provider, channel, cap, (), {"limit": 3})
        validate_call(provider, channel, cap, ("000001",), {"limit": 3})


def test_unknown_kwarg_is_still_rejected() -> None:
    """新增能力不能顺带放开未知关键字——那会让调用方的拼写错误静默生效。"""
    with pytest.raises(ValidationError):
        validate_call("wallstreet", "catalog", "breaking_news", (), {"limitt": 3})
