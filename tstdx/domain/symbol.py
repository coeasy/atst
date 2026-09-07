# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""统一证券符号引擎（单一事实源）。

全库唯一的符号解析 / 归一化入口。``tstdx.client``、``tstdx.web``、
``tstdx.facade`` 等所有模块一律经由此模块处理证券代码，
**不允许各自再实现一套**——这是「多种书写变种一次归一」的架构保证。

支持的输入变种（大小写不敏感，分隔符可有可无）
-----------------------------------------------
======================  ==========================
输入                    含义
======================  ==========================
``sh600000``            前缀：沪市
``SH600000``            同上（大小写不敏感）
``sh.600000``           前缀 + 点分隔
``600000.sh``           后缀 + 点分隔
``600000SH`` / ``600000SH`` 后缀无分隔
``600000``              纯 6 位代码（按前缀规则推断市场）
``sz000001``            深市
``bj430047``            北交所
``hk00700``             港股
======================  ==========================

归一化输出（canonical）为小写 ``{market}{code}``，如 ``sh600000``。

设计要点
--------
* **单次编译正则 + LRU 缓存**：解析路径零重复编译，热点查询 O(1)。
* **确定性**：同一输入永远得到同一 :class:`Symbol`；``000xxx`` 段的
  两市歧义（裸 ``000001`` = 深市平安银行 vs 沪市上证指数）按
  :data:`_SH_INDEX_BARE_CODES` 白名单裁决（默认归深市个股，
  中证/上证系列指数成员归沪），并可用 ``market=`` 显式覆盖。
* **零依赖**：仅标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "Market",
    "Symbol",
    "parse_symbol",
    "normalize_symbol",
    "split_symbol",
    "to_canonical",
    "to_prefix_dot",
    "to_suffix_dot",
    "to_tdx_market",
    "clear_symbol_cache",
    "SYMBOL_PATTERN",
]


# --------------------------------------------------------------------------- #
# 市场枚举
# --------------------------------------------------------------------------- #
class Market:
    """市场代码（与 TDX 标准市场编号一致的小写字母码）。"""

    SH = "sh"  # 上海（含指数 / A 股 / 基金 / 债券）
    SZ = "sz"  # 深圳
    BJ = "bj"  # 北京证券交易所
    HK = "hk"  # 中国香港
    US = "us"  # 美股（预留）

    #: 全部合法市场码
    ALL = (SH, SZ, BJ, HK, US)
    #: 前缀书写时使用的码（当前与 ALL 一致）
    PREFIXES = ALL

    #: 后缀书写时使用的码（大小写不敏感，统一小写）
    SUFFIXES = ALL


#: 沪市 6 位代码前缀（按 TDX 惯例）
_SH_CODE_PREFIXES = ("60", "68", "50", "51", "52", "56", "58", "11", "9")
#: 深市 6 位代码前缀
_SZ_CODE_PREFIXES = ("00", "30", "12", "15", "16", "18", "159")
#: 北交所 6 位代码前缀
_BJ_CODE_PREFIXES = ("43", "83", "87", "88", "92", "4", "8")

#: ``000xxx`` 段中证 / 上证系列指数裸码白名单 → 沪市。
#:
#: **歧义边界（务必先读）**：``000xxx`` 段在两市同时存在——沪市用它编
#: 中证/上证系列指数，深市用它编主板个股（``000001`` 深市=平安银行，
#: 沪市=上证指数）。早期实现按「后三位 < 100 → 沪市指数」启发（
#: ``_SH_INDEX_MAX_TAIL=100``），这会把深市绝大多数主板个股
#: （000001 平安银行、000002 万科A……）错归沪市，属于
#: 「帧合法但内容错误」级风险（审计 §2-3）。
#:
#: 本库裁决：**裸 ``000001`` 归深市（平安银行）**——需要上证指数时请
#: 显式书写 ``sh000001`` 或传 ``market="sh"``；其余 ``000xxx`` 默认归深，
#: 仅下表中的中证/上证系列指数成员（无深市个股同名冲突，或按行业惯例
#: 取指数义）归沪市：
#:
#: ========  ==========
#: 裸码      含义
#: ========  ==========
#: 000300    沪深 300
#: 000852    中证 1000
#: 000905    中证 500
#: 000903    中证系列指数（中证规模指数族成员）
#: ========  ==========
#:
#: 深审 L8 移除 000010 / 000016：二者在深市存在同名个股
#: （``000010`` 美丽生态、``000016`` 深康佳A），违反本白名单自身准入
#: 规则（「该裸码在深市是否有同名个股」核对）——裸写归深市个股；
#: 需要上证 180 / 上证 50 指数请显式书写 ``sh000010`` / ``sh000016``。
#:
#: 白名单按需增补；新增前先核对「该裸码在深市是否有同名个股」。
_SH_INDEX_BARE_CODES: frozenset[str] = frozenset({"000300", "000852", "000905", "000903"})


