# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""协议层：命令登记（§5）+ 三级解析（L1/L2/L3）+ Spec 驱动。

典型用法::

    from tstdx.protocol import dispatch
    from tstdx.codec.framing import parse_response_header, decode_response_body

    header = parse_response_header(raw[:16])
    frame = decode_response_header_frame(header, raw[16:])
    result = dispatch(frame)
    print(result.tier, result.confidence, len(result.rows))
"""

# 触发全部内置解析器注册
from . import parsers as _parsers  # noqa: F401,E402
from .commands import (  # noqa: F401
    COMMANDS,
    Command,
    Family,
    by_family,
    get_command,
    stats,
    unknown_command_ids,
)
from .generic import (  # noqa: F401
    CandidateSpec,
    ProtocolSniffer,
    get_sniffer,
    infer_record_layout,
    parse_generic,
)
from .registry import (  # noqa: F401
    PARSERS,
    TIER_L1,
    TIER_L2,
    TIER_L3,
    BaseParser,
    ParseResult,
    dispatch,
    get_parser,
    register_parser,
    registered_ids,
)

__all__ = [
    "Family",
    "Command",
    "COMMANDS",
    "get_command",
    "by_family",
    "unknown_command_ids",
    "stats",
    "BaseParser",
    "ParseResult",
    "PARSERS",
    "register_parser",
    "get_parser",
    "registered_ids",
    "dispatch",
    "parse_generic",
    "infer_record_layout",
    "ProtocolSniffer",
    "get_sniffer",
    "CandidateSpec",
    "TIER_L1",
    "TIER_L2",
    "TIER_L3",
]
