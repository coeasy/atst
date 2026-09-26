# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""0x0547 原始字节推送通道（G4 / B8）。

TDX 主站的 ``0x0547 QUOTES_DEPTH_PUSH`` 是**服务端主动推送**的五档刷新帧：
与请求-应答式命令不同，它没有对应的请求应答，而是连接建立后按变化节拍
持续到达。本模块提供：

* :class:`PushFrame` —— 原始推送帧的结构化视图（含 best-effort 解析）；
* :class:`PushChannel` —— 订阅管理 + 帧读取（``read()`` / ``iter_messages()``）。

传输解耦：PushChannel 依赖一个**独占**的传输对象（⚠️ 不要传
:class:`~tstdx.transport.pool.ConnectionPool` 的共享连接——推送帧会与
请求-应答帧在同一 socket 上交错，绕过池级串行化）。满足任一形态即可：

* 真实连接 :class:`~tstdx.transport.base.TcpConnection`：
  订阅走 ``request(cmd, body)``，收帧走 ``read_frame(timeout=…)``（返回
  :class:`~tstdx.codec.framing.ResponseFrame`，本模块自动取 ``.payload``）；
* 测试 fake：``send_command(cmd, body)`` + ``read_frame(timeout=…) -> bytes``，
  让断线重连、超时、脏帧等路径可完全离线测试。

0x0547 帧为 L2 级布局（启发式），字段以「best-effort」标注：
结构漂移时 :meth:`PushFrame.parse` 不抛异常，而是把原始字节留在
``raw_data`` 供 L3 兜底与 ProtocolSniffer 归档。
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..domain.symbol import parse_symbol as _parse_symbol
from ..errors import ConnectionClosed, ReadTimeout, TdxError

__all__ = ["PushChannel", "PushFrame", "PUSH_CMD"]

logger = logging.getLogger(__name__)

PUSH_CMD = 0x0547


def _market_code(symbol: str) -> tuple[int, str]:
    """订阅符号 → ``(0x0547 订阅体的 market 字节, 裸 6 位码)``。

    市场编号与 :mod:`tstdx.domain.symbol` 单一事实源对齐（v5 DC1：
    0=深，1=沪，2=北交所）——直接复用 ``Symbol.tdx_market``，不再
    自建二值映射（旧实现把北交所误写 0，与市场编号修正后的库内
    事实源矛盾）。无法解析的符号退回旧行为（末 6 位 + 市场字节 0），
    交由服务端裁决。
    """
    try:
        sym = _parse_symbol(symbol)
    except TdxError:
        return 0, (symbol or "")[-6:]
    return sym.tdx_market, sym.code


@dataclass
class PushFrame:
    """一帧 0x0547 推送的结构化视图（best-effort 解析）。"""

    timestamp: float
    market: int
    code: str
    raw_data: bytes
    #: best-effort 解析出的字段（键可能随服务端布局漂移而缺省）
    parsed: dict[str, float] = field(default_factory=dict)
    #: 解析置信度：0.0（纯原始）/ 1.0（全部字段成功）
    confidence: float = 0.0

    @classmethod
    def parse(cls, raw: bytes, *, now: float | None = None) -> PushFrame:
        """解析原始推送帧（永不抛解析异常 —— L2 语义）。"""
        ts = time.time() if now is None else now
        parsed: dict[str, float] = {}
        market = 0
        code = ""
        confidence = 0.0

        # 帧头：market(1) + code(6, gbk 空格填充)
        if len(raw) >= 7:
            market = raw[0]
            try:
                code = raw[1:7].decode("ascii", errors="ignore").strip().strip("\x00")
            except Exception:  # noqa: BLE001 —— best-effort
                code = ""
            body = raw[7:]
            # 五档价量区（L2 启发式）：4B price ×N + 4B volume/amount 交叉
            # price 为 tdx_int 风格（LE uint32，÷100 元）；全零字段跳过
            names = ("bid1", "bid1_vol", "bid2", "bid2_vol", "ask1", "ask1_vol", "ask2", "ask2_vol")
            if len(body) >= 32:
                try:
                    values = struct.unpack("<8I", body[:32])
                    for nm, v in zip(names, values, strict=False):
                        parsed[nm] = (
                            v / 100.0 if nm.endswith(("1", "2")) and "vol" not in nm else float(v)
                        )
                    confidence = 0.7
                except struct.error:  # pragma: no cover —— 长度已检查
                    pass
        return cls(
            timestamp=ts,
            market=market,
            code=code,
            raw_data=raw,
            parsed=parsed,
            confidence=confidence,
        )


