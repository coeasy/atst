# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""F10 资料网关解析器（7615 / TQLEX 文件型协议）。

F10 的资料不是单条命令一次性返回，而是**「列目录 → 分块下载文件 → 解析 GBK 文本」**
的两段式流程：

1. **目录**：用 0x06B9 ``FILE_DOWNLOAD`` 请求一个特殊的「目录文件」，返回
   该证券的 F10 栏目清单（栏目名 + 各栏目对应的文件名 / 偏移）；
2. **正文**：再用 0x06B9 按文件名 + 偏移分块下载每个栏目的 GBK 文本。

本模块做两件事：

* :class:`F10CatalogParser` —— 解析目录清单（⚠️ 布局待 golden 校正）；
* :func:`parse_f10_text` —— 把下载到的 GBK 字节**按【栏目名】分隔符切分**成结构化
  段落，这是 F10 资料最常用、也最确定的处理方式（确定性高、可独立测试）。

.. note::
   F10 正文是 **GBK 文本**，与二进制行情协议完全不同；因此这里不依赖 leb128 /
   tdx_float，直接做字符集解码 + 栏目切分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...codec.primitive import decode_gbk
from ..commands import Family
from ..registry import BaseParser, register_parser

__all__ = [
    "F10CatalogParser",
    "F10Section",
    "parse_f10_text",
    "split_f10_sections",
]

#: 栏目标题正则：匹配形如【公司概况】【财务分析】的方括号标题
_SECTION_RE = re.compile(r"【([^】]{1,20})】")


@dataclass
class F10Section:
    """一个 F10 栏目段落。"""

    title: str
    text: str


def split_f10_sections(raw: str) -> list[F10Section]:
    """把 F10 文本按【栏目名】切分为段落列表。

    没有显式栏目标题的头部内容归入 ``""`` 段落。
    """
    # 先按换行归一
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    positions = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text)]
    if not positions:
        return [F10Section(title="", text=text.strip())]

    sections: list[F10Section] = []
    for i, (pos, title) in enumerate(positions):
        start = pos + len(f"【{title}】")
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        body = text[start:end].strip()
        sections.append(F10Section(title=title, text=body))
    return sections


def parse_f10_text(raw: bytes, encoding: str = "gbk") -> list[F10Section]:
    """把下载到的 F10 字节解析为结构化栏目列表。

    Parameters
    ----------
    raw: 0x06B9 下载到的栏目正文（GBK 字节）。

    Note
    ----
    深审 M26：正文按 ``strip_nul="tail"`` 解码——只剥尾部 NUL，中间 NUL
    （分块下载的数据残留）不再把整篇正文截断到首块。
    """
    text = decode_gbk(raw, encoding, strip_nul="tail")
    return split_f10_sections(text)


# --------------------------------------------------------------------------- #
# 0x0001 F10 目录清单（⚠️ 待 golden 校正）
# --------------------------------------------------------------------------- #
@register_parser(0x0001, family=Family.F10, name="F10_CATALOG", head=2, tier="L2")
class F10CatalogParser(BaseParser):
    """某证券的 F10 栏目目录。

    ⚠️ 响应布局来自公开资料推断：``<H count>`` + 每条
    ``<8s 栏目名(gbk)><8s 文件名(gbk)>``。真实字段顺序以 golden 样本为准。
    """

    RECORD_SIZE = 16

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            title = decode_gbk(reader.bytes(8))
            filename = decode_gbk(reader.bytes(8))
            rows.append({"title": title, "filename": filename})
        return rows
