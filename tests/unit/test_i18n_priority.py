"""i18n/encoding 与 codec.primitive 优先级差异的口径锁定测试（审计 §2-21）。

生产链（reader/web）走 ``codec.primitive.detect_encoding``（GBK 优先）；
``i18n.encoding`` 是独立基础设施（UTF-8 优先）。两个入口对边界样本
（合法 UTF-8 的中文文本）结论相反——本测试把「优先级相反」这件事
与模块文档字符串的双向指认一起锁死，防止未来被无声统一。
"""

from __future__ import annotations

import pytest

import atst.charset.encoding as i18n_enc
from atst.codec.primitive import detect_encoding as primitive_detect

pytestmark = pytest.mark.unit

#: 合法 UTF-8 中文（边界样本：GBK 无法整串解码，但探测优先级决定归属）
_UTF8_CHINESE = "平安银行公告摘要".encode()


class TestPriorityContract:
    def test_module_docstring_declares_opposite_priority(self) -> None:
        doc = i18n_enc.__doc__ or ""
        assert "UTF-8 优先" in doc
        assert "GBK 优先" in doc
        assert "primitive" in doc

    def test_priorities_genuinely_differ_on_ascii_sample(self) -> None:
        """纯 ASCII 边界样本上两个入口结论相反（i18n→utf-8，primitive→gbk）。

        这正是模块注释警告的口径差异：换入口就是换口径。
        """
        ascii_bytes = b"600519,10.00"
        assert i18n_enc.detect_encoding(ascii_bytes) == "utf-8"
        assert primitive_detect(ascii_bytes) == "gbk"

    def test_i18n_detects_utf8_for_pure_utf8_chinese(self) -> None:
        assert i18n_enc.detect_encoding(_UTF8_CHINESE) == "utf-8"

    def test_gbk_bytes_decode_to_gbk_both(self) -> None:
        gbk_bytes = "平安银行".encode("gbk")
        assert i18n_enc.detect_encoding(gbk_bytes) == "gbk"
        assert primitive_detect(gbk_bytes) == "gbk"
