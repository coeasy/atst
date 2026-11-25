# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""trampoline 驱动器与告警栈深机制（P2-2：自 ``_mixin.py`` 拆出的机制半边）。

「模板 + trampoline」的机制层：op 定义（``_ReqOp`` / ``_CallOp``）、同步 /
异步双驱动器（:meth:`_TrampolineMixin._drive` / ``_adrive``）、告警转发口
（:func:`_forward_decode_caveats`）与栈深口径常数。协议模板本体在
:mod:`atst.client._templates_standard` / :mod:`atst.client._templates_family`；
组合类与既有 import 路径在 :mod:`atst.client._mixin`。

* 每个镜像方法是同步生成器模板（``_t_xxx``），凡原 ``await`` / ``self._req`` /
  ``self.<其他公开方法>`` 的位置改为 ``yield _op_req(...)`` / ``yield
  _op_call(...)`` 挂起；
* 同步端 :meth:`_TrampolineMixin._drive` 逐条执行 op（直接调用传输 seam
  ``_req``），异步端 ``_adrive`` 逐条 ``await``；
* op 执行抛出的异常经 ``gen.throw`` 在**原挂起点**重入模板，模板内的
  ``try/except TdxError``（坏标的隔离、0x054C 回退）语义逐字保留。

**驱动器深度是线程局部（B4/P2-2）**：原为客户端实例上的 ``int`` 属性，同步
``quotes_concurrent`` 的 worker 线程无锁增减同一份实例态，多线程下告警归属
互相串帧。深度本来就是「当前线程调用栈」的属性，改为 ``threading.local``
后各线程各数各的；异步侧单线程事件循环内语义不变。
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from ..diagnostics import WarningCode, record_warning
from ..domain.integrity import row_violations
from ..errors import InternalError
from ..protocol.registry import ParseResult

OutputFormat = str  # "dict" | "tuple" | "dataframe"

# ---- 告警栈深口径（P2-2：魔法常数改为逐帧列举的显式推导；改调用链先改这里）---- #
#
# :func:`~atst.diagnostics.record_warning` 的 ``stacklevel`` 与 ``warnings.warn``
# 同义（其内部 +2 跳过 ``record_warning`` / ``_emit`` 两层转发帧）：归属帧 =
# ``record_warning`` **直接调用方**上方第 ``stacklevel - 1`` 帧。
#
# 「模板直发」路径（模板自己调 ``record_warning``，如 ``_t_bars`` 的截断告警）
# 自归属帧下探到发射点依次是：调用方 → 公共方法壳 → 驱动器 → 模板 gen，共 3 帧：

#: 模板直发路径在发射点上方的帧数。
_DIRECT_FRAMES_ABOVE_TEMPLATE = 3

#: 模板内 ``warnings.warn`` 的 stacklevel：原实现（方法体直呼）为 2 = 调用方；
#: 模板化后多出「驱动器 + 公共方法壳」2 帧，按口径即 3 + 1。只对**单跳**成立——
#: 嵌套 ``_op_call`` 的路径由 :meth:`_TrampolineMixin._warning_stackdepth` 按
#: 驱动器深度显式记账，不再反查调用栈（W6/D1-A：告警归属改显式 ``stackdepth``）。
_TPL_WARN_STACKLEVEL = _DIRECT_FRAMES_ABOVE_TEMPLATE + 1

#: 「转发口」路径（模板经 :meth:`_TrampolineMixin._forward` →
#: :func:`_forward_decode_caveats` 调 ``record_warning``）比直发多 2 帧
#: （``_forward_decode_caveats`` 与 ``_forward``），共 5 帧，故 5 + 1：
_SINGLE_HOP_STACKDEPTH = _DIRECT_FRAMES_ABOVE_TEMPLATE + 3

#: 每多一跳 ``_op_call`` 增加的帧：外层模板 gen 挂起不在栈上，只多出
#: 「该跳的公共方法壳 + 驱动器」两帧。
_FRAMES_PER_NESTED_HOP = 2

#: 驱动器嵌套深度（B4/P2-2：实例属性 → 线程局部）。键名 ``depth``，
#: 未进入驱动器时读数为 0。
_DRIVE_DEPTH = threading.local()


