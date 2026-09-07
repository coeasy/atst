# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F1 批次「流式与客户端」域回归：C3 错误收集 / P1a 指数 ctx / P1 项。

全部离线（fake pool / monkeypatch），零网络。fake 一律打在传输层
（假 ConnectionPool），不打桩被测方法本身。
"""

from __future__ import annotations

import asyncio
import struct
import warnings

import pytest

pytestmark = pytest.mark.unit

import tstdx.client as client_mod  # noqa: E402
from tstdx.client import TdxClient, _quote_body  # noqa: E402
from tstdx.codec.framing import ResponseFrame  # noqa: E402
from tstdx.codec.primitive import encode_leb128  # noqa: E402
from tstdx.errors import ConnectionFailed, SymbolError  # noqa: E402

_MAGIC = 0x0074CBB1


# --------------------------------------------------------------------------- #
# fake 传输层
# --------------------------------------------------------------------------- #
def _quote_payload(code: str, market: int = 1) -> bytes:
    """最小合法 0x0530 响应（回声裸码 + 标准语义市场 + 价格流）。"""
    return (
        bytes([market])
        + code.encode("ascii").ljust(6, b"\x00")
        + b"\x00\x00"  # offset 7-8 不透明区
        + encode_leb128(5000)  # price = 50.00（scale 100）
        + encode_leb128(0)  # last_close - price
        + encode_leb128(0)  # open - price
        + encode_leb128(0)  # high - price
        + encode_leb128(0)  # low - price
        + encode_leb128(100)  # u4（未识别）
        + encode_leb128(-5000)  # 哨兵 = -price
        + encode_leb128(1000)  # volume_lots
        + encode_leb128(0)  # v2
        + b"\x00\x00\x00\x00"  # tdx_float amount
    )


def _bars_payload_index() -> bytes:
    """两条指数 K 线记录（0x052D + index 布局：尾部 uint16 涨/跌家数）。

    差分链：rec1 open=1000 → 1.0 元 / close=1500；rec2 以 close 为基准。
    """
    specs = [
        (20240108, 1000, 500, 800, 200, 800, 200),
        (20240109, 0, 0, 100, -100, 700, 300),
    ]
    recs: list[bytes] = []
    for dt, od, cd, hd, ld, up, down in specs:
        recs.append(
            struct.pack("<I", dt)
            + encode_leb128(od)
            + encode_leb128(cd)
            + encode_leb128(hd)
            + encode_leb128(ld)
            + b"\x00\x00\x00\x00"  # tdx_float volume
            + b"\x00\x00\x00\x00"  # tdx_float amount
            + struct.pack("<HH", up, down)  # 指数尾部 4 字节
        )
    return struct.pack("<H", len(recs)) + b"".join(recs)


class _FakePool:
    """假连接池：0x0530 动态按请求体回声合法帧；其余命令按 ``payloads``
    返回，未配置或列入 ``fail_cmds`` 时抛 TdxError（模拟主站下线/断连）。
    记录全部请求供断言。
    """

    def __init__(
        self,
        payloads: dict[int, bytes] | None = None,
        fail_cmds: tuple[int, ...] = (),
    ):
        self._payloads = payloads or {}
        self._fail_cmds = set(fail_cmds)
        self.requests: list[tuple[int, bytes]] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        self.requests.append((cmd, body))
        if cmd in self._fail_cmds:
            raise ConnectionFailed(f"simulated outage: {cmd:#x}")
        if cmd == 0x0530 and cmd not in self._payloads:
            code = body[2:8].decode("ascii").rstrip("\x00")
            return _frame(cmd, _quote_payload(code, market=1))
        payload = self._payloads.get(cmd)
        if payload is None:
            raise ConnectionFailed(f"fake pool 未配置命令 {cmd:#x} 的响应")
        return _frame(cmd, payload)


def _frame(cmd: int, payload: bytes) -> ResponseFrame:
    return ResponseFrame(
        magic=_MAGIC,
        zip_flag=0,
        seq=1,
        method=cmd,
        zip_size=0,
        unzip_size=0,
        payload=payload,
    )


class _SelectivePool:
    """0x0530 按回声代码定向失败：600001/600002 断连，其余回合法帧。"""

    BAD = {"600001", "600002"}

    def __init__(self) -> None:
        self.requests: list[tuple[int, bytes]] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        self.requests.append((cmd, body))
        code = body[2:8].decode("ascii").rstrip("\x00")
        if cmd == 0x0530 and code in self.BAD:
            raise ConnectionFailed(f"simulated outage: {code}")
        return _frame(cmd, _quote_payload(code, market=1))


# --------------------------------------------------------------------------- #
# C3：last_errors
# --------------------------------------------------------------------------- #
class TestLastErrors:
    def test_declared_in_init(self) -> None:
        """C3：__init__ 显式声明 last_errors（首访不再 AttributeError）。"""
        client = TdxClient(pool=_FakePool())
        assert client.last_errors == []
        aclient = client_mod.AsyncTdxClient(pool=_FakePool())
        assert aclient.last_errors == []

    def test_concurrent_two_bad_one_good_exact_errors(self) -> None:
        """C3 回归：并发两只坏一只好，last_errors 恰含两条错误（真实 quotes 路径）。"""
        client = TdxClient(pool=_SelectivePool())
        out = client.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=3)
        assert [q["code"] for q in out] == ["600003"]
        assert len(client.last_errors) == 2
        assert {sym for sym, _ in client.last_errors} == {"sh600001", "sh600002"}
        assert all(isinstance(exc, ConnectionFailed) for _, exc in client.last_errors)

    def test_bad_symbol_does_not_interrupt_batch(self) -> None:
        """坏标的（无法解析）只淘汰自己，不中断整批（错误进 last_errors）。"""
        client = TdxClient(pool=_FakePool({0x0530: _quote_payload("600003")}))
        out = client.quotes(["6009!", "sh600003"], as_format="dict")
        assert [q["code"] for q in out] == ["600003"]
        assert len(client.last_errors) == 1
        sym, exc = client.last_errors[0]
        assert sym == "6009!"
        assert isinstance(exc, SymbolError)

    def test_quotes_then_concurrent_reset_semantics(self) -> None:
        """串行调用后 last_errors 被新一轮覆盖（公开行为保持），并发结束一次性赋值。"""
        client = TdxClient(pool=_SelectivePool())
        client.quotes(["sh600003"], as_format="dict")
        assert client.last_errors == []
        client.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=3)
        assert len(client.last_errors) == 2

    def test_async_concurrent_exact_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C3 异步镜像：错误收进调用局部列表，结束一次性赋值。"""
        from tstdx.client import AsyncTdxClient

        aclient = AsyncTdxClient()

        async def fake_quotes(symbols, *, as_format="dict", _collect=None):
            sym = symbols[0]
            if sym in ("sh600001", "sh600002"):
                raise ConnectionFailed("sim")
            return [{"code": sym}]

        monkeypatch.setattr(aclient, "quotes", fake_quotes)

        async def run() -> list:
            return await aclient.quotes_concurrent(["sh600001", "sh600002", "sh600003"], workers=3)

        out = asyncio.run(run())
        assert [q["code"] for q in out] == ["sh600003"]
        assert len(aclient.last_errors) == 2


