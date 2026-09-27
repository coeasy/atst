# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""协议解析器注册表与三级分派（§5：协议三层覆盖）。

三级解析策略
------------
``L1``  精确解析
    已知命令由 ``@register_parser`` 注册的具体解析器处理，有 spec、有 golden。
``L2``  通用启发式
    未知/未验证命令交给 :mod:`atst.protocol.generic` 的启发式引擎：
    记录数前缀识别 → 定长记录推测 → 字段类型打分，返回**带置信度**的结构化结果。
``L3``  原始透传
    L2 推测失败时返回原始字节，**永不丢包**，并触发 ProtocolSniffer 归档。

分派入口是 :func:`dispatch`，它保证：任何输入都返回一个 :class:`ParseResult`，
绝不静默丢弃数据。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..codec.framing import ResponseFrame
from ..codec.primitive import BinaryReader
from ..errors import LowConfidenceParse, ParseError, TdxError
from .commands import Family, get_command

logger = logging.getLogger(__name__)

__all__ = [
    "ParseResult",
    "PARSERS",
    "register_parser",
    "get_parser",
    "registered_ids",
    "dispatch",
    "TIER_L1",
    "TIER_L2",
    "TIER_L3",
]

TIER_L1 = "L1"
TIER_L2 = "L2"
TIER_L3 = "L3"

#: L1 失败降级 L2/L3 时附加的可观测告警：让「降级后字段映射不完整」
#: （典型症状：client 侧全 0 Bar）至少能从 ``ParseResult.warnings`` 看出来。
DEGRADE_NOTICE = "精确解析失败已降级启发式，字段映射可能不完整"


# --------------------------------------------------------------------------- #
# 解析结果
# --------------------------------------------------------------------------- #
@dataclass
class ParseResult:
    """统一的解析输出。无论解析到哪一级，结构都一致。"""

    command: int
    name: str
    tier: str
    #: 0.0 ~ 1.0。L1 恒为 1.0；L2 由启发式评分给出；L3 为 0.0
    confidence: float
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: 原始 payload（L3 时唯一有效载荷）
    raw: bytes = b""
    #: 附加元信息（字段推断、样本量、告警等）
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def hex(self) -> str:
        return f"0x{self.command:04x}"

    @property
    def ok(self) -> bool:
        return self.tier == TIER_L1 or self.confidence >= 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.hex,
            "name": self.name,
            "tier": self.tier,
            "confidence": round(self.confidence, 3),
            "count": len(self.rows),
            "rows": self.rows,
            "meta": self.meta,
            "warnings": self.warnings,
        }

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
#: ``(family, msg_id) -> parser class``
PARSERS: dict[tuple[str, int], type[BaseParser]] = {}


def register_parser(
    msg_id: int,
    *,
    family: str = Family.STANDARD,
    name: str | None = None,
    tier: str = TIER_L1,
    head: int = 0,
    need_zip: bool | None = None,
    description: str = "",
) -> Callable[[type[BaseParser]], type[BaseParser]]:
    """解析器注册装饰器。

    Parameters
    ----------
    msg_id:
        命令号。
    family:
        协议族，见 :class:`~atst.protocol.commands.Family`。
    head:
        响应 payload 中位于记录数组之前的固定头部字节数。
    need_zip:
        是否必须解压；``None`` 表示由帧头 ``zip_size != unzip_size`` 决定。

    Example
    -------
    >>> @register_parser(0x052D, name="SECURITY_BARS", head=2)
    ... class SecurityBarsParser(BaseParser):
    ...     def parse_payload(self, reader): ...
    """

    def deco(cls: type[BaseParser]) -> type[BaseParser]:
        cls.MSG_ID = msg_id
        cls.FAMILY = family
        cls.NAME = name or cls.__name__
        cls.TIER = tier
        cls.HEAD = head
        cls.NEED_ZIP = need_zip
        cls.DESCRIPTION = description or cls.__doc__ or ""
        key = (family, msg_id)
        if key in PARSERS:
            existing = PARSERS[key]
            if existing.__name__ == cls.__name__:
                # 幂等：同键同名时保留先注册者，视为等价实现
                # （spec_audit 负责漂移检查）。跨模块同名（不同模块定义了
                # 同名类）时「顶替」不会发生——打 debug log 留痕以便排查
                # 「注册了却没生效」类问题。
                if existing.__module__ != cls.__module__:
                    logger.debug(
                        "解析器幂等顶替未发生：键 %s 已被 %s.%s 占用，"
                        "%s.%s 同名注册被忽略（保留先注册者）",
                        key,
                        existing.__module__,
                        existing.__name__,
                        cls.__module__,
                        cls.__name__,
                    )
                return cls
            raise ValueError(f"重复注册解析器: {key} ({existing.__name__} vs {cls.__name__})")
        PARSERS[key] = cls
        return cls

    return deco


