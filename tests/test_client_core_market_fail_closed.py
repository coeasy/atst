"""市场标识边界（fail-closed）。

本文件原锁定 ``_quote_body`` 的「已验证 0/1 市场 → 反向字节」行为。该行为自
``ac5e9cd``（fix: fail closed on inferred extended request layouts）起被**刻意**
废止：EXTENDED/GOODS/MAC 报价请求体（0x0105/0x0203/0x1301）在仓库协议规格中
声明为 12 字节，而旧实现发出的是 26 字节 0x052D 形状；任一形状都等于「把推断
布局伪装成已验证协议」。因此现在的契约是：

* ``_quote_body`` **无条件** fail-closed（``NotImplementedFeature``），
  市场标识根本不会被消费；
* 市场标识校验 + 反向字节契约迁移到**实时行情（0x0530）**路径
  （``quote_request_market`` / ``build_realtime_quote_body``），由
  ``tests/protocol/test_market_identity_ssot.py`` 逐条锁定；
* ``split_symbol`` 仍保留北交所 ``market=2`` 的身份识别，不得静默夹取为 0/1。

本文件因此守卫的是**迁移本身**：旧的反向字节推断不得回潜，
且已验证的 0/1 身份契约必须仍活在实时行情路径上。
"""

from __future__ import annotations

import pytest

from tstdx.client_core import _quote_body, split_symbol
from tstdx.domain.symbol import to_tdx_market
from tstdx.errors import NotImplementedFeature, ParseError
from tstdx.protocol.parsers.std7709 import build_realtime_quote_body


def test_quote_body_fails_closed_instead_of_emitting_inferred_reverse_bytes() -> None:
    """已验证的 0/1 市场同样 fail-closed——推断布局不得发送。"""
    for code, market in (("000001", 0), ("600519", 1)):
        with pytest.raises(NotImplementedFeature) as exc_info:
            _quote_body(code, market)

        assert exc_info.value.context["commands"] == ["0x0105", "0x0203", "0x1301"]


def test_verified_realtime_identity_owns_the_reverse_byte_contract() -> None:
    """反向字节契约未被 fail-closed 波及，仍由实时行情路径持有。"""
    assert build_realtime_quote_body("000001")[:2] == bytes([0x01, 0x01])
    assert build_realtime_quote_body("600519", market=1)[:2] == bytes([0x01, 0x00])


def test_bj_symbol_market_is_not_silently_clamped_for_unverified_quote_families() -> None:
    """北交所身份保留为 ``market=2``，但 symbol-based 请求 fail-closed。

    ``5f9a916`` 起：身份识别留在 domain 层（``to_tdx_market``），客户端
    ``split_symbol`` 拒绝把未定标的 ``market=2`` 发出去——既**不**静默夹取为
    SZ/SH，也**不**发推断号。
    """
    domain_market, code = to_tdx_market("bj430047")

    assert domain_market == 2
    assert code == "430047"
    with pytest.raises(ParseError) as exc_info:
        split_symbol("bj430047")

    assert exc_info.value.context["market"] == 2
    assert exc_info.value.context["verified_markets"] == [0, 1]


@pytest.mark.parametrize("market", [-1, 2, 99, True, 1.0, "1"])
def test_quote_body_rejects_unverified_or_coercible_market_identity(market) -> None:
    with pytest.raises(NotImplementedFeature):
        _quote_body("600519", market)  # type: ignore[arg-type]
