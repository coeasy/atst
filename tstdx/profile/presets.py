# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Market presets：常见市场/品种的命名与代码段登记表（Tier D item D3）。

每个预设是 :class:`MarketPreset` 实例。本表在生产链路上一共被咨询两处，
两处都只按 **市场号 + 代码前缀** 行动：

* :func:`match_preset`（公开入口）——按 ``code_prefixes`` 命中、``market_id``
  裁决，返回预设；
* :mod:`tstdx.profile.detect` 的 ``_match_preset_name``——把 ``hint_market``
  反查成预设名，命中时给首要记录长度候选 +0.1 置信度加权。

**这张表不声明任何解码口径**。影响数值正确性的维度一律由
:class:`~tstdx.reader.profile.DataProfile` 与其探测层承载：探测构造档案走的是
:func:`~tstdx.reader.profile.detect_profile` 的字节探测，从不咨询预设。

本表因此也不得通往档案：快照缩放与文件内缩值是两套口径，中间没有任何换算——
行情侧对黄金/期货惯用 1000，而日线档案 ``future_day`` 记 100，一座把前者填进
后者 ``price_scale`` 的桥就是十分之一价。守这条线（本表只有身份三列、且没有
通往 ``DataProfile`` 的桥）的判据见 ``tests/architecture/test_profile_knob_gates.py``。

两个单位口径本表管不了、内置档案也没写死，只能按发行文件逐一核对，记在这里
当提醒：ETF/LOF 的成交量在部分主站记「股」而非「份」；债券的成交量记「张」，
而一些报表把一张等同一元面值。

``market_id`` 采用 :class:`~tstdx.reader.profile.Market.CODES` 的
**文件/探测链编号**（0=深 1=沪 2=北 71=港 74=美），与 PROTOCOL_SPEC yaml、
:class:`DataProfile.market` 同一口径；**这不是 TDX 二进制协议链的市场号**——
协议帧里北交所复用 0（与深市同段，见下）。EX_GOLD/EX_FUTURES 的 73/75 是
TDX 扩展市场号，不在 ``Market.CODES`` 的五个段内。

**BJ 市场码双轨口径（§5 对齐）**：协议链 BJ=0（TDX 老协议把北交所并入
深市市场号段，0=深/北）；文件链 BJ=2（reader/profile 探测与 PROTOCOL_SPEC
用 2 区分北交所样本，避免与深市混流）。历史缘由：北交所 2021 由三板
精选层平移而来，协议帧沿用旧市场号 0，文件格式层则新开编号。**裁决
单一事实源是 :mod:`tstdx.domain.symbol`**——按代码前缀
（92/43/83/87/88）判定 BJ，从不依赖市场号；协议侧换算走
:data:`tstdx.domain.symbol.Symbol.tdx_market`（1=沪，其余=0）。

