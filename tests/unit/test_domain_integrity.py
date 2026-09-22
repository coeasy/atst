# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""值域尺子自身的判据：它说"越域"时必须真的越域，且不许把手伸到别人的域里。

第 14 轮（G7）把这把尺子从测试挪进 :mod:`tstdx.domain.integrity`，因为同一条判断
此前只有读源码的人才知道，wire 上的形状与一条干净结果一字不差。挪进生产侧之后，
误伤的代价也跟着升级：一条落在合法行上的告警会把信号淹成噪声，所以正控与负控都要判。
"""

from __future__ import annotations

import pytest

from tstdx.client.core import _standard_market_id
from tstdx.domain.integrity import illegal_code, illegal_market, row_violations, tdx_market_ids

pytestmark = pytest.mark.unit


def test_the_market_domain_is_the_one_the_entry_parser_accepts() -> None:
    """出口尺子用的市场域，必须就是入口解析器接受的那三个编号——两处不许各养一份。

    第 13 轮（G6）在入口把 ``market`` 的字符串写法按 ``_PREFIX_MARKET`` 派生；本条把
    出口那侧钉回同一张表：谁改了市场数量，两边必须一起动。
    """
    ids = tdx_market_ids()
    assert ids == frozenset({0, 1, 2}), f"市场域自己变了：{sorted(ids)}"
    for value in sorted(ids):
        assert _standard_market_id(str(value)) == value
        assert illegal_market(value) is None
    for value in sorted({-1, 3, 48, 52, ord("0")} - set(ids)):
        assert illegal_market(value) is not None, f"{value!r} 本该越域却被放行"


def test_the_ruler_fires_on_the_measured_misalignment_shapes() -> None:
    """正控：2026-09-22 盘中实采与 golden 重放里出现过的四种错位形状，每条都要被认出。"""
    planted = {
        "market": 48,  # ASCII '0' 被当成市场编号读（盘中 600519/000001 各 250 条同形）
        "code": "519\x01",  # 错位记录剩下的半个代码，带着控制字节
        "date": "4231-66-07",  # 日期形状却不是真日历日
        "extra": {"code": ""},  # 嵌套层里的空代码
    }
    problems = row_violations(planted)
    assert len(problems) == 4, problems
    assert row_violations({"market": 1, "code": "600000", "date": "2026-08-18"}) == []


def test_the_ruler_does_not_reach_outside_its_own_domain() -> None:
    """负控：越域不等于"不是 A 股代码"。

    7727 扩展市场族交出港股 5 位代码、商品族交出字母开头的 6 位代码，把它们判成越域
    等于把噪声混进信号——把尺子收窄成 symbol 引擎只需一行改动就会重新长出来，所以这条
    负控要单独钉住。
    """
    for row in (
        {"market": 1, "code": "00700"},  # 港股：7727 的 5 位代码
        {"market": 0, "code": "rb2010"},  # 商品：字母开头的 6 位代码
        {"market": "sh", "code": "600519"},  # canonical token 也在市场域内
    ):
        assert row_violations(row) == [], row


def test_each_checker_states_its_own_reason() -> None:
    """理由要能自己站住：调用方拿到字符串就知道哪里错了，不用回去读代码。"""
    assert "市场" in (illegal_market(48) or "")
    assert illegal_market(1) is None
    assert "代码" in (illegal_code("519\x01") or "")
    assert illegal_code(600000) is not None, "非字符串的 code 也是错位形状，不许静默放行"


@pytest.mark.parametrize("path", ["row", "payload[0]"])
def test_the_violation_path_names_where_the_value_sits(path: str) -> None:
    """嵌套形状里的定位信息是这条告警的全部用处：只说"有一行错了"等于没说。"""
    problems = row_violations({"rows": [{"market": 77}]}, path)
    assert len(problems) == 1
    assert problems[0].startswith(f"{path}.rows[0].market: ")
    assert "77" in problems[0]