# --------------------------------------------------------------------------- #
# P1a：指数 K 线 ctx
# --------------------------------------------------------------------------- #
class TestIndexBarsCtx:
    def test_bars_passes_index_ctx_to_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P1a：client.bars(index=True) 的 ctx 到达 dispatch（接线断点修复）。"""
        captured: dict = {}
        real_dispatch = client_mod.dispatch

        def spy(frame, **kwargs):
            captured.update(kwargs)
            return real_dispatch(frame, **kwargs)

        monkeypatch.setattr(client_mod, "dispatch", spy)
        client = TdxClient(pool=_FakePool({0x052D: _bars_payload_index()}))
        client.bars("sh000300", index=True, count=2, as_format="dict")
        assert captured.get("index") is True

    def test_index_parser_consumes_4byte_tail(self) -> None:
        """P1a（更扎实路）：带 index ctx 时解析器消费 4 字节涨跌家数尾，
        第 2 根 datetime/差分链不再错位。"""
        client = TdxClient(pool=_FakePool({0x052D: _bars_payload_index()}))
        bars = client.bars("sh000300", index=True, count=2, as_format="dict")
        assert [b["datetime"] for b in bars] == ["2024-01-08 15:00", "2024-01-09 15:00"]
        assert bars[0]["up_count"] == 800
        assert bars[0]["down_count"] == 200
        assert bars[1]["up_count"] == 700
        assert bars[1]["down_count"] == 300
        assert bars[1]["close"] == pytest.approx(1.5)

    def test_without_index_ctx_no_updown_fields(self) -> None:
        """默认 index=False 行为不回归：股票路径无涨跌家数字段（布局需 golden 定标）。"""
        client = TdxClient(pool=_FakePool({0x052D: _bars_payload_index()}))
        bars = client.bars("sh600519", count=2, as_format="dict")
        assert bars and all("up_count" not in b for b in bars)

    def test_facade_index_bars_end_to_end(self) -> None:
        """P1a：facade market.HqClient.index_bars 显式传 index=True（此前永不传递）。"""
        from tstdx.facade.market import HqClient

        hq = HqClient(pool=_FakePool({0x052D: _bars_payload_index()}))
        rows = hq.index_bars("sh000300", limit=2)
        assert rows[0]["up_count"] == 800
        assert rows[1]["datetime"] == "2024-01-09 15:00"

    def test_async_bars_signature_mirrors_index(self) -> None:
        """同步/异步 bars 签名一致（parity 门禁约束：参数名集合相等）。"""
        import inspect

        sync_params = set(inspect.signature(TdxClient.bars).parameters)
        async_params = set(inspect.signature(client_mod.AsyncTdxClient.bars).parameters)
        assert sync_params == async_params
        assert "index" in sync_params


# --------------------------------------------------------------------------- #
# P1：quotes_snapshot 分片 / export 截断告警 / _quote_body 防护
# --------------------------------------------------------------------------- #
class TestQuotesSnapshotChunking:
    def test_chunks_of_max_80_with_fallback(self) -> None:
        """>255 只不再 struct.error 绕过回退；每批 ≤80 分片请求。"""
        client = TdxClient(pool=_FakePool(fail_cmds=(0x054C,)))  # 0x054C 全部失败 → 逐只回退
        syms = [f"sh{600000 + i}" for i in range(300)]
        out = client.quotes_snapshot(syms)
        cmds = [cmd for cmd, _ in client._pool.requests]
        assert cmds.count(0x054C) == 4  # 300 = 80+80+80+60
        body_lens = [body[0] for cmd, body in client._pool.requests if cmd == 0x054C]
        assert body_lens == [80, 80, 80, 60]
        assert len(out) == 300
        assert len(client.last_errors) == 4  # 每片一次 0x054C 失败记录
        assert {sym for sym, _ in client.last_errors} == {"0x054C"}

    def test_empty_batch_ok(self) -> None:
        client = TdxClient(pool=_FakePool())
        assert client.quotes_snapshot([]) == []
        assert client._pool.requests == []


class TestExportSecurityListTruncation:
    def test_max_pages_exhaustion_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """短页/max_pages 耗尽 → UserWarning 明示可能截断。"""
        client = TdxClient()
        monkeypatch.setattr(
            client,
            "security_list",
            lambda m, start=0: [{"code": f"X{start:04d}"} for _ in range(1000)],
        )
        with pytest.warns(UserWarning, match="截断"):
            out = client.export_security_list(0, max_pages=2)
        assert len(out) == 2000

    def test_short_page_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常短页收尾不告警。"""
        client = TdxClient()
        pages = {
            0: [{"code": "600000"} for _ in range(1000)],
            1000: [{"code": "600000"} for _ in range(600)],
        }
        monkeypatch.setattr(client, "security_list", lambda m, start=0: pages.get(start, []))
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = client.export_security_list(0)
        assert len(out) == 1600

    def test_empty_first_page_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = TdxClient()
        monkeypatch.setattr(client, "security_list", lambda m, start=0: [])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert client.export_security_list(0) == []


class TestQuoteBodyGuard:
    def test_market_inverted_semantics_unchanged(self) -> None:
        """0/1 反转语义保持（深→1 沪→0）。"""
        assert _quote_body("600519", 1) == bytes([0x01, 0]) + b"600519"
        assert _quote_body("000651", 0) == bytes([0x01, 1]) + b"000651"

    def test_out_of_range_market_clamped(self) -> None:
        """market>1 不再产出 1-market 负数字节（clamp 到 0/1）。"""
        assert _quote_body("600519", 5) == bytes([0x01, 0]) + b"600519"
        assert _quote_body("600519", 255) == bytes([0x01, 0]) + b"600519"
        assert _quote_body("600519", -3) == bytes([0x01, 1]) + b"600519"