9 个开箱预设
------------
================================= ========= ======================
名称              market_id   典型代码前缀
================================= ========= ======================
SH_A              1           600/601/603/605/688
SZ_A              0           000/001/002/003/300/301
BJ_A              2           43/83/87/88/92
SH_FUND           1           510/511/512/513/515/516/517/518/560/588
SZ_FUND           0           15/16/18
SH_BOND           1           01/10/11/12/13/14
SZ_BOND           0           11/12/13/14/15
EX_GOLD           73          au/AU（上期所黄金）
EX_FUTURES        75          cu/ru/al/zn/pb/IF/IH/IC（中金所/上期所）
================================= ========= ======================
"""

from __future__ import annotations

from dataclasses import dataclass

from ..reader.profile import Market

__all__ = [
    "MarketPreset",
    "PRESETS",
    "get_preset",
    "match_preset",
    "list_preset_names",
]


# --------------------------------------------------------------------------- #
# MarketPreset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketPreset:
    """一份市场/品种预设的**身份**：叫什么、市场号、管哪些代码段。

    Attributes
    ----------
    name:
        预设名（如 ``"SH_A"``），用于 :func:`get_preset` 查找，也是
        :mod:`tstdx.profile.detect` 报告里的 ``preset_name``。
    market_id:
        文件/探测链市场编号（口径见模块 docstring）：:func:`match_preset`
        用它裁决市场，探测层用它做先验加权。
    code_prefixes:
        该市场/品种下 6 字节代码串的常见前缀，:func:`match_preset` 唯一的
        匹配依据。
    """

    name: str
    market_id: int
    code_prefixes: tuple[str, ...]


# --------------------------------------------------------------------------- #
# 预设表
# --------------------------------------------------------------------------- #
_PRESETS: dict[str, MarketPreset] = {
    "SH_A": MarketPreset(
        name="SH_A",
        market_id=Market.CODES[Market.SH],
        code_prefixes=("600", "601", "603", "605", "688"),
    ),
    "SZ_A": MarketPreset(
        name="SZ_A",
        market_id=Market.CODES[Market.SZ],
        code_prefixes=("000", "001", "002", "003", "300", "301"),
    ),
    # 文件链 BJ=2；TDX 协议帧把 BJ 并入 0（与深市同段）——见上方双轨口径说明。
    "BJ_A": MarketPreset(
        name="BJ_A",
        market_id=Market.CODES[Market.BJ],
        code_prefixes=("43", "83", "87", "88", "92"),
    ),
    "SH_FUND": MarketPreset(
        name="SH_FUND",
        market_id=Market.CODES[Market.SH],
        code_prefixes=("510", "511", "512", "513", "515", "516", "517", "518", "560", "588"),
    ),
    "SZ_FUND": MarketPreset(
        name="SZ_FUND",
        market_id=Market.CODES[Market.SZ],
        code_prefixes=("15", "16", "18"),
    ),
    "SH_BOND": MarketPreset(
        name="SH_BOND",
        market_id=Market.CODES[Market.SH],
        code_prefixes=("01", "10", "11", "12", "13", "14"),
    ),
    "SZ_BOND": MarketPreset(
        name="SZ_BOND",
        market_id=Market.CODES[Market.SZ],
        code_prefixes=("11", "12", "13", "14", "15"),
    ),
    "EX_GOLD": MarketPreset(name="EX_GOLD", market_id=73, code_prefixes=("au", "AU")),
    "EX_FUTURES": MarketPreset(
        name="EX_FUTURES",
        market_id=75,
        code_prefixes=("cu", "CU", "ru", "al", "AL", "zn", "pb", "IF", "IH", "IC"),
    ),
}

#: 预设表（只读别名）。
PRESETS: dict[str, MarketPreset] = dict(_PRESETS)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_preset_names() -> list[str]:
    """所有预设名称，按字典序。"""
    return sorted(PRESETS)


def get_preset(name: str) -> MarketPreset:
    """按名称取预设。

    Raises
    ------
    KeyError
        名称不存在。
    """
    if name not in PRESETS:
        known = ", ".join(list_preset_names())
        raise KeyError(f"未知 market preset: {name!r}；可用: {known}")
    return PRESETS[name]


def match_preset(code: str, market: int) -> MarketPreset | None:
    """按代码前缀 + 市场编号匹配预设。

    匹配规则（按优先级）：

    1. ``code_prefixes`` 是完整前缀（长度 ≥ 3）时，要求
       ``code.startswith(prefix)`` 精确命中；
    2. 前缀长度为 2 时，允许 ``code.startswith(prefix)``；
    3. 匹配成功的预设中，``market_id == market`` 者优先；
       **市场号全部不符时返回 ``None``**——不再硬塞首个前缀命中者
       （沪深交叉代码如 ``600519`` + market=0 属于上游数据错误，
       硬塞会把错误伪装成有效先验，审计 §3-5）。

    Returns
    -------
    :class:`MarketPreset` 或 ``None``（无匹配 / 市场号不符）。

    Examples
    --------
    >>> match_preset("600519", 1).name
    'SH_A'
    >>> match_preset("000001", 0).name
    'SZ_A'
    >>> match_preset("600519", 0) is None  # 沪深交叉：市场号不符 → None
    True
    """
    if not code:
        return None
    code_s = str(code).strip()

    # 精确前缀匹配（长度 ≥ 3）
    candidates: list[MarketPreset] = []
    for p in PRESETS.values():
        for prefix in p.code_prefixes:
            if len(prefix) >= 3 and code_s.startswith(prefix):
                candidates.append(p)
                break
    if not candidates:
        # 长度 2 的短前缀
        for p in PRESETS.values():
            for prefix in p.code_prefixes:
                if len(prefix) == 2 and code_s.startswith(prefix):
                    candidates.append(p)
                    break
    if not candidates:
        return None

    # 优先市场编号匹配；不符 → None（不再硬塞首候选）
    for p in candidates:
        if p.market_id == market:
            return p
    return None


__all__.append("_PRESETS")