def get_parser(cmd: int, family: str = Family.STANDARD) -> type[BaseParser] | None:
    return PARSERS.get((family, cmd))


def registered_ids(family: str | None = None) -> list[tuple[str, int]]:
    if family is None:
        return sorted(PARSERS.keys())
    return sorted(k for k in PARSERS if k[0] == family)


# --------------------------------------------------------------------------- #
# BaseParser
# --------------------------------------------------------------------------- #
class BaseParser:
    """解析器基类。

    子类只需实现 :meth:`parse_payload`。框架负责：
    跳过固定头部、构造 :class:`BinaryReader`、包装 :class:`ParseResult`、
    异常转译（越界 / 解压失败 → 对应错误类型并保留 raw）。
    """

    MSG_ID: int = 0
    FAMILY: str = Family.STANDARD
    NAME: str = "BASE"
    TIER: str = TIER_L1
    #: 记录数组之前的固定头部字节数（通常是 ``uint16 count``）。
    #: **纯元信息**：框架不自动跳过，由各解析器自行读取，避免重复跳导致错位。
    HEAD: int = 0
    NEED_ZIP: bool | None = None
    DESCRIPTION: str = ""

    def parse(self, frame: ResponseFrame, **ctx: Any) -> ParseResult:
        payload = frame.payload
        # 读游标从 0 开始：各解析器**自己**读取记录数头（``uint16 count``）。
        # HEAD 只作为元信息（供 L2 通用解析、测试与文档使用）——若这里再跳一次
        # 而解析器又读一次 count，就会整体错位 2 字节，症状是首条记录字段全乱。
        reader = BinaryReader(payload)
        # 解析器侧告警缓冲：解析器经 warn_ctx() 写入，统一呈现到
        # ParseResult.warnings（钳制/截断/降级等静默修正必须可观测）。
        warn_buf: list[str] = []
        # 解析状态盒（与告警缓冲同机制：parse_payload 收到的 **ctx 是新 dict，
        # 但共享同一 state 对象）——guarded_count 在此登记声明记录数，供下方
        # 截断自检（§2-17：截断静默丢行必须可观测）。
        state: dict[str, Any] = {}
        # 深审 M8：解析器 → 框架的 meta 回传通道。注意 `**ctx` 拆包是**拷贝**，
        # 解析器 rebind `ctx["_meta"] = {...}` 不会传回（旧通道因此永久失效、
        # 无人使用）；正确用法是往共享对象里写键：`ctx["_meta"]["k"] = v`。
        meta_out: dict[str, Any] = {}
        ctx["_warnings"] = warn_buf
        ctx["_state"] = state
        ctx["_meta"] = meta_out
        try:
            rows = self.parse_payload(reader, **ctx) or []
            meta = dict(reader_meta=reader.pos)
        except LowConfidenceParse:
            raise
        except Exception as exc:
            # 致命错误（已识别出的数据问题）原样抛出：
            # 包装成 ParseError 会丢掉 fatal 标记，导致分派器误降级。
            if getattr(exc, "fatal", False):
                raise
            raise ParseError(
                f"{self.NAME} 解析失败: {exc}",
                context={"command": hex(self.MSG_ID), "payload_len": len(payload)},
                cause=exc,
            ) from exc
        # §2-17 截断自检（SecurityListParser 自洽校验的温和版）：未发生钳制
        # 而实收少于声明 → 循环因 remaining 耗尽中途 break，静默丢行改为记
        # 一条「声明 N 实收 M」。已钳制的场景由 guarded_count 的钳制告警覆盖
        # （其消息已含声明数与容纳上限），不重复告警。
        declared = state.get("declared_count")
        if (
            isinstance(declared, int)
            and declared > 0
            and len(rows) < declared
            and not state.get("count_clamped")
        ):
            warn_buf.append(
                f"记录截断：声明 {declared} 条，实收 {len(rows)} 条（响应不完整或记录布局漂移）"
            )
        meta.update(meta_out)
        if isinstance(declared, int):
            # 声明数上 `meta`：分页侧要拿它区分"真没有历史"（声明 0）与
            # "声明 N 却回 0 个记录字节"（空桩），二者处置完全不同。
            meta["declared_count"] = declared
        return ParseResult(
            command=self.MSG_ID,
            name=self.NAME,
            tier=self.TIER,
            confidence=1.0 if self.TIER == TIER_L1 else 0.9,
            rows=list(rows),  # Sequence→list：parse_payload 允许返回任意 Sequence
            raw=payload,
            meta={**meta, "parser": type(self).__name__},
            warnings=warn_buf,
        )

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> Sequence[dict[str, Any]]:
        raise NotImplementedError

    # -- 便捷工具 ---------------------------------------------------------- #
    #: F-107：这里曾有 ``u16_count(reader)``，体只有 ``return reader.uint16()``，
    #: 零调用点、零点名。计数只有两种**真**形状，都各有归口：越域防护走
    #: :meth:`guarded_count`（约 40 个解析器在用），裸计数直接写 ``reader.uint16()``
    #: （``_std7709_quote`` / ``_std7709_bars`` / ``std7727`` 与 ``tools/codegen`` 模板
    #: 生成的都是这一种）。第三种拼法只会让后来人多一次"该用哪个"的判断题，按 D3 删除。
    @staticmethod
    def warn_ctx(ctx: dict[str, Any], message: str) -> None:
        """向当前解析上下文追加一条 warning（呈现在 ``ParseResult.warnings``）。

        供解析器在「钳制 / 截断 / 降级」等静默修正场景留痕。经
        :meth:`BaseParser.parse` 调用时缓冲必然存在；直接调用
        ``parse_payload`` 时静默丢弃（无处呈现，也不污染调用方 dict）。
        """
        buf = ctx.get("_warnings")
        if isinstance(buf, list):
            buf.append(message)

    def guarded_count(
        self, reader: BinaryReader, ctx: dict[str, Any], min_record_bytes: int
    ) -> int:
        """读取 uint16 记录数并按剩余容量钳制（T2/T3 慢解析防御）。

        声明数（可达 65535）超出 ``remaining`` 按每条 ``min_record_bytes``
        字节可容纳的上限时，收敛到该上限并向 warnings 记一条告警——
        畸形流上的 count 驱动循环迭代次数因此与真实数据量同阶。
        同时把声明数登记到解析状态盒（``ctx["_state"]``），供
        :meth:`BaseParser.parse` 的 §2-17 截断自检使用。
        """
        declared = reader.uint16()
        count = reader.count_guard(declared, min_record_bytes)
        state = ctx.get("_state")
        if isinstance(state, dict):
            state["declared_count"] = declared
            if count != declared:
                state["count_clamped"] = True
        if count != declared:
            self.warn_ctx(
                ctx,
                f"count 失真已钳制：声明 {declared} 条，"
                f"按剩余字节 {min_record_bytes}B/条 只能容纳 {count} 条",
            )
        return count


