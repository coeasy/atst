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
``600000SH``            后缀无分隔
``600000``              纯 6 位代码（按前缀规则推断市场）
``sz000001``            深市
``bj430047``            北交所
``hk00700``             港股
======================  ==========================

归一化输出（canonical）为小写 ``{market}{code}``，如 ``sh600000``。

设计要点
--------
* **类型边界先于缓存**：公开入口先校验 raw/market，再进入只接收字符串的
  LRU cache；list/dict 等不可哈希错误不会从 functools 提前泄漏。
* **确定性**：同一输入永远得到同一 :class:`Symbol`；``000xxx`` 段的
  两市歧义（裸 ``000001`` = 深市平安银行 vs 沪市上证指数）按
  :data:`_SH_INDEX_BARE_CODES` 白名单裁决（默认归深市个股，
  中证/上证系列指数成员归沪），并可用 ``market=`` 显式覆盖。
* **边界明确**：HK/US 可以被统一 symbol engine 解析，但不能冒充 TDX
  0/1 市场；只有 SZ/SH/BJ 可通过 :attr:`Symbol.tdx_market` 进入二进制协议。
* **零依赖**：仅标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

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


class Market:
    """全库 canonical 市场 token；仅 SZ/SH/BJ 映射到 TDX 二进制市场编号。"""

    SH = "sh"
    SZ = "sz"
    BJ = "bj"
    HK = "hk"
    US = "us"

    ALL = (SH, SZ, BJ, HK, US)
    PREFIXES = ALL
    SUFFIXES = ALL


_SH_CODE_PREFIXES = ("60", "68", "50", "51", "52", "56", "58", "11", "9")
_SZ_CODE_PREFIXES = ("00", "30", "12", "15", "16", "18", "159")
_BJ_CODE_PREFIXES = ("43", "83", "87", "88", "92", "4", "8")
_SH_INDEX_BARE_CODES: frozenset[str] = frozenset({"000300", "000852", "000905", "000903"})


SYMBOL_PATTERN = re.compile(
    r"""^\s*
    (?:
        [Uu][Ss][.\-\s:]?(?P<us_code>[A-Za-z.]{1,12})
      | (?:(?P<pre>[a-zA-Z]{2})[.\-\s:]?)?(?P<code>\d{5,6})
        (?:[.\-\s:]?(?P<post>[a-zA-Z]{2}))?
    )
    \s*$""",
    re.X,
)


@dataclass(frozen=True, slots=True)
class Symbol:
    """归一化后的证券符号。"""

    market: str
    code: str

    def __str__(self) -> str:
        return f"{self.market}{self.code}"

    @property
    def canonical(self) -> str:
        return f"{self.market}{self.code}"

    @property
    def prefix_dot(self) -> str:
        return f"{self.market}.{self.code}"

    @property
    def suffix_dot(self) -> str:
        return f"{self.code}.{self.market}"

    @property
    def bare(self) -> str:
        return self.code

    @property
    def tdx_market(self) -> int:
        """TDX 标准市场编号：SZ=0、SH=1、BJ=2；HK/US fail closed。"""

        if self.market == Market.SZ:
            return 0
        if self.market == Market.SH:
            return 1
        if self.market == Market.BJ:
            return 2

        from ..errors import SymbolError

        raise SymbolError(
            f"市场 {self.market!r} 不属于 TDX SZ/SH/BJ 二进制市场",
            context={
                "symbol": self.canonical,
                "market": self.market,
                "allowed_markets": [Market.SZ, Market.SH, Market.BJ],
                "provider_switch_allowed": False,
            },
        )


def _infer_market_from_code(code: str) -> str:
    """从裸代码推断 canonical 市场码。"""

    if len(code) == 5 and code.isdigit():
        return Market.HK
    if code.startswith("000") and code[3:].isdigit() and len(code) == 6:
        if code in _SH_INDEX_BARE_CODES:
            return Market.SH
        return Market.SZ
    for prefix in _BJ_CODE_PREFIXES:
        if code.startswith(prefix) and len(code) == 6:
            return Market.BJ
    for prefix in _SZ_CODE_PREFIXES:
        if code.startswith(prefix) and len(code) == 6:
            return Market.SZ
    for prefix in _SH_CODE_PREFIXES:
        if code.startswith(prefix) and len(code) == 6:
            return Market.SH
    return Market.SH if code and code[0] in "651" else Market.SZ


