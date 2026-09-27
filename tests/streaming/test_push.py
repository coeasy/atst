"""PushChannel 测试（G4）：0x0547 推送帧解析与订阅管理。

使用 fake 传输验证：订阅/取消订阅、超时读取返回 None、帧解析 best-effort、
真实 ``TcpConnection`` 形态（``request`` + 无参 ``read_frame`` 返回 ResponseFrame）
的鸭子类型适配、订阅体符号归一化。
"""

from __future__ import annotations

import struct

import pytest

from atst.streaming.push import PUSH_CMD, PushChannel, PushFrame


class _FakeTransport:
    """满足 PushChannel 需要的传输协议：send_command / read_frame。"""

    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.frames = list(frames or [])
        self.sent: list[tuple[int, bytes]] = []
        self._timeout = False

    def send_command(self, cmd: int, body: bytes) -> bytes:
        self.sent.append((cmd, body))
        return b"ok"

    def read_frame(self, timeout: float = 5.0) -> bytes | None:
        if self._timeout:
            raise TimeoutError("timeout")
        if self.frames:
            return self.frames.pop(0)
        return None


@pytest.mark.unit
class TestPushChannel:
    """PushChannel 推送通道测试。"""

    def test_subscribe_unsubscribe(self):
        """#1 订阅/取消订阅：登记到 subscribed 集合并发送 0x0547 命令。"""
        ch = PushChannel(_FakeTransport())
        assert ch.subscribe(["sh600519", "sz000001"]) is True
        assert ch.subscribed == {"sh600519", "sz000001"}
        assert ch._transport.sent[-1][0] == PUSH_CMD
        # 重复订阅去重
        assert ch.subscribe(["sh600519"]) is True
        assert len(ch.subscribed) == 2
        # 取消订阅
        assert ch.unsubscribe(["sh600519"]) is True
        assert ch.subscribed == {"sz000001"}

    def test_read_timeout_returns_none(self):
        """#2 超时读取返回 None。"""
        t = _FakeTransport()
        t._timeout = True
        ch = PushChannel(t)
        assert ch.read(timeout=0.01) is None

    def test_read_returns_parsed_frame(self):
        """#3 读取一帧并解析出 best-effort 结构。"""
        raw = bytes([1]) + b"600519" + b"\x00" * 32
        ch = PushChannel(_FakeTransport(frames=[raw]))
        frame = ch.read(timeout=1.0)
        assert isinstance(frame, PushFrame)
        assert frame.code == "600519"
        assert frame.market == 1
        assert frame.raw_data == raw

    def test_iter_messages_yields_frames(self):
        """#4 iter_messages 持续产出推送帧。"""
        raw = bytes([0]) + b"000001" + b"\x00" * 32
        ch = PushChannel(_FakeTransport(frames=[raw, raw]))
        frames = [f for f in ch.iter_messages(timeout=0.001)]
        assert len(frames) == 2

    def test_close_stops_subscription(self):
        """#5 close 后订阅/读取返回失败/None。"""
        ch = PushChannel(_FakeTransport())
        ch.subscribe(["sh600519"])
        ch.close()
        assert ch.subscribe(["sz000001"]) is False
        assert ch.read(timeout=0.01) is None


@pytest.mark.unit
class TestPushBodyNormalization:
    """订阅体符号归一化与市场字节（审计 §2-27）。"""

    def test_suffix_symbol_no_more_519sh(self):
        """C1 家族回归：``"600519.SH"`` 归一化为 market=1 + 裸码 600519。"""
        body = PushChannel._build_sub_body(["600519.SH"], on=True)
        assert body == struct.pack("<H", 1) + bytes([1]) + b"600519" + bytes([1])

    def test_prefix_symbol_unchanged(self):
        """前缀符号行为不回归：sh→1，sz→0。"""
        body = PushChannel._build_sub_body(["sh600519", "sz000001"], on=True)
        assert body[2] == 1
        assert body[3:9] == b"600519"
        assert body[9] == 0
        assert body[10:16] == b"000001"

    def test_bj_market_byte_aligned_with_prefix_market(self):
        """bj 市场字节对齐 client._PREFIX_MARKET（v5 DC1：0=深 1=沪 2=北）。"""
        body = PushChannel._build_sub_body(["bj430047"], on=True)
        assert body[2] == 2
        assert body[3:9] == b"430047"


@pytest.mark.unit
class TestPushChannelConnectionAdapter:
    """对真实 Connection 形态的鸭子类型适配。"""

    def test_real_connection_shape(self):
        """无 ``send_command`` 的连接走 ``request(cmd, body)``；无参
        ``read_frame()`` 回退 + ResponseFrame 自动取 ``.payload``。"""

        class _FakeConn:
            """模拟 TcpConnection 的真实 API 面（request / read_frame(timeout=…)）。"""

            def __init__(self, payload: bytes):
                self.payload = payload
                self.requested: list[tuple[int, bytes]] = []
                self.timeouts: list[float | None] = []

            def request(self, cmd: int, body: bytes):
                self.requested.append((cmd, body))
                return b"ok"

            def read_frame(self, timeout: float | None = None):
                self.timeouts.append(timeout)
                from atst.codec.framing import ResponseFrame

                return ResponseFrame(
                    magic=0x0074CBB1,
                    zip_flag=0,
                    seq=1,
                    method=0x0547,
                    zip_size=0,
                    unzip_size=0,
                    payload=self.payload,
                )

        raw = bytes([1]) + b"600519" + b"\x00" * 32
        conn = _FakeConn(raw)
        ch = PushChannel(conn, symbols=["600519.SH"])
        assert ch.subscribed == {"600519.SH"}
        assert conn.requested and conn.requested[0][0] == PUSH_CMD
        frame = ch.read(timeout=1.0)
        assert isinstance(frame, PushFrame)
        assert frame.code == "600519"
        assert frame.market == 1
        assert frame.raw_data == raw
        # 调用方的截止值必须真的到达传输对象（第 28 轮：它曾被 except TypeError 吞掉）
        assert conn.timeouts == [1.0]

    def test_close_uses_request_adapter(self):
        """close 注销同样经 request 适配（不再调不存在的 send_command）。"""

        class _FakeConn:
            def __init__(self):
                self.requested: list[tuple[int, bytes]] = []

            def request(self, cmd: int, body: bytes):
                self.requested.append((cmd, body))
                return b"ok"

            def read_frame(self, timeout: float | None = None):
                raise TimeoutError("no frame")

        conn = _FakeConn()
        ch = PushChannel(conn, symbols=["sh600519"])
        before = len(conn.requested)
        ch.close()
        assert len(conn.requested) == before + 1
        # 注销体 flag=0
        assert conn.requested[-1][1][-1] == 0
