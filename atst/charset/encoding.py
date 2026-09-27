# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""字符集自动探测与编解码工具（§23.4，档 A 项 A6）。

本模块是**独立**的字符集探测基础设施，不含任何协议业务语义。
从 :mod:`atst.codec.primitive` 中解耦，供上层按需引用。

设计目标
--------
* 支持 GBK / GB18030 / Big5 / UTF-8 四种中文字符集自动探测。
* 兼容 BOM 标记（UTF-8 / UTF-16 LE/BE / UTF-32 LE/BE）。
* 纯标准库实现，零外部依赖。
* 探测结果附带**置信度评分**（0.0 – 1.0），便于上层做条件决策。

探测策略（优先级从高到低）
--------------------------
1. **BOM 检测**：若字节流以 BOM 开头，直接采用 BOM 所标识的编码。
2. **边界情形**：空字节串或全 NUL 字节串 → ``utf-8``（置信度 1.0）。
3. **纯 ASCII**：所有字节 ``< 0x80`` → ``utf-8``（UTF-8 是 ASCII 的超集）。
4. **逐字符集严格解码**：按优先级依次尝试 UTF-8 → GBK → GB18030 → Big5，
   以 ``UnicodeDecodeError`` 判定非法序列。
5. **可打印比例过滤**（≥ 0.8）：拒绝「技术上可解码但结果大量不可打印」的
   误判——典型场景为 UTF-8 多字节序列被误认为 GBK 后产生乱码。
6. **回退**：若所有候选均失败，以 ``utf-8`` + ``errors="replace"`` 兜底。

.. note::
    **与 :mod:`atst.codec.primitive` 的探测优先级相反（双向指认）**：
    本模块按 **UTF-8 优先**（``UTF-8 → GBK → GB18030 → Big5``）；
    而 :func:`atst.codec.primitive.detect_encoding` 按 **GBK 优先**
    （``GBK → GB18030 → Big5 → UTF-8``）。**生产链（reader/web 解析）
    一律走 primitive 的 GBK 优先版**；本模块是独立基础设施，仅供
    不想引入 codec 的上层消费方使用。TDX 场景下两者结论一致
    （GBK 严格解码会因非法字节序列而失败，自然回退；纯 ASCII
    始终 UTF-8），但边界样本（合法 UTF-8 的中文文本）结论相反——
    选择哪个入口就是选择口径，换入口前先对拍。primitive.py 不反向
    指认（避免生产链文档漂移），以本注释为准。
