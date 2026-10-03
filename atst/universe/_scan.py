# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""段表 → 候选代码的枚举。

段是"代码前缀"，``sh600`` 段代表 ``sh600000`` … ``sh600999`` 这 1000 个候选。
tdx 的 ``0x044D SECURITY_LIST`` 已 offline（实测 E3035 多主站无响应），代码表
只能自己枚举 + 探测，所以段表是这套方案的唯一入口。

段表本身在 :data:`atst.universe._classes.ASSET_CLASSES`（每个类别列出自己能
探的段），本模块只负责"把段展开成候选代码"——分开两处是为了让"哪个类别探哪
几段"这件事有单一事实源。
"""

from __future__ import annotations

from collections.abc import Iterator

__all__ = ["iter_candidates", "segment_candidates"]


def segment_candidates(segment: str) -> Iterator[str]:
    """单个段展开成候选代码（``sh600`` → ``sh600000`` … ``sh600999``）。

    段本身**就是**固定的那几位（``sh600``），候选 = 段 + 3 位流水号。早先这里写成
    ``head = code[:-3]``，把 ``sh600`` 截成了 ``sh``，产出的其实是 ``sh000`` …
    ``sh999``——跨段全撞在一起、探测全落空，而 ``--dry-run`` 从不真探，所以它一直
    没人发现。判据钉在 ``tests/unit/test_universe.py::test_segment_candidates_*``。
    """
    code = segment.strip().lower()
    if len(code) < 4 or not code[:-3].isalpha():
        return
    if not code[-3:].isdigit():
        return
    for number in range(1000):
        yield f"{code}{number:03d}"


def iter_candidates(segments: tuple[str, ...] | list[str] | str) -> Iterator[str]:
    """多个段合并成一个候选代码流（去重、按段顺序）。

    >>> list(iter_candidates(("sz127", "sz131")))[:3]
    ['sz127000', 'sz127001', 'sz127002']
    """
    if isinstance(segments, str):
        segments = (segments,)
    seen: set[str] = set()
    for segment in segments:
        for code in segment_candidates(segment):
            if code not in seen:
                seen.add(code)
                yield code
