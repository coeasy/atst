"""统一符号引擎测试（atst.domain.symbol）。

覆盖全部书写变种、大小写、分隔符、市场推断、歧义处理与非法输入。
"""

from __future__ import annotations

import pytest

from atst.domain.symbol import (
    Market,
    normalize_symbol,
    parse_symbol,
    split_symbol,
    to_prefix_dot,
    to_suffix_dot,
    to_tdx_market,
)
from atst.errors import SymbolError


class TestVariants:
    """书写变种全矩阵：同一标的各种写法必须归一到同一结果。"""

    EXPECT = "sh600000"

    @pytest.mark.parametrize(
        "raw",
        [
            "sh600000",  # 前缀
            "SH600000",  # 前缀大写
            "Sh600000",  # 前缀混合
            "sh.600000",  # 前缀 + 点
            "sh-600000",  # 前缀 + 横杠
            "sh 600000",  # 前缀 + 空格
            "sh:600000",  # 前缀 + 冒号
            "600000.sh",  # 后缀
            "600000.SH",  # 后缀大写
            "600000.sh",  # 后缀小写
            "600000.SH".lower(),  # 同上
            "600000SH",  # 后缀无分隔
            "600000sh",  # 后缀无分隔小写
            "600000",  # 纯代码（前缀推断）
            "sh600000.sh",  # 冗余但一致
        ],
    )
    def test_variants(self, raw: str):
        assert normalize_symbol(raw) == self.EXPECT, raw

    def test_sz_bj_hk(self):
        assert normalize_symbol("SZ000651") == "sz000651"
        assert normalize_symbol("000651.sz") == "sz000651"
        assert normalize_symbol("bj430047") == "bj430047"
        assert normalize_symbol("430047") == "bj430047"
        assert normalize_symbol("hk00700") == "hk00700"
        assert normalize_symbol("00700") == "hk00700"
        assert normalize_symbol("00700.HK") == "hk00700"


class TestUsMarket:
    """美股字母代码（``us`` 前缀，Market.US 预留能力落地）。"""

    def test_us_letter_codes(self):
        assert normalize_symbol("usAAPL") == "usAAPL"
        assert normalize_symbol("usaapl") == "usAAPL"  # 大写归一
        assert normalize_symbol("USAAPL") == "usAAPL"
        assert normalize_symbol("us.BRK.B") == "usBRK.B"  # 含点代码
        assert normalize_symbol("usBRK.B") == "usBRK.B"

    def test_us_split_and_object(self):
        assert split_symbol("usAAPL") == ("us", "AAPL")
        s = parse_symbol("usBRK.B")
        assert s.market == Market.US
        assert s.code == "BRK.B"
        assert s.canonical == "usBRK.B"

    def test_us_via_tencent_helper(self):
        from atst.web.base import to_tencent_symbol

        assert to_tencent_symbol("usAAPL") == "usAAPL"
        assert to_tencent_symbol("hk00700") == "hk00700"


class TestInference:
    """市场推断与歧义处理。"""

    def test_index_vs_stock_000(self):
        """000xxx 两市歧义按白名单裁决（审计 §2-3 新契约）：

        * 裸 ``000001`` 归深市平安银行（旧「后三位<100→沪市指数」惯例
          把深市主板个股错归沪市，已废止）；
        * 中证/上证系列指数白名单成员归沪市；
        * 显式 ``market=`` 始终可覆盖。
        """
        assert normalize_symbol("000001") == "sz000001"
        assert normalize_symbol("000001", market="sh") == "sh000001"  # 上证指数显式覆盖
        # 中证指数裸码白名单 → 沪（深市无同名个股的成员）
        for idx_code in ("000300", "000852", "000905", "000903"):
            assert normalize_symbol(idx_code) == f"sh{idx_code}", idx_code
        # 深审 L8：000010/000016 已移出白名单——深市存在同名个股
        # （美丽生态 / 深康佳A），裸写归深市；指数请显式 sh000010/sh000016
        for stock_code in ("000010", "000016"):
            assert normalize_symbol(stock_code) == f"sz{stock_code}", stock_code
        # 其余 000xxx 归深市主板个股
        assert normalize_symbol("000651") == "sz000651"
        assert normalize_symbol("000002") == "sz000002"

    def test_prefix_inference(self):
        assert normalize_symbol("600000") == "sh600000"  # 60 → 沪
        assert normalize_symbol("300750") == "sz300750"  # 30 → 深创业板
        assert normalize_symbol("688981") == "sh688981"  # 68 → 科创板
        assert normalize_symbol("833171") == "bj833171"  # 83 → 北交所

    def test_split_forms(self):
        assert split_symbol("600000.SH") == ("sh", "600000")
        assert to_prefix_dot("SH600000") == "sh.600000"
        assert to_suffix_dot("sh.600000") == "600000.sh"
        assert to_tdx_market("sz000651") == (0, "000651")
        assert to_tdx_market("sh600000") == (1, "600000")

    def test_symbol_object(self):
        s = parse_symbol("600000.SH")
        assert s.market == Market.SH
        assert s.code == "600000"
        assert s.canonical == "sh600000"
        assert str(s) == "sh600000"


class TestInvalid:
    """非法输入必须报 SymbolError。"""

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "xx123456", "sh123abc", "600000.qq"])
    def test_invalid(self, raw: str):
        with pytest.raises(SymbolError):
            normalize_symbol(raw)

    def test_conflict(self):
        with pytest.raises(SymbolError):
            normalize_symbol("sh600000.sz")

    def test_unknown_market_kwarg(self):
        with pytest.raises(SymbolError):
            normalize_symbol("600000", market="xx")