"""

from __future__ import annotations

__all__ = [
    "CANDIDATE_ENCODINGS",
    "DEFAULT_PRIORITY",
    "decode_bytes",
    "detect_encoding",
    "detect_with_confidence",
    "encode_text",
    "is_valid_big5",
    "is_valid_gb18030",
    "is_valid_gbk",
    "is_valid_utf8",
    "try_decode",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 候选字符集（按探测优先级排列）
CANDIDATE_ENCODINGS: tuple[str, ...] = ("utf-8", "gbk", "gb18030", "big5")

#: 默认优先级顺序（数值越小优先级越高）
DEFAULT_PRIORITY: dict[str, int] = {
    "utf-8": 0,
    "gbk": 1,
    "gb18030": 2,
    "big5": 3,
}

#: 可打印比例最低门槛——低于此值视为误判
#: 0.8 与旧版 :func:`atst.codec.primitive._printable_ratio` 一致，
#: 可有效过滤 GB18030 对 Big5 数据的误判（典型 ratio ≈ 0.75）。
_MIN_PRINTABLE_RATIO: float = 0.8

#: UTF-8 BOM 字节序列
_UTF8_BOM: bytes = b"\xef\xbb\xbf"
#: UTF-16 LE BOM
_UTF16_LE_BOM: bytes = b"\xff\xfe"
#: UTF-16 BE BOM
_UTF16_BE_BOM: bytes = b"\xfe\xff"
#: UTF-32 LE BOM
_UTF32_LE_BOM: bytes = b"\xff\xfe\x00\x00"
#: UTF-32 BE BOM
_UTF32_BE_BOM: bytes = b"\x00\x00\xfe\xff"

# BOM 查找表：前缀字节 → 编码名
_BOM_TABLE: list[tuple[bytes, str]] = [
    (_UTF32_LE_BOM, "utf-32"),
    (_UTF32_BE_BOM, "utf-32"),
    (_UTF16_LE_BOM, "utf-16"),
    (_UTF16_BE_BOM, "utf-16"),
    (_UTF8_BOM, "utf-8"),
]


# --------------------------------------------------------------------------- #
# 公共 API
# --------------------------------------------------------------------------- #
def detect_encoding(data: bytes) -> str:
    """自动探测字节串的字符编码。

    按优先级依次尝试 UTF-8 → GBK → GB18030 → Big5 严格解码，
    以可打印比例过滤误判，返回首个通过检测的编码名。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    str
        编码名，如 ``"utf-8"``、``"gbk"``、``"gb18030"``、``"big5"``。
        若所有候选均失败，回退到 ``"utf-8"``。

    Examples
    --------
    >>> detect_encoding("你好".encode("gbk"))
    'gbk'
    >>> detect_encoding("hello".encode("utf-8"))
    'utf-8'
    >>> detect_encoding(b"\\xef\\xbb\\xbf你好")
    'utf-8'
    """
    enc, _conf = detect_with_confidence(data)
    return enc


def detect_with_confidence(data: bytes) -> tuple[str, float]:
    """自动探测字节串的字符编码，并返回置信度评分。

    置信度含义：

    - ``1.0``：确定性判断（BOM 检测、纯 ASCII、边界情形）。
    - ``0.90 – 0.95``：唯一候选编码且可打印比例高。
    - ``0.75 – 0.85``：多候选但首选编码可打印比例良好。
    - ``0.60 – 0.75``：首选编码可打印比例中等。
    - ``0.30``：所有候选均失败，回退 ``utf-8`` + ``errors="replace"``。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    (encoding, confidence)
        ``encoding`` 为编码名字符串，``confidence`` 为 0.0–1.0 的浮点数。

    Examples
    --------
    >>> detect_with_confidence(b"\\xef\\xbb\\xbfhello")
    ('utf-8', 1.0)
    >>> detect_with_confidence("你好".encode("gbk"))
    ('gbk', 0.95)
    """
    # --- 边界情形 ---------------------------------------------------------- #
    if not data:
        return "utf-8", 1.0

    # 全 NUL 字节
    if all(b == 0 for b in data):
        return "utf-8", 1.0

    # --- BOM 检测 ---------------------------------------------------------- #
    bom_enc = _detect_bom(data)
    if bom_enc is not None:
        return bom_enc, 1.0

    # --- 纯 ASCII 检测 ------------------------------------------------------ #
    if all(b < 0x80 for b in data):
        return "utf-8", 1.0

    # --- 逐字符集严格解码 ---------------------------------------------------- #
    valid: list[tuple[str, float]] = []
    for enc in CANDIDATE_ENCODINGS:
        try:
            decoded = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        ratio = _printable_ratio(decoded)
        if ratio >= _MIN_PRINTABLE_RATIO:
            valid.append((enc, ratio))

    if not valid:
        return "utf-8", 0.3

    # 按优先级排序，可打印比例作为次级排序键
    def _sort_key(item: tuple[str, float]) -> tuple[int, float]:
        return (DEFAULT_PRIORITY.get(item[0], 99), -item[1])

    valid.sort(key=_sort_key)
    best_enc, best_ratio = valid[0]

    # --- 置信度计算 ---------------------------------------------------------- #
    if len(valid) == 1:
        # 唯一候选
        confidence = 0.95 if best_ratio >= 0.95 else 0.9
    else:
        # 多候选——按可打印比例判定
        if best_ratio >= 0.95:
            confidence = 0.90
        elif best_ratio >= 0.85:
            confidence = 0.85
        elif best_ratio >= 0.7:
            confidence = 0.75
        else:
            confidence = 0.65

    return best_enc, confidence


def decode_bytes(data: bytes, encoding: str | None = None) -> str:
    """解码字节串，支持显式指定编码或自动探测。

    Parameters
    ----------
    data:
        待解码的原始字节串。
    encoding:
        指定编码名（如 ``"gbk"``）。若为 ``None`` 则自动探测。

    Returns
    -------
    str
        解码后的文本。若指定编码解码失败，回退到 UTF-8 + ``errors="replace"``。

    Examples
    --------
    >>> decode_bytes("你好".encode("gbk"))
    '你好'
    >>> decode_bytes("hello".encode("utf-8"), encoding="utf-8")
    'hello'
    """
    enc = detect_encoding(data) if encoding is None else encoding
    try:
        return data.decode(enc)
    except (UnicodeDecodeError, LookupError):
        # 回退：UTF-8 替换模式
        return data.decode("utf-8", errors="replace")


def encode_text(text: str, encoding: str = "gbk") -> bytes:
    """将文本编码为字节串。

    Parameters
    ----------
    text:
        待编码的文本。
    encoding:
        编码名，默认 ``"gbk"``（TDX 协议标准编码）。

    Returns
    -------
    bytes
        编码后的字节串。无法编码的字符替换为 ``'?'``。

    Examples
    --------
    >>> encode_text("你好", "utf-8")
    b'\\xe4\\xbd\\xa0\\xe5\\xa5\\xbd'
    >>> encode_text("你好", "gbk")
    b'\\xc4\\xe3\\xba\\xc3'
    """
    return text.encode(encoding, errors="replace")


def try_decode(data: bytes, candidates: list[str] | None = None) -> tuple[str, str]:
    """尝试用一组候选编码解码字节串，返回首个成功的结果。

    Parameters
    ----------
    data:
        待解码的原始字节串。
    candidates:
        候选编码列表。若为 ``None`` 则使用默认优先级顺序
        ``["utf-8", "gbk", "gb18030", "big5"]``。

    Returns
    -------
    (text, used_encoding)
        解码文本和实际使用的编码名。若全部候选失败，
        返回 ``("...替换文本...", "utf-8")``。

    Examples
    --------
    >>> text, enc = try_decode("你好".encode("gbk"))
    >>> text
    '你好'
    >>> enc
    'gbk'
    """
    if candidates is None:
        candidates = list(CANDIDATE_ENCODINGS)

    for enc in candidates:
        try:
            decoded = data.decode(enc)
            if _printable_ratio(decoded) >= _MIN_PRINTABLE_RATIO:
                return decoded, enc
        except (UnicodeDecodeError, LookupError):
            continue

    # 所有候选失败
    return data.decode("utf-8", errors="replace"), "utf-8"


# --------------------------------------------------------------------------- #
# 合法性检测
# --------------------------------------------------------------------------- #
def is_valid_utf8(data: bytes) -> bool:
    """检查字节串是否为合法的 UTF-8 序列。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    bool
        若可被 UTF-8 严格解码则为 ``True``。

    Examples
    --------
    >>> is_valid_utf8("你好".encode("utf-8"))
    True
    >>> is_valid_utf8("你好".encode("gbk"))
    False
    """
    try:
        data.decode("utf-8")
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def is_valid_gbk(data: bytes) -> bool:
    """检查字节串是否为合法的 GBK 序列。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    bool
        若可被 GBK 严格解码则为 ``True``。

    Examples
    --------
    >>> is_valid_gbk("你好".encode("gbk"))
    True
    >>> is_valid_gbk("你好".encode("utf-8"))
    False
    """
    try:
        data.decode("gbk")
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def is_valid_gb18030(data: bytes) -> bool:
    """检查字节串是否为合法的 GB18030 序列。

    .. note::
       GB18030 是 GBK 的超集，因此所有合法 GBK 序列也都是合法 GB18030。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    bool
        若可被 GB18030 严格解码则为 ``True``。

    Examples
    --------
    >>> is_valid_gb18030("你好".encode("gb18030"))
    True
    """
    try:
        data.decode("gb18030")
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def is_valid_big5(data: bytes) -> bool:
    """检查字节串是否为合法的 Big5 序列。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    bool
        若可被 Big5 严格解码则为 ``True``。

    Examples
    --------
    >>> is_valid_big5("股票".encode("big5"))
    True
    """
    try:
        data.decode("big5")
        return True
    except (UnicodeDecodeError, LookupError):
        return False


# --------------------------------------------------------------------------- #
# 内部工具函数
# --------------------------------------------------------------------------- #
def _detect_bom(data: bytes) -> str | None:
    """检测字节流开头的 BOM 标记。

    Parameters
    ----------
    data:
        原始字节串。

    Returns
    -------
    str or None
        编码名（如 ``"utf-8"``），若未发现 BOM 则返回 ``None``。
    """
    if not data:
        return None

    for bom_bytes, enc_name in _BOM_TABLE:
        if data.startswith(bom_bytes):
            return enc_name

    return None


def _printable_ratio(text: str) -> float:
    """计算文本中可打印字符的比例。

    用于过滤「技术上可解码但结果大量不可打印」的误判场景。

    Parameters
    ----------
    text:
        解码后的文本。

    Returns
    -------
    float
        0.0 – 1.0 之间的比例值。空字符串返回 1.0。
    """
    if not text:
        return 1.0

    good = 0
    for ch in text:
        if ch.isprintable() or ch == "\x00" or ch in ("\r", "\n", "\t"):
            good += 1

    return good / len(text)
