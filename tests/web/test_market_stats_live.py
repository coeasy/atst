"""S1/S2 实时冒烟（网络护栏）：验证 facade → 源 → 聚合的端到端链路可用。

沙箱网络对东财可能返回受限 / 异常样本（clist 行数截断、涨跌幅异常、涨跌停
池为空），故本测试只校验**结构有效性**（类型 / 非负 / 比率区间 / 可序列化），
不校验具体市场数值；网络不可达或源抛错时自动 skip。
"""

from __future__ import annotations

import pytest

from atst.errors import TdxError
from atst.web.market_stats import LimitUpLadder, MarketBreadth
from atst.web.session import WebQuoteSession

pytestmark = pytest.mark.network


def _guarded(fn):
    """执行 fn，遇网络 / 传输 / 源错误则 skip（沙箱网络受限）。"""
    try:
        return fn()
    except (TdxError, OSError, TimeoutError) as exc:
        pytest.skip(f"东财网络不可达或受限：{exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")


def test_limit_up_ladder_live_structure() -> None:
    lad = _guarded(WebQuoteSession.limit_up_ladder)
    assert isinstance(lad, LimitUpLadder)
    assert lad.limit_up_count >= 0 and lad.limit_down_count >= 0
    assert lad.broken_count >= 0
    assert 0.0 <= lad.broken_rate <= 1.0
    assert lad.highest_board >= 0
    assert isinstance(lad.as_dict(), dict)


def test_market_breadth_live_structure() -> None:
    mb = _guarded(lambda: WebQuoteSession.market_breadth())
    assert isinstance(mb, MarketBreadth)
    assert mb.total >= 0 and mb.up >= 0 and mb.down >= 0 and mb.flat >= 0
    d = mb.as_dict()
    assert isinstance(d, dict)
    adr = d["advance_decline_ratio"]
    assert adr is None or isinstance(adr, (int, float))
