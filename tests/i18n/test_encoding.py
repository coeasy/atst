"""字符集探测测试（§23.4）：验证 GBK/GB18030/Big5/UTF-8 自动探测。

覆盖：纯 ASCII / GBK 中文 / GB18030 4字节 / Big5 繁体 /
UTF-8 中文 / 空字节 / 无效字节回退 / BOM 检测。

P1-7 探测合一：``atst.charset.encoding`` 自带的 UTF-8 优先独立探测已
删除，现为 ``atst.codec.primitive``（GBK 优先，协议 SSOT）的委托薄
封装；边界样本（纯 ASCII / 合法 UTF-8 中文 / BOM / 空串）的期望值
随 SSOT 口径更新，不再是 UTF-8 优先时代的结论。
"""

from __future__ import annotations

import pytest

from atst.charset.encoding import (
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
        """#1 纯 ASCII → gbk（P1-7：GBK 优先 SSOT，charset 版是委托）。"""
        assert detect_encoding(b"hello world 123") == "gbk"
        assert detect_encoding(b"ABC") == "gbk"
        # 换行符不计入 primitive 的可打印比例（3/4=0.75 < 0.8 门槛）→
        # 四候选全被筛掉，SSOT 回退 gb18030 兑底
        assert detect_encoding(b"ABC\n") == "gb18030"

    def test_control_heavy_bytes_fallback(self):
        """#2 控制字符占比过高的低字节流 → SSOT 回退 gb18030。

        委托 primitive（GBK 优先 + 可打印比例 ≥ 0.8 粗筛）：
        ``bytes(range(0, 128))`` 的控制字符把四个候选的可打印比例都压到
        门槛之下，落回最宽超集兑底。
        """
        data = bytes(range(0, 128))
        assert detect_encoding(data) == "gb18030"

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
        """#6 UTF-8 中文：GBK 无法整串解码的样本 → utf-8。

        P1-7 后 GBK 优先是 SSOT：能被 GBK 整串解码成可打印文本的
        UTF-8 中文（如「你好世界」）判 gbk；本测试选 GBK 无法整串
        解码的样本（「平安银行公告摘要」），验证 utf-8 分支可达。
        """
        assert detect_encoding("你好世界".encode()) == "gbk"
        data = "平安银行公告摘要".encode()
        enc, conf = detect_with_confidence(data)
        assert enc == "utf-8"
        assert conf == 1.0

    def test_empty_bytes(self):
        """#7 空字节 → gbk（置信度 1.0）。

        P1-7 后空串对四个候选都可「解码」，GBK 居 SSOT 首位。
        """
        assert detect_encoding(b"") == "gbk"
        enc, conf = detect_with_confidence(b"")
        assert enc == "gbk"
        assert conf == 1.0

    def test_all_nul_bytes(self):
        """#8 全 NUL 字节 → gbk（SSOT 口径，\x00 计入可打印字符）。"""
        assert detect_encoding(b"\x00\x00\x00\x00") == "gbk"

    def test_invalid_bytes_fallback(self):
        """#9 无效字节 → utf-8 回退。"""
        # 构造一个所有编码都失败的字节串
        # 0xC0 0xC1 在 UTF-8 中是非法的（overlong），在 GBK 中可能部分匹配
        data = b"\xff\xff\xff\xff\xff\xff"
        enc = detect_encoding(data)
        # 至少不会崩溃，会回退到某个编码
        assert enc in CANDIDATE_ENCODINGS + ("utf-8",)

    def test_utf8_bom(self):
        """#10 UTF-8 BOM → gbk（置信度 1.0）。

        P1-7 后 SSOT 无 BOM 特判分支：EF BB / BF 68 是合法 GBK 对。
        """
        data = b"\xef\xbb\xbfhello"
        enc, conf = detect_with_confidence(data)
        assert enc == "gbk"
        assert conf == 1.0

    def test_utf16_le_bom(self):
        """#11 UTF-16 LE BOM → gb18030（置信度 0.3）。

        P1-7 后 SSOT 无 BOM 特判分支：GBK 在 trail 0x00 处失败，
        逐候选失败后回退 gb18030 兑底（不可严格解码 → 置信 0.3）。
        """
        data = b"\xff\xfeh\x00e\x00l\x00l\x00o\x00"
        enc, conf = detect_with_confidence(data)
        assert enc == "gb18030"
        assert conf == 0.3

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