def _current_drive_depth() -> int:
    """当前线程的驱动器嵌套深度（多线程 quotes_concurrent 各数各的，不再串帧）。"""
    return getattr(_DRIVE_DEPTH, "depth", 0)


def _push_drive_depth() -> None:
    """进入驱动器：当前线程深度 +1（``_drive`` / ``_adrive`` 进出成对调用）。"""
    _DRIVE_DEPTH.depth = _current_drive_depth() + 1


def _pop_drive_depth() -> None:
    """退出驱动器：恢复进入前的深度（同线程内进出严格嵌套，-1 即回到进入值）。"""
    _DRIVE_DEPTH.depth = _current_drive_depth() - 1


#: 一条越域告警最多带几条实例。910 行的错位页能产出上千条理由，全塞进 wire 等于
#: 用噪声换信号；计数始终是全文，例子只截前几条。
_DOMAIN_CAVEAT_SAMPLES = 3


def _op_req(cmd: int, body: bytes, **kw: Any) -> _ReqOp:
    """op：发送单命令帧（sync ``self._req`` / async ``await self._req``）。"""
    return _ReqOp(cmd=cmd, body=body, kwargs=kw)


def _op_call(name: str, *args: Any, **kwargs: Any) -> _CallOp:
    """op：调用本客户端另一方法（sync 直呼 / async ``await``），保持
    monkeypatch 可见性与拆分前一致。"""
    return _CallOp(name=name, args=args, kwargs=kwargs)


@dataclass(frozen=True, slots=True)
class _ReqOp:
    """模板挂起的一跳传输请求；密封形状取代旧的裸 tuple（W6/D1-A）。"""

    cmd: int
    body: bytes
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _CallOp:
    """模板挂起的一次同客户端方法调用（sync 直呼 / async ``await``）。"""

    name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


_TrampolineOp = _ReqOp | _CallOp


def _forward_decode_caveats(result: ParseResult, label: str, *, stackdepth: int) -> ParseResult:
    """把"这一页数据有问题"的判断接进结果侧的告警通道——唯一的那个转发口。

    两路判断都从这里过，因为它们在 wire 上是同一个缺陷的两半：

    * 解码层每一页记下的"声明 N 实收 M""钳制""降级为 L3 透传"。袋本身没人读就是
      wire 上的静默（F-51 的第 26 步接线只覆盖了 ``bars`` 一条命令，其余 14 个分派点
      把判断整族丢弃 —— F-63①）。
    * 解出来的行落在库自己声明的值域之外。布局未经真机 golden 锁定的命令（G3 的
      ``0x000F``/``0x0010``）页内字节数对得上，解码层因此一个字都不记，而值已经错位
      ——调用方此前只能靠读源码 docstring 知道（G7）。
    """
    for caveat in result.warnings:
        record_warning(
            WarningCode.DECODE_CAVEAT,
            f"{label}：{caveat}",
            stacklevel=stackdepth,
        )
    offenders = 0
    samples: list[str] = []
    for index, row in enumerate(result.rows):
        if violations := row_violations(row, f"row[{index}]"):
            offenders += 1
            if len(samples) < _DOMAIN_CAVEAT_SAMPLES:
                samples.extend(violations[: _DOMAIN_CAVEAT_SAMPLES - len(samples)])
    if samples:
        record_warning(
            WarningCode.FIELD_OUT_OF_DOMAIN,
            f"{label}：{offenders} 行的字段值落在库自己声明的取值域之外"
            f"（例：{'；'.join(samples)}）——这些字段不可当作可信输入；对记录布局尚未"
            "由真机 golden 锁定的命令（见命令账本的 tier/verified），这正是错位的形状",
            stacklevel=stackdepth,
        )
    return result


#: trampoline 的步数上界（每个 op = 一次 RPC 或一次状态操作）。
#:
#: 驱动器本身是 ``while True``：模板只要持续 ``yield`` 而条件永不收敛，这里就是一条
#: **没有 I/O、没有超时、也不会被 socket 超时打断**的纯空转——协议侧的任何重试/分页
#: 上界都管不到它。正常模板的步数是几十到几百（分页、批量），10 万步已远高于任何
#: 合法调用；越界说明模板写出了不收敛的循环，按内部错误当场失败，而不是让调用方挂死。
_MAX_TRAMPOLINE_STEPS = 100_000


