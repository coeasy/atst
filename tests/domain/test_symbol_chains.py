"""符号单一事实源一致性测试（审计 §2-1 专项回归）。

对审计点名的五个「两链相反市场号」代码，断言所有权域内的两条链
（domain.symbol 与 reader.formats._guess_market / resolve_vipdoc_path）
给出一致结论；协议链（std7709.infer_market）不在本域所有权内，其分歧
单独用 xfail 标注并在汇报中列出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tstdx.domain.symbol import parse_symbol, to_tdx_market
from tstdx.reader.formats import _guess_market, resolve_vipdoc_path

pytestmark = pytest.mark.unit

#: 审计点名的链一致性样本：code → 期望市场（小写市场码）
CASES = {
    "560530": "sh",  # 沪 ETF（旧 reader 链 lstrip 误判边界样本）
    "900901": "sh",  # 沪 B 股
    "118000": "sh",  # 沪科创板转债（11x 段）
    "920001": "bj",  # 北交所新码段（旧 reader 链 "9" 前缀误判沪）
    "000300": "sh",  # 沪深300 指数（白名单；协议链误判深，见 xfail）
}


class TestFiveChainConsistency:
    """domain / reader 两链对同一代码必须同市场。"""

    @pytest.mark.parametrize("code", sorted(CASES))
    def test_domain_vs_reader_guess_market(self, code: str) -> None:
        assert _guess_market(code) == parse_symbol(code).market

    @pytest.mark.parametrize("code", sorted(CASES))
    def test_vipdoc_path_uses_bare_code(self, tmp_path: Path, code: str) -> None:
        sym = parse_symbol(code)
        path = resolve_vipdoc_path(tmp_path, code)
        assert path == tmp_path / sym.market / "lday" / f"{sym.market}{sym.code}.day"
        assert code.lower() not in path.name or code.isdigit()

    @pytest.mark.parametrize("code", sorted(CASES))
    def test_tdx_market_numbering(self, code: str) -> None:
        mkt, _ = to_tdx_market(code)
        # v5 DC1：0=深 1=沪 2=北交所（不再二值化）
        assert mkt == {"sh": 1, "sz": 0, "bj": 2}[CASES[code]]


class TestSuffixFormPath:
    """resolve_vipdoc_path 对后缀式符号不再拼出含前缀的错路径。"""

    def test_suffix_dot_form(self, tmp_path: Path) -> None:
        path = resolve_vipdoc_path(tmp_path, "600519.SH")
        assert path == tmp_path / "sh" / "lday" / "sh600519.day"

    def test_prefix_form_unchanged(self, tmp_path: Path) -> None:
        path = resolve_vipdoc_path(tmp_path, "sh600519")
        assert path == tmp_path / "sh" / "lday" / "sh600519.day"

    def test_bare_with_explicit_market(self, tmp_path: Path) -> None:
        path = resolve_vipdoc_path(tmp_path, "000001", market="sz")
        assert path == tmp_path / "sz" / "lday" / "sz000001.day"

    def test_whitelist_index_explicit_market(self, tmp_path: Path) -> None:
        path = resolve_vipdoc_path(tmp_path, "000300", market="sh")
        assert path == tmp_path / "sh" / "lday" / "sh000300.day"


class TestShIndexWhitelistBoundary:
    """000xxx 段白名单歧义边界（裸 000001 必须归深市平安银行）。"""

    def test_bare_000001_is_sz(self) -> None:
        assert parse_symbol("000001").market == "sz"

    def test_explicit_override_still_works(self) -> None:
        assert parse_symbol("000001", market="sh").canonical == "sh000001"

    def test_whitelist_members_are_sh(self) -> None:
        from tstdx.domain.symbol import _SH_INDEX_BARE_CODES

        for code in sorted(_SH_INDEX_BARE_CODES):
            assert parse_symbol(code).market == "sh", code

    def test_non_whitelist_000xxx_is_sz(self) -> None:
        for code in ("000002", "000100", "000651", "000999"):
            assert parse_symbol(code).market == "sz", code


@pytest.mark.xfail(
    reason="协议链 std7709.infer_market 属 protocol 域（本任务禁改），"
    "其对 000300 仍按旧「后三位<100」惯例归深市；五链收敛待 protocol 域同批修复",
    strict=False,
)
def test_protocol_chain_agrees_on_000300() -> None:
    from tstdx.protocol.parsers.std7709 import infer_market

    assert infer_market("000300") == 1
