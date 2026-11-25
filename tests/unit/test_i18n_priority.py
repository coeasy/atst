"""charset.encoding 与 codec.primitive 探测合一的口径锁定测试（P1-7）。

P1-7 之前：``charset.encoding`` 自带一套 UTF-8 优先的独立探测，与生产
链 ``codec.primitive``（GBK 优先）优先级相反（审计 H3）。P1-7 之后：
charset 版是 primitive 的**委托薄封装**，不是第二实现——GBK 优先是
全仓唯一探测优先级。本测试把「委托后两入口同口径」与模块文档字符串
的委托声明一起锁死，防止第二套探测实现无声回归。
"""

from __future__ import annotations

import pytest

import atst.charset.encoding as i18n_enc
from atst.codec.primitive import detect_encoding as primitive_detect

pytestmark = pytest.mark.unit

#: 合法 UTF-8 中文（边界样本：GBK 无法整串解码，SSOT 落到 utf-8）
_UTF8_CHINESE = "平安银行公告摘要".encode()


class TestPriorityContract:
    def test_module_docstring_declares_delegation(self) -> None:
        doc = i18n_enc.__doc__ or ""
        assert "GBK 优先" in doc
        assert "primitive" in doc
        # P1-7 的核心声明：charset 版是委托不是第二实现
        assert "委托" in doc and "不是第二套探测实现" in doc

    def test_charset_delegates_to_primitive_on_all_samples(self) -> None:
        """两个入口对边界样本结论一致（P1-7 探测合一的验收口径）。"""
        samples = {
            "ascii": b"600519,10.00",
            "gbk": "平安银行".encode("gbk"),
            "utf8-chinese": _UTF8_CHINESE,
            "empty": b"",
            "bom": b"\xef\xbb\xbfhello",
        }
        for name, raw in samples.items():
            assert i18n_enc.detect_encoding(raw) == primitive_detect(raw), name

    def test_pure_ascii_is_gbk_under_protocol_ssot(self) -> None:
        """纯 ASCII 边界样本上两入口同判 gbk（旧 UTF-8 优先实现已删除）。"""
        assert i18n_enc.detect_encoding(b"600519,10.00") == "gbk"
        assert primitive_detect(b"600519,10.00") == "gbk"

    def test_gbk_bytes_decode_to_gbk_both(self) -> None:
        gbk_bytes = "平安银行".encode("gbk")
        assert i18n_enc.detect_encoding(gbk_bytes) == "gbk"
        assert primitive_detect(gbk_bytes) == "gbk"
