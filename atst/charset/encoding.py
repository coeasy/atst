# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""字符集编解码工具（§23.4，档 A 项 A6）——P1-7 起为 primitive 薄封装。

**本模块不是第二套探测实现（P1-7 探测合一）**。GBK 优先
（``GBK → GB18030 → Big5 → UTF-8``）是全仓字符集探测优先级的唯一
事实源（协议 SSOT），实现只在 :func:`atst.codec.primitive.detect_encoding`
一处；本模块的 :func:`detect_encoding` / :func:`try_decode` 只做委托
转发。历史上本模块曾自带一套 **UTF-8 优先**（``UTF-8 → GBK →
GB18030 → Big5``）的独立探测，与生产链口径相反（审计 H3），已按
方案 P1-7 整体删除——现在全仓只剩 primitive 一套探测优先级，
换入口不再换口径。

公开名（``detect_encoding`` / ``detect_with_confidence`` /
``decode_bytes`` / ``encode_text`` / ``try_decode`` / ``is_valid_*`` /
``CANDIDATE_ENCODINGS`` / ``DEFAULT_PRIORITY``）保持不变，供既有
消费方（``tests/i18n``、``tests/test_bridges``）无痛迁移；语义随
SSOT 收敛，边界样本结论变化如下（不再有 BOM / 纯 ASCII 的 UTF-8
特判分支）：

* 纯 ASCII → ``gbk``（GBK 是 ASCII 超集，GB18030/Big5 亦可解码，
  GBK 在 SSOT 优先级中居首）；
* 合法 UTF-8 中文：能被 GBK 整串解码为可打印文本的（多数短文本）
  → ``gbk``；GBK 无法整串解码的 → 落到 ``utf-8``——这是「全仓一套
  探测优先级」的既定口径（TDX 服务端以 GBK 为主），不是缺陷。

置信度语义（:func:`detect_with_confidence`）同步收敛为两档：
SSOT 选出的编码能**严格解码**整串数据 → ``1.0``；SSOT 回退（无可
打印候选，返回 ``gb18030`` 兜底）→ ``0.3``。旧的 0.6–0.95 分级属于
被删除的独立实现，不再保留。
"""

from __future__ import annotations

from ..codec.primitive import CANDIDATE_ENCODINGS, decode_gbk
from ..codec.primitive import detect_encoding as _primitive_detect_encoding

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

#: 候选字符集与优先级：直接转发 primitive 的 SSOT（GBK 优先），不再自持一份
#:
#: （``CANDIDATE_ENCODINGS`` 由上方 import 原样再导出；
#: ``DEFAULT_PRIORITY`` 由 SSOT 候选表派生，不手抄）
DEFAULT_PRIORITY: dict[str, int] = {enc: i for i, enc in enumerate(CANDIDATE_ENCODINGS)}


# --------------------------------------------------------------------------- #
# 公共 API（委托层）
# --------------------------------------------------------------------------- #
def detect_encoding(data: bytes) -> str:
    """自动探测字节串的字符编码。

    **本函数是委托不是第二实现（P1-7）**：GBK 优先
    （``GBK → GB18030 → Big5 → UTF-8``）是协议探测优先级的 SSOT，
    策略与回退（全部候选不可打印时回退 ``gb18030`` 最宽超集）都在
    :func:`atst.codec.primitive.detect_encoding` 一处定义，这里只转发。

    Parameters
    ----------
    data:
        待检测的原始字节串。

    Returns
    -------
    str
        编码名，如 ``"gbk"``、``"gb18030"``、``"big5"``、``"utf-8"``。

    Examples
    --------
    >>> detect_encoding("你好".encode("gbk"))
    'gbk'
    >>> detect_encoding("平安银行公告摘要".encode("utf-8"))
    'utf-8'
    """
    return _primitive_detect_encoding(data)


def detect_with_confidence(data: bytes) -> tuple[str, float]:
    """自动探测字节串的字符编码，并返回置信度评分。

    选码完全委托 SSOT（:func:`detect_encoding`），本模块只补置信度
    语义——不再是独立的第二套探测：

    - ``1.0``：SSOT 选出的编码可**严格解码**整串数据；
    - ``0.3``：SSOT 回退（无可打印候选，返回 ``gb18030`` 兜底），
      选码无法严格解码。

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
    >>> detect_with_confidence("你好".encode("gbk"))
    ('gbk', 1.0)
    """
    enc = _primitive_detect_encoding(data)
    try:
        data.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return enc, 0.3
    return enc, 1.0


def decode_bytes(data: bytes, encoding: str | None = None) -> str:
    """解码字节串，支持显式指定编码或自动探测。

    探测（``encoding=None`` 时）委托 SSOT，解码委托
    :func:`atst.codec.primitive.decode_gbk` 的回退链（指定码 →
    ``gb18030`` → ``utf-8`` → ``latin-1``，永不失败）及其 NUL/空白
    清理语义（默认在首个 NUL 处截断，适配定长字段 padding）。

    Examples
    --------
    >>> decode_bytes("你好".encode("gbk"))
    '你好'
    >>> decode_bytes("hello".encode("utf-8"), encoding="utf-8")
    'hello'
    """
    enc = detect_encoding(data) if encoding is None else encoding
    return decode_gbk(data, enc)


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
    """尝试用一组候选编码解码字节串，返回解码结果与实际使用的编码。

    选码委托 SSOT（:func:`atst.codec.primitive.detect_encoding` 的
    ``candidates`` 形参）；``candidates`` 缺省用 SSOT 候选表（GBK 优先）。
    解码委托 :func:`atst.codec.primitive.decode_gbk`（``strip_nul="tail"``，
    适配整篇正文场景），回退链保证本函数不抛异常——即使 SSOT 回退到
    ``gb18030`` 兜底也能给出替换文本。

    Examples
    --------
    >>> text, enc = try_decode("你好".encode("gbk"))
    >>> text
    '你好'
    >>> enc
    'gbk'
    """
    enc = _primitive_detect_encoding(
        data, tuple(candidates) if candidates is not None else CANDIDATE_ENCODINGS
    )
    return decode_gbk(data, enc, strip_nul="tail"), enc


# --------------------------------------------------------------------------- #
# 合法性检测
# --------------------------------------------------------------------------- #
def is_valid_utf8(data: bytes) -> bool:
    """检查字节串是否为合法的 UTF-8 序列。

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
