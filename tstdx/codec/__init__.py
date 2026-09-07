# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""编解码层：报文帧（§7.1）、变长数值（§7.2）、字符集（§23.4）。"""

from .framing import (  # noqa: F401
    DEFAULT_7709_SPEC,
    MAX_FRAME_BYTES,
    FrameSpec,
    RequestFrame,
    ResponseFrame,
    SeqGenerator,
    build_request,
    decode_response_body,
    iter_frames,
    parse_response_header,
)
from .primitive import (  # noqa: F401
    CANDIDATE_ENCODINGS,
    BinaryReader,
    BinaryWriter,
    count_guard,
    decode_gbk,
    decode_price,
    decode_varint,
    decode_volume,
    detect_encoding,
    encode_varint,
    get_datetime_from_lc,
    minutes_to_hhmm,
    zlib_compress,
    zlib_decompress,
)

__all__ = [
    "BinaryReader",
    "BinaryWriter",
    "count_guard",
    "decode_varint",
    "encode_varint",
    "decode_price",
    "decode_volume",
    "get_datetime_from_lc",
    "minutes_to_hhmm",
    "decode_gbk",
    "detect_encoding",
    "CANDIDATE_ENCODINGS",
    "zlib_compress",
    "zlib_decompress",
    "FrameSpec",
    "DEFAULT_7709_SPEC",
    "MAX_FRAME_BYTES",
    "RequestFrame",
    "ResponseFrame",
    "SeqGenerator",
    "build_request",
    "parse_response_header",
    "decode_response_body",
    "iter_frames",
]
