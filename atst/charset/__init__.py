# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""字符集探测与编解码模块（§23；C2：原 ``atst.i18n`` 更名，
名实一致——本包只做字符集探测，不含本地化字符串）。

子模块
------
:mod:`encoding` — 字符集自动探测（GBK/GB18030/Big5/UTF-8）与编解码工具。

本包为 `docs/archive/GAP_ANALYSIS_v0.md`（档 A 项 A6）的落地模块，
将字符集探测从 :mod:`atst.codec.primitive` 中解耦为独立模块，
供上层按需引用而无需引入整个 codec 基础设施。
"""

from .encoding import (  # noqa: F401
    CANDIDATE_ENCODINGS,
    DEFAULT_PRIORITY,
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
