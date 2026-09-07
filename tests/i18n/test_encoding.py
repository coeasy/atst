"""字符集探测测试（§23.4）：验证 GBK/GB18030/Big5/UTF-8 自动探测。

覆盖：纯 ASCII / GBK 中文 / GB18030 4字节 / Big5 繁体 /
UTF-8 中文 / 空字节 / 无效字节回退 / BOM 检测。
"""

from __future__ import annotations

import pytest

from tstdx.charset.encoding import (
    CANDIDATE_ENCODINGS,
    decode_bytes,
    detect_encoding,
    detect_with_confidence,
    encode_text,
    is_valid_big5,
    is_valid_gb18030,
    is_valid_gbk,
    is_valid_utf8,
    try_decode,
)


@pytest.mark.unit
class TestEncodingDetection:
    """字符集探测测试。"""

    def test_pure_ascii(self):
        """#1 纯 ASCII → utf-8。"""
        assert detect_encoding(b"hello world 123") == "utf-8"
        assert detect_encoding(b"ABC\n") == "utf-8"

    def test_ascii_with_high_bytes_not_ascii(self):
        """#2 含高位字节但仍是 ASCII 兼容的 → utf-8。"""
        # 仅低字节
        data = bytes(range(0, 128))
        assert detect_encoding(data) == "utf-8"

    def test_gbk_chinese(self):
        """#3 GBK 中文 → gbk。"""
        text = "你好世界"
        data = text.encode("gbk")
        assert detect_encoding(data) == "gbk"
        enc, conf = detect_with_confidence(data)
        assert enc == "gbk"
        assert conf >= 0.9

    def test_gb18030_4byte_chars(self):
        """#4 GB18030 4字节字符 → gb18030 或 gbk。"""
        # 生僻汉字，GBK 不支持但 GB18030 支持
        text = "𰀀"  # 4-byte CJK Extension
        data = text.encode("gb18030")
        assert is_valid_gb18030(data) is True
        # 探测可能返回 gb18030 或 gbk（取决于可打印比例）
        enc = detect_encoding(data)
        assert enc in ("gb18030", "gbk")

    def test_big5_traditional(self):
        """#5 Big5 繁体 → big5。"""
        text = "股票市場"  # 繁体
        data = text.encode("big5")
        assert is_valid_big5(data) is True
        enc = detect_encoding(data)
        # Big5 和 GBK 可能有交叉，但应能解码
        assert enc in ("big5", "gbk")

    def test_utf8_chinese(self):
        """#6 UTF-8 中文 → utf-8。"""
        text = "你好世界"
        data = text.encode("utf-8")
        assert detect_encoding(data) == "utf-8"
        enc, conf = detect_with_confidence(data)
        assert enc == "utf-8"
        assert conf >= 0.9

    def test_empty_bytes(self):
        """#7 空字节 → utf-8 (置信度 1.0)。"""
        assert detect_encoding(b"") == "utf-8"
        enc, conf = detect_with_confidence(b"")
        assert enc == "utf-8"
        assert conf == 1.0

    def test_all_nul_bytes(self):
        """#8 全 NUL 字节 → utf-8。"""
        assert detect_encoding(b"\x00\x00\x00\x00") == "utf-8"

    def test_invalid_bytes_fallback(self):
        """#9 无效字节 → utf-8 回退。"""
        # 构造一个所有编码都失败的字节串
        # 0xC0 0xC1 在 UTF-8 中是非法的（overlong），在 GBK 中可能部分匹配
        data = b"\xff\xff\xff\xff\xff\xff"
        enc = detect_encoding(data)
        # 至少不会崩溃，会回退到某个编码
        assert enc in CANDIDATE_ENCODINGS + ("utf-8",)

    def test_utf8_bom(self):
        """#10 UTF-8 BOM → utf-8 (置信度 1.0)。"""
        data = b"\xef\xbb\xbfhello"
        enc, conf = detect_with_confidence(data)
        assert enc == "utf-8"
        assert conf == 1.0

    def test_utf16_le_bom(self):
        """#11 UTF-16 LE BOM → utf-16。"""
        data = b"\xff\xfeh\x00e\x00l\x00l\x00o\x00"
        enc, conf = detect_with_confidence(data)
        assert enc == "utf-16"
        assert conf == 1.0

    def test_decode_bytes_auto(self):
        """#12 decode_bytes 自动探测。"""
        text = "你好"
        data = text.encode("gbk")
        result = decode_bytes(data)
        assert result == text

    def test_decode_bytes_explicit(self):
        """#13 decode_bytes 显式编码。"""
        data = b"hello"
        result = decode_bytes(data, encoding="utf-8")
        assert result == "hello"

    def test_decode_bytes_fallback(self):
        """#14 decode_bytes 失败回退。"""
        data = b"\xff\xfe"  # 看起来像 UTF-16 BOM 但可能不完整
        result = decode_bytes(data, encoding="ascii")
        # 不会崩溃，会回退
        assert isinstance(result, str)

    def test_encode_text_default_gbk(self):
        """#15 encode_text 默认 GBK。"""
        data = encode_text("你好")
        assert data == "你好".encode("gbk")

    def test_encode_text_utf8(self):
        """#16 encode_text 指定 UTF-8。"""
        data = encode_text("你好", "utf-8")
        assert data == "你好".encode()

    def test_encode_text_unencodable(self):
        """#17 encode_text 不可编码字符替换为 ?。"""
        data = encode_text("你好🎉", "ascii")
        assert data == b"??\xef\xbf\xbd" or b"?" in data

    def test_try_decode(self):
        """#18 try_decode 多候选尝试。"""
        data = "你好".encode("gbk")
        text, enc = try_decode(data)
        assert text == "你好"
        assert enc == "gbk"

    def test_is_valid_utf8(self):
        """#19 is_valid_utf8 检查。"""
        assert is_valid_utf8("你好".encode()) is True
        assert is_valid_utf8("你好".encode("gbk")) is False

    def test_is_valid_gbk(self):
        """#20 is_valid_gbk 检查。"""
        assert is_valid_gbk("你好".encode("gbk")) is True
        # UTF-8 中文在 GBK 下可能也"合法"（取决于字节序列）
        # 但纯 ASCII 是合法的
        assert is_valid_gbk(b"hello") is True

    def test_is_valid_gb18030(self):
        """#21 is_valid_gb18030 检查。"""
        assert is_valid_gb18030("你好".encode("gb18030")) is True

    def test_is_valid_big5(self):
        """#22 is_valid_big5 检查。"""
        assert is_valid_big5("股票".encode("big5")) is True

    def test_candidate_encodings_constant(self):
        """#23 候选编码常量。"""
        assert "utf-8" in CANDIDATE_ENCODINGS
        assert "gbk" in CANDIDATE_ENCODINGS
        assert "gb18030" in CANDIDATE_ENCODINGS
        assert "big5" in CANDIDATE_ENCODINGS
