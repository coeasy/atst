# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""tstdx.profile —— 数据规格探测与市场预设（Tier D item D3）。

本包是 :mod:`tstdx.reader.profile` 的**上层包装**，不重复定义任何已有类型。
主要新增：

* :func:`detect` —— 六步证据化探测管线，输出 :class:`DetectionResult`
  （含候选排名与每步证据）；
* :mod:`tstdx.profile.presets` —— 9 个常见市场/品种预设（SH_A / SZ_A /
  BJ_A / SH_FUND / SZ_FUND / SH_BOND / SZ_BOND / EX_GOLD / EX_FUTURES）。

从 :mod:`tstdx.reader.profile` 复用的类型（**不要在此重新定义**）：

* :class:`DataProfile` —— 完整数据规格档案；
* :func:`detect_profile` —— 无证据输出的快速探测入口；
* :class:`ProfileDetector` —— 六步探测引擎（reader 侧实现）；
* :class:`Market` / :class:`AssetClass` / :class:`Period` /
  :class:`PriceEncoding` / :class:`VolumeUnit` / :class:`AmountUnit` /
  :class:`TimeEncoding` —— 枚举常量类；
* :data:`BUILTIN_PROFILES` —— 内置档案表。

用法示例
--------
.. code-block:: python

    from tstdx.profile import detect, DataProfile, detect_profile, get_preset
    from tstdx.profile.presets import match_preset

    # 方式一：带证据的六步管线
    result = detect(raw_bytes, hint_market=1, hint_period="day")
    print(result.profile, result.confidence, len(result.evidence))

    # 方式二：沿用 reader 侧快速入口
    profile: DataProfile = detect_profile(raw_bytes)

    # 方式三：按代码前缀查预设
    preset = match_preset("600519", market=1)   # → MarketPreset("SH_A")
    preset = get_preset("SH_FUND")
"""

from __future__ import annotations

# 复用 reader.profile 的类型（不重复定义）
from ..reader.profile import (  # noqa: F401
    BUILTIN_PROFILES,
    AmountUnit,
    AssetClass,
    DataProfile,
    Market,
    Period,
    PriceEncoding,
    ProfileDetector,
    TimeEncoding,
    VolumeUnit,
    detect_profile,
    get_profile,
)

# 本包新增
from .detect import (  # noqa: F401
    CONFIDENCE_THRESHOLD,
    DetectionResult,
    StepEvidence,
    detect,
)
from .presets import (  # noqa: F401
    PRESETS,
    MarketPreset,
    get_preset,
    list_preset_names,
    match_preset,
)

__all__ = [
    # 复用自 reader.profile
    "DataProfile",
    "detect_profile",
    "get_profile",
    "ProfileDetector",
    "BUILTIN_PROFILES",
    "Market",
    "AssetClass",
    "Period",
    "PriceEncoding",
    "VolumeUnit",
    "AmountUnit",
    "TimeEncoding",
    # 本包新增：detect
    "detect",
    "DetectionResult",
    "StepEvidence",
    "CONFIDENCE_THRESHOLD",
    # 本包新增：presets
    "MarketPreset",
    "PRESETS",
    "get_preset",
    "list_preset_names",
    "match_preset",
]
