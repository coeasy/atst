# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G48 — 同步 :meth:`TcpConnection._recv_exact` 的读预算必须有墙钟截止，与异步孪生同强。

第 31 轮第 2 遍实测到的无界形状：同步传输的"读满 N 字节"循环只在**每次 recv 之前**用
``sock.settimeout(self.timeout)``，那是**空档**超时——每收到 1 字节就重新武装。一个逐字节
吐数据的对端永远撞不到它：声明 ``timeout=0.4s`` 的调用方读 ``_recv_exact(32768)`` 可以永远
读不完。32768 不是虚构数字，它是单帧上限（``spec.max_frame_bytes``），而一帧里要读多少字节
是**对端**在响应头里写的 ``zip_size``。池的重试上界按 attempt 计，每个 attempt 本身无界，
所以这条兜不住；异步孪生用一条 ``wait_for`` 包住整个 ``readexactly``，本来就有墙钟——
同一份 ``timeout`` 声明在两张面上一张生效一张不生效（第 31 轮 B-3 那一族）。

实测证据（同轮日志 ``Temp/g47_before.out``，修复前）：

* 有限滴流对端：``declared timeout=0.4s -> got 24 bytes after 1.166s``（预算被越过 3 倍）；
* 无限滴流对端：``HUNG: _recv_exact never returned``；
* 池级同形状：``pool.request -> AllHostsUnreachable after 0.786s``（声明 0.4s）。

修复口径：进循环前算一次 ``deadline = monotonic() + self.timeout``，每轮按剩余预算收紧
socket 超时（``min(self.timeout, left)``，默认路径逐字节不变），预算见底即 ``close()`` 并抛
:class:`~tstdx.errors.ReadTimeout`；三条出口各自把 socket 超时还回 ``self.timeout``——读满时
一次、单帧上限那一格由"不丢连接"的空档超时分支还一次，因为这条连接接下来会被复用，
不复原就是把上一帧的几十毫秒残余当成下一帧的读预算。