# --------------------------------------------------------------------------- #
# 解析正则（单次编译）
# --------------------------------------------------------------------------- #
#: ``(market, code)`` 捕获组：前后缀、点/横杠分隔均可，大小写不敏感。
#: 美股为字母代码（``usAAPL`` / ``usBRK.B``），单列为 ``us`` 前缀 + 字母主体；
#: A 股 / 港股 / 北交所为 5~6 位数字代码。
SYMBOL_PATTERN = re.compile(
    r"""^\s*
    (?:
        [Uu][Ss][.\-\s:]?(?P<us_code>[A-Za-z.]{1,12})  # 美股字母代码（含点，如 BRK.B；大小写不敏感）
      | (?:(?P<pre>[a-zA-Z]{2})[.\-\s:]?)?(?P<code>\d{5,6})
        (?:[.\-\s:]?(?P<post>[a-zA-Z]{2}))?         # 可选后缀
    )
    \s*$""",
    re.X,
)


# --------------------------------------------------------------------------- #
# Symbol 数据类
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Symbol:
    """归一化后的证券符号。"""

    market: str  # "sh" | "sz" | "bj" | "hk" | "us"
    code: str  # 主体：A 股/港股/北交所为数字（"600000"），美股为字母（"AAPL"）

    def __str__(self) -> str:
        return f"{self.market}{self.code}"

    @property
    def canonical(self) -> str:
        """规范形式：``sh600000``。"""
        return f"{self.market}{self.code}"

    @property
    def prefix_dot(self) -> str:
        """前缀点分：``sh.600000``。"""
        return f"{self.market}.{self.code}"

    @property
    def suffix_dot(self) -> str:
        """后缀点分：``600000.sh``。"""
        return f"{self.code}.{self.market}"

    @property
    def bare(self) -> str:
        """裸代码：``600000``。"""
        return self.code

    @property
    def tdx_market(self) -> int:
        """TDX 标准市场编号（0=深，1=沪，2=北交所），供二进制协议使用。

        v5 DC1：北交所从「与深市共用 0」修正为独立市场编号 **2**——
        实测主站 ``0x044E`` 对 market=2 返回 379（北交所合理规模），
        market=0/1 分别为 24194/27904；此前 ``bj→0`` 会把北交所标的
        当深市拉取，代码表/行情/财务全链路静默缺失。
        """
        if self.market == Market.SH:
            return 1
        if self.market == Market.BJ:
            return 2
        return 0


# --------------------------------------------------------------------------- #
# 核心推断
# --------------------------------------------------------------------------- #
def _infer_market_from_code(code: str) -> str:
    """从裸代码推断市场码（全库统一惯例）。

    ``000xxx`` 段先查中证/上证指数裸码白名单（见
    :data:`_SH_INDEX_BARE_CODES` 及其歧义边界注释）：命中 → 沪市指数；
    未命中 → 深市主板个股（裸 ``000001`` 因此归平安银行而非上证指数）。
    其余按前缀表匹配，北交所优先（``92`` 前缀不能落进沪市 ``9``）。
    """
    if len(code) == 5 and code.isdigit():
        return Market.HK  # 港股 5 位代码
    if code.startswith("000") and code[3:].isdigit() and len(code) == 6:
        if code in _SH_INDEX_BARE_CODES:
            return Market.SH
        return Market.SZ
    for p in _BJ_CODE_PREFIXES:
        if code.startswith(p) and len(code) == 6:
            return Market.BJ
    for p in _SZ_CODE_PREFIXES:
        if code.startswith(p) and len(code) == 6:
            return Market.SZ
    for p in _SH_CODE_PREFIXES:
        if code.startswith(p) and len(code) == 6:
            return Market.SH
    # 兜底：6/5/1 开头归沪，其余归深
    return Market.SH if code and code[0] in "651" else Market.SZ