class PushChannel:
    """0x0547 推送订阅通道（线程安全）。

    Parameters
    ----------
    transport:
        **独占**的传输对象：真实连接传
        :class:`~tstdx.transport.base.TcpConnection`（``request`` +
        ``read_frame(timeout=…)``，返回 ``ResponseFrame`` 自动取 ``.payload``），
        测试可传具备 ``send_command(cmd, body)`` 与同名关键字的 fake。
        两侧都必须接受 ``timeout`` 关键字——本通道把调用方给的截止值透传给
        它，不再对无参形态做 ``except TypeError`` 退路。⚠️ 不要传连接池的共享连接：
        推送帧与请求-应答帧会在同一 socket 上交错，且绕过池级串行化。
    symbols:
        初始订阅列表（可后续 :meth:`subscribe` 增补）。

    用法::

        channel = PushChannel(conn, symbols=["sh600519"])
        frame = channel.read(timeout=5.0)
        if frame:
            print(frame.code, frame.parsed)
        channel.close()
    """

    def __init__(self, transport: Any, symbols: list[str] | None = None) -> None:
        self._transport = transport
        self._subscribed: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._last_error: BaseException | None = None
        if symbols:
            self.subscribe(symbols)

    # -- 传输适配 ------------------------------------------------------------ #
    def _send(self, cmd: int, body: bytes) -> None:
        """订阅登记：优先 ``send_command``（fake / 旧接口），
        真实 :class:`~tstdx.transport.base.TcpConnection` 走 ``request(cmd, body)``。
        """
        send = getattr(self._transport, "send_command", None)
        if callable(send):
            send(cmd, body)
            return
        self._transport.request(cmd, body)

    def _read_frame(self, timeout: float) -> Any:
        """读一帧，并把调用方给的截止值真的传下去。

        真实连接（:class:`~tstdx.transport.base.TcpConnection` 及其异步对偶）与
        测试替身都接受 ``read_frame(timeout=…)``；这里不再用
        ``except TypeError`` 退回无参调用——那条退路会把调用方的截止值静默丢掉，
        让 ``read(timeout=5.0)`` 在真连接上变成「按连接的 timeout 等、且不限次数」。
        """
        return self._transport.read_frame(timeout=timeout)

    # -- 订阅管理 ------------------------------------------------------------ #
    def subscribe(self, symbols: list[str]) -> bool:
        """追加订阅；成功登记返回 True（传输失败返回 False）。"""
        with self._lock:
            if self._closed:
                return False
            new = [s for s in symbols if s not in self._subscribed]
            if not new:
                return True
            try:
                self._send(PUSH_CMD, self._build_sub_body(new, on=True))
            except Exception as exc:  # noqa: BLE001 —— 传输失败不炸调用方
                logger.warning("0x0547 订阅登记失败: %s", exc)
                return False
            self._subscribed.update(new)
            return True

    def unsubscribe(self, symbols: list[str]) -> bool:
        """取消订阅；成功返回 True。"""
        with self._lock:
            if self._closed:
                return False
            gone = [s for s in symbols if s in self._subscribed]
            if not gone:
                return True
            try:
                self._send(PUSH_CMD, self._build_sub_body(gone, on=False))
            except Exception as exc:  # noqa: BLE001
                logger.warning("0x0547 取消订阅失败: %s", exc)
                return False
            self._subscribed.difference_update(gone)
            return True

    @staticmethod
    def _build_sub_body(symbols: list[str], *, on: bool) -> bytes:
        """订阅登记体：count(2) + [market(1)+code(6)] × N + flag(1)。

        符号经 :mod:`tstdx.domain.symbol` 归一化——``"600519.SH"`` 不再
        产出 ``"519.SH"``，市场编号见 :func:`_market_code`。
        """
        parts = [struct.pack("<H", len(symbols))]
        for s in symbols:
            market, code = _market_code(s)
            parts.append(
                bytes([market]) + code.encode("ascii", errors="ignore")[:6].ljust(6, b"\x00")
            )
        parts.append(bytes([1 if on else 0]))
        return b"".join(parts)

    # -- 读取 ---------------------------------------------------------------- #
    def read(self, timeout: float = 5.0) -> PushFrame | None:
        """读取下一帧推送；超时返回 None，通道已关闭返回 None。

        深审 M16：断线等传输错误不再静默降级为「无帧」——升级 warning
        并记录 :attr:`last_error` 供调用方感知重连；非传输类异常继续抛出
        （数据损坏伪装成「无推送」比报错更危险）。
        """
        frame, _ = self._read_with_reason(timeout)
        return frame

    #: 「这一轮没有帧」里仍然终止 :meth:`iter_messages` 的那几种原因。
    #: ``timeout`` / ``empty`` 不在其中：它们只说明这一刻没有数据，通道照样能用。
    _TERMINAL_READ = frozenset({"closed", "eof", "transport"})

    def _read_with_reason(self, timeout: float) -> tuple[PushFrame | None, str]:
        """一次读帧，连同「为什么没有帧」一起返回。

        原因分两类是这张面的口径：把「超时」和「通道已关闭 / 传输耗尽」混成同一个
        ``None``，消费者就无法区分「再等一会儿还有帧」和「这条通道再也不会给帧」——
        后者若被当成前者包在 ``while True`` 里，是一个不会自己结束的空转。
        """
        if self._closed:
            return None, "closed"
        try:
            raw = self._read_frame(timeout)
        except (TimeoutError, ReadTimeout):
            # 真连接把 socket 超时归类成 ReadTimeout（TdxError 支系，不是 OSError）：
            # 只接内建 TimeoutError 的话，这张面在真连接上永远走不到「超时」这一格。
            return None, "timeout"
        except (ConnectionError, OSError, ConnectionClosed) as exc:
            self._last_error = exc
            logger.warning("PushChannel 传输断开（连接层错误，可重连）: %s", exc)
            return None, "transport"
        if raw is None:
            return None, "eof"
        payload = getattr(raw, "payload", raw)  # ResponseFrame → 取载荷字节
        if not payload:
            return None, "empty"
        return PushFrame.parse(payload), "ok"

    @property
    def last_error(self) -> BaseException | None:
        """最近一次传输层错误（断线重连决策用；正常读帧/超时不清除）。"""
        return self._last_error

    def iter_messages(self, timeout: float = 1.0) -> Iterator[PushFrame]:
        """持续产出推送帧；通道关闭或传输耗尽（``read_frame`` 返回 None）时停止。

        超时与空载荷**不**结束迭代——它们不是终态。需要持续轮询的调用方请在外层
        ``while True`` 中消费本生成器：本生成器自己只会在终止原因上收口。
        """
        while True:
            frame, reason = self._read_with_reason(timeout)
            if reason in self._TERMINAL_READ:
                return
            if frame is not None:
                yield frame

    @property
    def subscribed(self) -> set[str]:
        """当前订阅集合（快照）。"""
        with self._lock:
            return set(self._subscribed)

    def close(self) -> None:
        """关闭通道（幂等）；已订阅标的尽力注销，并释放**独占**的传输对象。

        构造契约写明 transport 归本通道独占（见类文档），所以这里必须把它关掉：
        只发注销帧、留下 socket 一直开着，等于每建一条推送通道漏一条连接
        （第 26 轮 F-93）。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            if self._subscribed:
                self._send(PUSH_CMD, self._build_sub_body(sorted(self._subscribed), on=False))
        except Exception as exc:  # noqa: BLE001 —— 关闭路径尽力而为
            logger.debug("PushChannel 关闭时注销订阅失败: %s", exc)
        close = getattr(self._transport, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 —— 关闭路径尽力而为
                logger.debug("PushChannel 关闭传输对象失败: %s", exc)

    def __enter__(self) -> PushChannel:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