判据不看注释也不看分支写法，只看五件事：① 对端每次空档都小于声明值、但总时长越过声明值时
必须被切断，且切断时刻跟着**声明值**走（两个不同的 ``timeout`` 各自量一遍）；② 无限滴流下
``_recv_exact(32768)`` 必须自己收口（修复前它永远不返回），并且它留下的两条确定分支各自
把预算还回去（②b 空档超时、②c 读满）；③ 这一格在池的调用面同样生效，报错按声明节拍上来
到调用方手里；④ 正常往返没被误伤；⑤ 结构上那份墙钟读数只算一次、算在循环之前，并被循环
与 ``settimeout`` 同时消费——异步孪生的整读 ``wait_for`` 也不许跟着被拆回逐次空档。
"""

from __future__ import annotations

import ast
import contextlib
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from tstdx.errors import AllHostsUnreachable, ReadTimeout
from tstdx.transport.base import TcpConnection
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import ECHO_CMD, RESP_HEADER, FakeTdxServer  # noqa: E402


class _DribbleServer:
    """一条由对端掌握节奏的连接：先给 ``prefix``，之后每 ``gap`` 秒滴 1 字节。

    ``total`` 为 ``None`` 时永不收口。这不是编出来的恶意形状——一帧要读多少字节由对端在
    响应头里写（``zip_size``，上限 32768），慢速链路本就存在。
    """

    def __init__(self, *, gap: float = 0.05, total: int | None = None, prefix: bytes = b"") -> None:
        self.gap = gap
        self.total = total
        self.prefix = prefix
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen(4)
        self.port: int = self._lsock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="probe-dribble", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._lsock.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _addr = self._lsock.accept()
            except (TimeoutError, socket.timeout):  # noqa: UP041 - 3.9+ 双分支（T1）
                continue
            except OSError:
                return
            threading.Thread(
                target=self._drip, args=(conn,), name="probe-dribble-conn", daemon=True
            ).start()

    def _drip(self, conn: socket.socket) -> None:
        with contextlib.suppress(OSError), conn:
            if self.prefix:
                conn.sendall(self.prefix)
            sent = 0
            while not self._stop.is_set() and (self.total is None or sent < self.total):
                time.sleep(self.gap)
                conn.sendall(b"\x00")
                sent += 1

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._lsock.close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> _DribbleServer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _conn(port: int, timeout: float) -> TcpConnection:
    conn = TcpConnection("127.0.0.1", port, timeout=timeout, connect_timeout=1.0, handshake=False)
    conn.connect()
    return conn


def _callee(call: ast.Call) -> str:
    node: ast.expr = call.func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return "<expr>"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timeout", "gap", "size"),
    [(0.2, 0.05, 16), (0.8, 0.02, 64)],
    ids=["timeout=0.2s", "timeout=0.8s"],
)
def test_a_peer_that_never_idles_long_enough_is_still_cut_off(
    timeout: float, gap: float, size: int
) -> None:
    """① 单次空档永远够不到 ``settimeout``，总时长却越过预算：必须按**声明值**切断。

    ``size * gap`` 刻意大于 ``timeout``：修复前这一格根本不打异常（每轮都"没超时"），
    所以它就是 G14 要的"改前必须红"。两个参数点各自量一遍，证明上界是 ``self.timeout``
    而不是某条硬编码常量。
    """
    assert size * gap > timeout, "自证锚点：对端必须比预算更慢"
    with _DribbleServer(gap=gap, total=size) as server:
        conn = _conn(server.port, timeout)
        try:
            started = time.monotonic()
            with pytest.raises(ReadTimeout):
                conn._recv_exact(size)
            elapsed = time.monotonic() - started
        finally:
            conn.close()
    assert elapsed < timeout + 0.2, f"墙钟截止没生效：声明 {timeout}s 却等了 {elapsed:.3f}s"


@pytest.mark.unit
def test_an_endless_dribbling_peer_cannot_keep_the_read_alive() -> None:
    """② 单帧上限那一格：读满 32768 字节必须自己收口，而不是永远读下去。

    修复前实测 ``HUNG: _recv_exact never returned``（``Temp/g47_before.out``）。

    这一格只量"有没有收口"，不去分辨是哪条分支收的口：滴流节奏和剩余预算赛跑，最后一段
    可能先撞 ``left <= 0``（丢连接），也可能先撞单次空档超时（留连接），两种都是上界内
    的正常行为。"留不留连接、预算还不还得回去"由下面两格各自钉住一条确定的分支。
    """
    with _DribbleServer(gap=0.05, total=None) as server:
        conn = _conn(server.port, 0.4)
        try:
            started = time.monotonic()
            with pytest.raises(ReadTimeout) as caught:
                conn._recv_exact(32768)
            elapsed = time.monotonic() - started
            assert "0.4" in str(caught.value), "报错里的预算不是声明值"
            assert elapsed < 1.5, f"无限滴流下仍然拖到 {elapsed:.3f}s"
        finally:
            conn.close()


@pytest.mark.unit
def test_the_idle_gap_branch_keeps_the_connection_and_returns_the_budget() -> None:
    """②b 空档跨过硬截止：必须按**剩余预算**武装这一次 recv，而不是按整份声明值。

    对端每 0.15 s 滴 1 字节，预算 0.4 s：第三段空档开始于 t≈0.30，只剩 ~0.10 s。收紧过的
    武装值让这一格在 t≈0.40 由**空档超时分支**收口（连接留着、下一帧还能用）；不收紧的话
    这一格会一直等到 t≈0.45 那个字节到来才发现预算用光，并顺路把连接丢掉。所以这一格同时
    钉住三件事：① 预算按剩余收紧；② 收口走的是不丢连接那条分支；③ 分支把循环里收紧出来
    的 ~0.10 s 还给 ``self.timeout``——不还，下一帧的读预算就成了几十毫秒残余。
    """
    with _DribbleServer(gap=0.15, total=None) as server:
        conn = _conn(server.port, 0.4)
        try:
            started = time.monotonic()
            with pytest.raises(ReadTimeout):
                conn._recv_exact(32768)
            elapsed = time.monotonic() - started
            assert elapsed < 0.47, f"没按剩余预算收口：等了 {elapsed:.3f}s（等下一个字节才发觉）"
            assert conn._sock is not None, "空档超时不该替调用方丢掉连接"
            assert conn._sock.gettimeout() == pytest.approx(0.4), "残余的收紧预算留给了下一帧"
        finally:
            conn.close()


@pytest.mark.unit
def test_a_completed_read_returns_the_socket_timeout_it_narrowed() -> None:
    """②c 读满那条分支：缓存在循环里的收紧值必须被读满后的复原盖回去。

    对端每 50 ms 滴 1 字节、总共 12 字节，预算 1.0 s：读取会**成功**，但退出循环时 socket
    超时已经被夹到 0.4 s 附近。所以这一格量的正是 ``_recv_exact`` 末尾那一次复原——直接调
    ``_recv_exact`` 而不是走 ``request``，因为 :meth:`TcpConnection.read_frame` 的 ``finally``
    自己也复原一次，会把缺了末尾复原的分支遮掉（④ 那一格遮的就是这一刀）。
    """
    with _DribbleServer(gap=0.05, total=12) as server:
        conn = _conn(server.port, 1.0)
        try:
            data = conn._recv_exact(12)
            assert len(data) == 12
            assert conn._sock is not None
            assert conn._sock.gettimeout() == pytest.approx(1.0), "读满后仍带着循环里的残余预算"
        finally:
            conn.close()


@pytest.mark.unit
def test_the_wall_clock_reaches_the_pool_call_surface() -> None:
    """③ 池里的一次请求也必须按声明节拍收口：重试上界是 attempt 数，救不了无界的 attempt。

    对端先回一份合法响应头、把 ``zip_size`` 写成单帧上限，随后逐字节滴——这正是
    "对端决定读多少" 与"对端决定多慢"叠在一起的形状。
    """
    header = RESP_HEADER.pack(0x0074CBB1, 0, 1, 0, ECHO_CMD, 32768, 32768)
    with _DribbleServer(gap=0.05, total=None, prefix=header) as server:
        pool = ConnectionPool(
            [HostEntry("127.0.0.1", server.port)],
            slots_per_host=1,
            timeout=0.4,
            connect_timeout=1.0,
            max_retries=0,
            heartbeat_interval=0,
            handshake=False,
        )
        started = time.monotonic()
        try:
            with pytest.raises(AllHostsUnreachable):
                pool.request(ECHO_CMD, b"\x01")
        finally:
            elapsed = time.monotonic() - started
            pool.close()
    assert elapsed < 2.0, f"池级请求拖到 {elapsed:.3f}s：单 attempt 仍然无界"


@pytest.mark.unit
def test_a_normal_round_trip_still_works_and_leaves_the_socket_timeout_declared() -> None:
    """④ 默认路径不许被误伤：正常回显照旧。

    注意这一格**测不到** ``_recv_exact`` 末尾那次复原：走 ``request`` 时外层
    :meth:`TcpConnection.read_frame` 的 ``finally`` 自己也把 socket 超时还原一次，把缺口遮住了
    （本轮在本文件内单独试过一次：删掉末尾复原，这一格照样绿，红的是 ②c）。所以"读满后预算回到声明值"
    由 ②c 直接调 ``_recv_exact`` 来钉，这一格只负责"正常往返没被误伤"。
    """
    with FakeTdxServer() as server:
        conn = TcpConnection(
            "127.0.0.1", server.port, timeout=1.23, connect_timeout=1.0, handshake=False
        )
        try:
            conn.connect()
            frame = conn.request(ECHO_CMD, b"\x09\x08")
            assert frame.payload[4:] == b"\x09\x08"
            assert conn._sock is not None
            assert conn._sock.gettimeout() == pytest.approx(1.23)
        finally:
            conn.close()


@pytest.mark.unit
def test_the_async_twin_still_wraps_its_read_in_one_wall_clock() -> None:
    """⑤ 另一半：异步孪生的整读 ``wait_for`` 不许跟着被拆成逐次空档超时。

    这一格量的就是"两张面一样强"的对照面——B-3 那族的根因正是只有一张面有墙钟。
    """
    tree = ast.parse((REPO_ROOT / "tstdx" / "transport" / "async_.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_recv_exact"
    )
    callees = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "asyncio.wait_for" in callees, "异步侧的墙钟被拆掉了"
    assert any(name.endswith("readexactly") for name in callees), "异步侧不再整读"


@pytest.mark.unit
def test_the_read_budget_is_read_once_outside_the_loop_and_spent_inside_it() -> None:
    """结构判据：墙钟截止必须算在循环之前、并被循环与 ``settimeout`` 同时消费。

    这一格量的就是修复前那个形状——整个函数里没有一处"总预算"读数，只有逐次
    ``settimeout``：空档超时永远量不到"对端每 50 ms 吐 1 字节"。
    """
    tree = ast.parse((REPO_ROOT / "tstdx" / "transport" / "base.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_recv_exact"
    )
    loops = [node for node in ast.walk(fn) if isinstance(node, ast.While)]
    assert len(loops) == 1, f"读取循环的形状变了（{len(loops)} 个 while），这一格要重读"
    loop = loops[0]
    deadline_names = {
        target.id
        for assign in ast.walk(fn)
        if isinstance(assign, ast.Assign)
        and assign.lineno < loop.lineno
        and isinstance(assign.value, ast.BinOp)
        and any(
            isinstance(node, ast.Call) and _callee(node) == "time.monotonic"
            for node in ast.walk(assign.value)
        )
        and any(
            isinstance(node, ast.Attribute) and node.attr == "timeout"
            for node in ast.walk(assign.value)
        )
        for target in assign.targets
        if isinstance(target, ast.Name)
    }
    assert deadline_names, "_recv_exact 不再在循环外读一份墙钟截止"
    loaded_in_loop = {
        node.id
        for node in ast.walk(loop)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert loaded_in_loop & deadline_names, "循环没按那份截止推进"
    #: 循环从截止里再算出"还剩多少"（``left = deadline - monotonic()``），武装 socket
    #: 用的正是那个派生读数——所以放行"截止名本身"或"由它派生出来的名字"。
    derived = {
        target.id
        for assign in ast.walk(loop)
        if isinstance(assign, ast.Assign)
        for target in assign.targets
        if isinstance(target, ast.Name)
        and {n.id for n in ast.walk(assign.value) if isinstance(n, ast.Name)} & deadline_names
    }
    budget_names = deadline_names | derived
    arms = [
        node
        for node in ast.walk(loop)
        if isinstance(node, ast.Call) and _callee(node) == "sock.settimeout"
    ]

    def _is_declared_timeout(arg: ast.expr) -> bool:
        #: ``self.timeout``：把预算原样还回去，不是收紧。
        return (
            isinstance(arg, ast.Attribute)
            and arg.attr == "timeout"
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "self"
        )

    def _plain_restore(call: ast.Call) -> bool:
        return len(call.args) == 1 and _is_declared_timeout(call.args[0])

    narrowing = [call for call in arms if not _plain_restore(call)]
    assert len(narrowing) == 1, f"循环里收紧了 {len(narrowing)} 次 socket 超时，期望 1 次"
    assert len(arms) == 2, f"循环里该有一次收紧 + 一次复原，现扫 {len(arms)} 次"
    armed_with = {node.id for node in ast.walk(narrowing[0]) if isinstance(node, ast.Name)}
    assert armed_with & budget_names, "逐次 settimeout 没被那份截止夹住：空档又成了唯一预算"