# --------------------------------------------------------------------------- #
# 分派
# --------------------------------------------------------------------------- #
def dispatch(
    frame: ResponseFrame,
    *,
    family: str = Family.STANDARD,
    allow_generic: bool = True,
    min_confidence: float = 0.0,
    **ctx: Any,
) -> ParseResult:
    """按帧分派到对应解析器，失败自动降级。

    返回顺序：L1 精确解析器 → L2 通用启发式 → L3 原始透传。

    Parameters
    ----------
    **ctx:
        透传给解析器的**请求侧上下文**。TDX 的响应体大量依赖请求参数，
        典型如 K 线的 ``category``（决定日期字段是 uint32 YYYYMMDD 还是
        ``uint16`` lc16 + 分钟数）、``index``（指数尾部多 4 字节涨跌家数）。
        没有上下文就无法正确解析，因此这里是**必须**的通道而非可选装饰。
    """
    cmd = frame.method
    cmd_info = get_command(cmd, family)

    parser_cls = get_parser(cmd, family)
    if parser_cls is not None:
        try:
            result = parser_cls().parse(frame, **ctx)
            if cmd_info is not None:
                result.name = cmd_info.name
            _record_parse(result, family, cmd)
            return result
        except LowConfidenceParse:
            # 置信度不足 → 落入下方 L2/L3 兜底
            pass
        except TdxError as exc:
            # 致命错误（完整性违规）**不降级**：降级会把「已知错误」
            # 换成「看起来像数据的噪声」，比直接报错危险得多。
            if getattr(exc, "fatal", False):
                raise
            # L1 失败不吞异常，但降级到 L2/L3 并保留告警
            err_msg = f"L1 解析失败({type(exc).__name__}): {exc}"
            if not allow_generic:
                raise
            fallback = _generic_or_raw(frame, family, cmd, min_confidence)
            fallback.warnings.insert(0, err_msg)
            fallback.warnings.insert(1, DEGRADE_NOTICE)
            return fallback
        except Exception as exc:
            # P1b/T2 边界收口：解析器逃逸的原生异常（TypeError / ValueError /
            # IndexError / struct.error / AttributeError / OverflowError…）
            # 统一包装为 ParseError——保留 cause 与异常类型，使客户端
            # ``except TdxError`` 一定接得住；TdxError 子类（含 fatal
            # IntegrityViolation）在上面按原语义 re-raise，绝不二次包装。
            raise ParseError(
                f"L1 解析器抛出原生异常 {type(exc).__name__}: {exc}",
                context={
                    "command": hex(cmd),
                    "family": family,
                    "exception_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

    if not allow_generic:
        raise LowConfidenceParse(
            f"命令 {hex(cmd)} 无解析器且已禁用通用解析",
            context={"family": family},
        )
    return _generic_or_raw(frame, family, cmd, min_confidence)


def _generic_or_raw(
    frame: ResponseFrame, family: str, cmd: int, min_confidence: float
) -> ParseResult:
    from .generic import parse_generic  # 延迟导入避免循环

    try:
        result = parse_generic(frame, family=family)
    except TdxError:
        raise
    except Exception as exc:
        # P1b/T2 边界收口（L2 侧）：与 L1 同语义——原生异常统一包装为
        # ParseError，保留 cause 与异常类型。
        raise ParseError(
            f"L2 通用解析抛出原生异常 {type(exc).__name__}: {exc}",
            context={
                "command": hex(cmd),
                "family": family,
                "exception_type": type(exc).__name__,
            },
            cause=exc,
        ) from exc
    if result.confidence < max(min_confidence, 0.5):
        if result.tier == TIER_L3:
            result.warnings.append(
                "L2 置信度不足，已回落 L3 原始透传；建议显式指定 DataProfile 或为该命令补充 spec/golden"
            )
        # 深审 M7：显式传入的 min_confidence（>0.5，来自 profile.min_confidence
        # 配置）此前只加 warning 不拒绝——「最低置信度」形同虚设。低于显式
        # 阈值的结果现在拒绝，调用方可 except LowConfidenceParse 走降级/换源。
        # 默认 min_confidence=0.0 时行为与旧版完全一致。
        if result.confidence < min_confidence:
            raise LowConfidenceParse(
                f"命令 {hex(cmd)} 解析置信度 {result.confidence:.2f} 低于要求 {min_confidence:.2f}",
                context={
                    "command": hex(cmd),
                    "family": family,
                    "confidence": result.confidence,
                    "min_confidence": min_confidence,
                },
            )
    # P#1 归档闭环：registry 模块头与 generic 模块头都承诺「未知/低置信样本交给
    # ProtocolSniffer 归档」，但旧实现零调用点——样本静默丢失。现在接线；归档是旁路
    # 观测，落盘失败仅忽略，绝不影响解析结果。（enabled=False 或已验证 L1 命令时
    # archive 内部直接跳过）
    try:
        from .generic import get_sniffer

        get_sniffer().archive(frame, result, family)
    except Exception:  # noqa: BLE001
        pass
    _record_parse(result, family, cmd)
    return result


def _record_parse(result: ParseResult, family: str, cmd: int) -> None:
    """把解析分派结果记进指标门面（旁路观测，绝不影响解析结果）。

    F-118：``atst_protocol_parse_total`` 与 ``atst_protocol_parse_confidence``
    从注册那天起就被 ``/metrics`` 渲染，可全仓没有任何一处调用 ``record_parse()``
    ——请求侧、流侧、错误侧三族指标都有人喂，唯独解析这一族是空缺，抓取方看到的
    "三级解析分派总次数"永远是空序列。指标写入本身吞异常，这里的 import 也一并
    包住：可观测性不许把一次成功的解析变成失败。
    """
    try:
        from ..observability.metrics import record_parse

        record_parse(
            tier=result.tier,
            family=family,
            command=f"0x{cmd:04x}",
            confidence=result.confidence,
        )
    except Exception:  # noqa: BLE001
        pass