def _validate_market_token(token: str) -> str | None:
    normalized = token.lower()
    return normalized if normalized in Market.ALL else None


def _require_public_inputs(raw: Any, market: Any) -> tuple[str, str | None]:
    from ..errors import SymbolError

    if not isinstance(raw, str):
        raise SymbolError(
            f"证券代码必须是字符串，收到 {type(raw).__name__}",
            context={"symbol_type": type(raw).__name__},
        )
    if market is not None and not isinstance(market, str):
        raise SymbolError(
            f"market 必须是字符串或 None，收到 {type(market).__name__}",
            context={"market_type": type(market).__name__},
        )
    return raw, market


@lru_cache(maxsize=4096)
def _parse_symbol_cached(raw: str, market: str | None) -> Symbol:
    from ..errors import SymbolError

    s = raw.strip()
    if not s:
        raise SymbolError("证券代码为空", context={"symbol": raw})

    explicit = _validate_market_token(market) if market else None
    if market and explicit is None:
        raise SymbolError(f"未知市场 {market!r}；可选: {Market.ALL}", context={"market": market})

    match = SYMBOL_PATTERN.match(s)
    if match:
        us_code = match.group("us_code")
        if us_code is not None:
            if explicit is not None and explicit != Market.US:
                raise SymbolError(
                    f"显式市场 {explicit!r} 与美股符号 {raw!r} 冲突",
                    context={"symbol": raw, "market": explicit},
                )
            return Symbol(market=Market.US, code=us_code.upper())

        code = match.group("code")
        raw_pre = (match.group("pre") or "").lower()
        raw_post = (match.group("post") or "").lower()
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
        if raw_pre and raw_post and raw_pre != raw_post:
            raise SymbolError(
                f"前后缀市场冲突: {raw!r}", context={"pre": raw_pre, "post": raw_post}
            )

        written_market = raw_pre or raw_post
        if explicit is not None and written_market and explicit != written_market:
            raise SymbolError(
                f"显式 market={explicit!r} 与符号市场 {written_market!r} 冲突",
                context={"symbol": raw, "market": explicit, "written_market": written_market},
            )
        resolved_market = explicit or written_market or _infer_market_from_code(code)
        if resolved_market == Market.HK and len(code) != 5:
            raise SymbolError(
                f"港股代码必须是 5 位数字: {code!r}", context={"symbol": raw, "code": code}
            )
        if resolved_market in (Market.SH, Market.SZ, Market.BJ) and len(code) != 6:
            raise SymbolError(
                f"A/BJ 代码必须是 6 位数字: {code!r}", context={"symbol": raw, "code": code}
            )
        return Symbol(market=resolved_market, code=code)

    raise SymbolError(
        f"无法解析证券代码: {raw!r}",
        context={
            "symbol": raw,
            "expected": "sh600000 / sh.600000 / sh-600000 / sh:600000 / sh 600000",
        },
    )


def parse_symbol(raw: str, *, market: str | None = None) -> Symbol:
    """把已声明书写变种解析为 :class:`Symbol`；类型/未知分隔符统一为 SymbolError。"""

    validated_raw, validated_market = _require_public_inputs(raw, market)
    return _parse_symbol_cached(validated_raw, validated_market)


def normalize_symbol(raw: str, *, market: str | None = None) -> str:
    return parse_symbol(raw, market=market).canonical


def split_symbol(raw: str, *, market: str | None = None) -> tuple[str, str]:
    sym = parse_symbol(raw, market=market)
    return sym.market, sym.code


def to_canonical(raw: str, *, market: str | None = None) -> str:
    return parse_symbol(raw, market=market).canonical


def to_prefix_dot(raw: str, *, market: str | None = None) -> str:
    return parse_symbol(raw, market=market).prefix_dot


def to_suffix_dot(raw: str, *, market: str | None = None) -> str:
    return parse_symbol(raw, market=market).suffix_dot


def to_tdx_market(raw: str, *, market: str | None = None) -> tuple[int, str]:
    """返回 ``(tdx_market_id, code)``；仅支持 SZ/SH/BJ。"""

    sym = parse_symbol(raw, market=market)
    return sym.tdx_market, sym.code


def clear_symbol_cache() -> None:
    _parse_symbol_cached.cache_clear()