class _TrampolineMixin:
    """trampoline 驱动器（sync/async 共用的机制半边，P2-2 自 ``_ClientMixin`` 上移）。

    传输 seam 由子类实现：sync 为 ``_req(cmd, body, *, timeout)``，
    async 为同名 ``async _req``。``last_errors`` 收尾统一走
    :meth:`_set_last_errors` 钩子（sync 加锁、async 直赋——镜像既有差异）。
    """

    # The sync/async concrete clients provide these members.  They are
    # declared on the shared template so mypy can validate the trampoline
    # without weakening the concrete client contracts.
    timeout: float
    family: str
    _errors_lock: Any
    last_errors: list[tuple[str, BaseException]]
    _req: Any

    # ------------------------------------------------------------------ #
    # trampoline 驱动器
    # ------------------------------------------------------------------ #
    def _forward(self, result: ParseResult, label: str) -> ParseResult:
        """执行体重贴 :func:`_forward_decode_caveats` 的唯一入口：栈深显式记账。"""
        return _forward_decode_caveats(
            result,
            label,
            stackdepth=self._warning_stackdepth(),
        )

    def _warning_stackdepth(self) -> int:
        """``record_warning`` 语义下的调用方栈深（替代旧的运行期栈帧反查）。

        嵌套深度按**当前线程**的驱动器深度记账（B4/P2-2：``_DRIVE_DEPTH`` 是
        线程局部，并发 worker 各数各的，告警归属不再串帧）。
        """
        nested = max(_current_drive_depth() - 1, 0)
        return _SINGLE_HOP_STACKDEPTH + nested * _FRAMES_PER_NESTED_HOP

    def _drive(self, gen: Generator[_TrampolineOp, Any, Any]) -> Any:
        """同步驱动：执行模板挂起的 op，直到模板 return。"""
        _push_drive_depth()
        try:
            try:
                op = next(gen)
                steps = 0
                while True:
                    steps += 1
                    if steps > _MAX_TRAMPOLINE_STEPS:
                        raise self._trampoline_overrun(steps)
                    try:
                        if isinstance(op, _ReqOp):
                            out = self._req(op.cmd, op.body, **op.kwargs)
                        else:
                            out = getattr(self, op.name)(*op.args, **op.kwargs)
                    except BaseException as exc:  # noqa: BLE001 - 原样在挂起点重入模板
                        op = gen.throw(exc)
                    else:
                        op = gen.send(out)
            except StopIteration as stop:
                return stop.value
        finally:
            _pop_drive_depth()

    async def _adrive(self, gen: Generator[_TrampolineOp, Any, Any]) -> Any:
        """异步驱动：逐条 ``await`` 模板挂起的 op（镜像 :meth:`_drive`）。"""
        _push_drive_depth()
        try:
            try:
                op = next(gen)
                steps = 0
                while True:
                    steps += 1
                    if steps > _MAX_TRAMPOLINE_STEPS:
                        raise self._trampoline_overrun(steps)
                    try:
                        if isinstance(op, _ReqOp):
                            out = await self._req(op.cmd, op.body, **op.kwargs)
                        else:
                            out = await getattr(self, op.name)(*op.args, **op.kwargs)
                    except BaseException as exc:  # noqa: BLE001
                        op = gen.throw(exc)
                    else:
                        op = gen.send(out)
            except StopIteration as stop:
                return stop.value
        finally:
            _pop_drive_depth()

    @staticmethod
    def _trampoline_overrun(steps: int) -> InternalError:
        """模板不收敛时唯一的失败口：把"挂死"变成一条可读的内部错误。"""
        return InternalError(
            f"客户端模板在 {steps} 步内未收敛（上限 {_MAX_TRAMPOLINE_STEPS}）",
            context={
                "phase": "client_template",
                "steps": steps,
                "max_steps": _MAX_TRAMPOLINE_STEPS,
            },
        )

    def _set_last_errors(self, errors: list[tuple[str, BaseException]]) -> None:
        """``last_errors`` 收尾钩子（sync：与并发路径互斥加锁，C3）。"""
        with self._errors_lock:
            self.last_errors = errors
