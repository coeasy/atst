# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""量纲换算系数单一事实源（§33.7 / P1-8）。

数据契约：**price = 元，volume = 股，amount = 元**。各 Web 源的原生单位并不
统一（腾讯量「手」、额「万元」；东财价「×100 整数」、量「手」；涨停池价
「×1000 整数」……），换算系数历史上以裸字面量分别写在三个地方：

1. :mod:`atst.sources.sources` 里 ``SourceSpec.{price,volume,amount}_scale``；
2. :mod:`atst.sources.normalize` 里各 ``VolumeNormalizer`` 的类属性；
3. 解析层内联缩放（``eastmoney/adapters.py``、``tencent/adapters.py``、
   ``ticks.py``、``fundflow.py``）。

三处各写一遍数字，任一处改动都会让同一份行情在两条路径上差 100 / 10000 倍
（P1-8「量纲系数单源化仍延后」）。本模块是**唯一**允许写出这些数字的地方，
其余模块一律 import 常量；新增源若确需新系数，先在此登记再引用。

``tests/sources/test_scale_single_source.py`` 用源码扫描守住这条约束。
"""

from __future__ import annotations

__all__ = [
    "SHARES",
    "YUAN",
    "HAND_TO_SHARE",
    "WAN_TO_YUAN",
    "YI_TO_YUAN",
    "FEN_INT_TO_YUAN",
    "MILLI_INT_TO_YUAN",
    "CENT_INT_DIVISOR",
    "MILLI_INT_DIVISOR",
    "DECIMAL_TO_PERCENT",
]

#: 源原生即「股」：成交量已是契约单位，identity。
SHARES = 1.0
#: 源原生即「元」：价格 / 成交额已是契约单位，identity。
YUAN = 1.0
#: 手 → 股（1 手 = 100 股）。
HAND_TO_SHARE = 100.0
#: 万元 → 元。
WAN_TO_YUAN = 10_000.0
#: 亿元 → 元（百度分时 ``amount`` 的「亿」后缀）。
YI_TO_YUAN = 100_000_000.0
#: ×100 整数价 → 元（东财 f2/f43 等字段）。
FEN_INT_TO_YUAN = 1.0 / 100.0
#: ×1000 整数价 → 元（东财涨停池 ``p`` 字段）。
MILLI_INT_TO_YUAN = 1.0 / 1000.0
#: 「×100 整数 → 十进制」的**除数**：价格（f43/f60/f174…）、百分比
#: （f3/f170/f301…）、比率（f162 PE / f167 PB）三类字段共用同一算术，
#: 只是语义域不同，故不再按语义拆成三个同值常量。保留「除」而非改乘
#: ``FEN_INT_TO_YUAN``：``x / 100.0`` 与 ``x * 0.01`` 的浮点结果可能差 1 ulp。
CENT_INT_DIVISOR = 100.0
#: ``MILLI_INT_TO_YUAN`` 的倒数，供「除以 1000」的既有调用点保持逐位相同的
#: 浮点结果：``x / 1000.0`` 与 ``x * 0.001`` 并不总是相等（9/1000 = 0.009，
#: 9*0.001 = 0.009000000000000001），故除数由本常量派生，而不是再写一遍 1000。
MILLI_INT_DIVISOR = 1.0 / MILLI_INT_TO_YUAN
#: 小数 → 百分数（资金占比、涨跌幅比率等）。与量纲无关，但同属「不得再写
#: 裸 100」的换算；保留「乘」的逐位结果。
DECIMAL_TO_PERCENT = 100.0
