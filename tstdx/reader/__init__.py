# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""本地 vipdoc 数据层（§6）：离线解析 + DataProfile 全数据兼容。"""

from .formats import (  # noqa: F401
    BlockReader,
    DayBarReader,
    FinanceReader,
    MinBarReader,
    read_day_file,
    read_min_file,
    resolve_vipdoc_path,
)
from .profile import (  # noqa: F401
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

__all__ = [
    "DataProfile",
    "ProfileDetector",
    "BUILTIN_PROFILES",
    "detect_profile",
    "get_profile",
    "Market",
    "AssetClass",
    "Period",
    "PriceEncoding",
    "VolumeUnit",
    "AmountUnit",
    "TimeEncoding",
    "DayBarReader",
    "MinBarReader",
    "BlockReader",
    "FinanceReader",
    "read_day_file",
    "read_min_file",
    "resolve_vipdoc_path",
]