def _validate_market_token(token: str) -> str | None:
    """校验书写中的市场 token，合法返回小写，否则 None。"""
    t = token.lower()
    return t if t in Market.ALL else None


@lru_cache(maxsize=4096)
def parse_symbol(raw: str, *, market: str | None = None) -> Symbol:
    """把任意书写变种的证券符号解析为 :class:`Symbol`。

    Parameters
    ----------
    raw:
        任意形式输入：``sh600000`` / ``SH.600000`` / ``600000.sh``
        / ``600000SH`` / ``600000``。
    market:
        显式指定市场（解决 ``000001`` 两市歧义）；
        ``None`` 时按统一惯例推断。

    Raises
    ------
    tstdx.errors.SymbolError
        输入为空或无法解析。
    """
    from ..errors import SymbolError

    s = (raw or "").strip()
    if not s:
        raise SymbolError("证券代码为空", context={"symbol": raw})

    explicit = _validate_market_token(market) if market else None
    if market and explicit is None:
        raise SymbolError(f"未知市场 {market!r}；可选: {Market.ALL}", context={"market": market})

    m = SYMBOL_PATTERN.match(s)
    if m:
        # 美股字母代码：直接以 us 前缀识别，不走数字推断
        us_code = m.group("us_code")
        if us_code is not None:
            return Symbol(market=Market.US, code=us_code.upper())

        code = m.group("code")
        raw_pre = (m.group("pre") or "").lower()
        raw_post = (m.group("post") or "").lower()
        # 书写了市场 token 但不合法（如 "xx600000"）→ 直接报错
        if raw_pre and raw_pre not in Market.ALL:
            raise SymbolError(
                f"未知市场前缀 {raw_pre!r}；可选: {Market.ALL}",
                context={"symbol": raw, "prefix": raw_pre},
            )
        if raw_post and raw_post not in Market.ALL:
            raise SymbolError(
                f"未知市场后缀 {raw_post!r}；可选: {Market.ALL}",
                context={"symbol": raw, "suffix": raw_post},
            )
        # 前后缀同时出现时必须一致（如 "sh600000.sh" 冗余但一致，宽容接受）
        if raw_pre and raw_post and raw_pre != raw_post:
            raise SymbolError(
                f"前后缀市场冲突: {raw!r}", context={"pre": raw_pre, "post": raw_post}
            )
        mkt = explicit or raw_pre or raw_post or _infer_market_from_code(code)
        return Symbol(market=mkt, code=code)

    # 兜底：剥掉所有非字母数字后重试一次（如 "sh 600 000"）
    compact = re.sub(r"[^0-9a-zA-Z]", "", s)
    if compact != s:
        return parse_symbol(compact, market=market)

    raise SymbolError(
        f"无法解析证券代码: {raw!r}",
        context={"symbol": raw, "expected": "sh600000 / 600000.sh / 600000"},
    )


# --------------------------------------------------------------------------- #
# 便捷 API
# --------------------------------------------------------------------------- #
def normalize_symbol(raw: str, *, market: str | None = None) -> str:
    """归一化为规范形式 ``sh600000``（全库统一符号契约）。"""
    return parse_symbol(raw, market=market).canonical


def split_symbol(raw: str, *, market: str | None = None) -> tuple[str, str]:
    """拆分为 ``(market, code)``，如 ``("sh", "600000")``。"""
    sym = parse_symbol(raw, market=market)
    return sym.market, sym.code


def to_canonical(raw: str, *, market: str | None = None) -> str:
    """→ ``sh600000``。"""
    return parse_symbol(raw, market=market).canonical


def to_prefix_dot(raw: str, *, market: str | None = None) -> str:
    """→ ``sh.600000``。"""
    return parse_symbol(raw, market=market).prefix_dot


def to_suffix_dot(raw: str, *, market: str | None = None) -> str:
    """→ ``600000.sh``。"""
    return parse_symbol(raw, market=market).suffix_dot


def to_tdx_market(raw: str, *, market: str | None = None) -> tuple[int, str]:
    """→ ``(tdx_market_id, code)``，供二进制协议层直接使用。"""
    sym = parse_symbol(raw, market=market)
    return sym.tdx_market, sym.code


def clear_symbol_cache() -> None:
    """清空符号解析缓存（测试 / 热重载用）。"""
    parse_symbol.cache_clear()
