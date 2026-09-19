# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v5 优化批次客户端域回归（PG1 bars 分页 / PG2 file_download 多包 / DC1 bj 市场号）。

全部离线（fake pool 注入传输层），零网络。fake 一律打在
ConnectionPool 上（对齐 test_client_f1 模式），不打桩被测方法本身。
"""

from __future__ import annotations

import asyncio
import struct
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit

from tstdx.client import (  # noqa: E402
    _PREFIX_MARKET,
    MAX_BARS_PER_REQUEST,
    AsyncTdxClient,
    TdxClient,
)
from tstdx.codec.framing import ResponseFrame  # noqa: E402
from tstdx.codec.primitive import encode_leb128  # noqa: E402
from tstdx.diagnostics import WarningCode, warning_sink  # noqa: E402
from tstdx.domain.symbol import to_tdx_market  # noqa: E402
from tstdx.errors import ParseError, TruncatedDataError  # noqa: E402

_MAGIC = 0x0074CBB1


# --------------------------------------------------------------------------- #
# 载荷合成
# --------------------------------------------------------------------------- #
def _date_int(day_offset: int) -> int:
    """以 2000-01-01 为基准生成合法 YYYYMMDD 整数（day K 线 datetime）。"""
    return int((date(2000, 1, 1) + timedelta(days=day_offset)).strftime("%Y%m%d"))


def _bars_payload(n: int, *, start: int = 0, index: bool = False) -> bytes:
    """合成 n 条 0x052D 日 K 记录，datetime 按请求 start 偏移（新→旧）。

    差分链：每条 od=1000/cd=100/hd=50/ld=-50（数值自洽即可，测试只关心
    条数与 datetime 唯一性）。
    """
    recs: list[bytes] = []
    for i in range(n):
        dt = _date_int(20000 - start - i)  # start 越大 datetime 越旧
        recs.append(
            struct.pack("<I", dt)
            + encode_leb128(1000)
            + encode_leb128(100)
            + encode_leb128(50)
            + encode_leb128(-50)
            + b"\x00\x00\x00\x00"  # tdx_float volume
            + b"\x00\x00\x00\x00"  # tdx_float amount
            + (struct.pack("<HH", 10, 5) if index else b"")
        )
    return struct.pack("<H", n) + b"".join(recs)


def _file_payload(total_len: int, data: bytes) -> bytes:
    """合成 0x06B9 响应载荷：``<I total_len>`` + 本包字节。"""
    return struct.pack("<I", total_len) + data


class _PagePool:
    """0x052D 分页 fake：按请求体里的 start/count 动态合成响应页。

    ``pages`` 映射 start → 该页实际返回条数（缺省 = 满 page 条）；
    记录全部 (start, count) 请求供断言。
    """

    def __init__(self, pages: dict[int, int] | None = None, *, index: bool = False):
        # pages[start] = 实际返回条数；None 值表示返回空页
        self.pages = pages or {}
        self.index = index
        self.requests: list[tuple[int, int]] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        assert cmd == 0x052D
        mkt, code, category, one, start, count = struct.unpack_from("<H6sHHHH", body, 0)
        self.requests.append((start, count))
        n = self.pages.get(start, count if count <= MAX_BARS_PER_REQUEST else count)
        if n is None:
            n = 0
        return _frame(cmd, _bars_payload(min(n, count), start=start, index=self.index))


class _FilePool:
    """0x06B9 多包 fake：按请求 offset 返回对应分片（脚本化序列）。"""

    def __init__(self, total_len: int, chunks: dict[int, bytes]):
        self.total_len = total_len
        self.chunks = chunks  # offset → 本包字节
        self.requests: list[tuple[int, int]] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        assert cmd == 0x06B9
        offset, length = struct.unpack_from("<II", body, 6 + 2 + 80)
        self.requests.append((offset, length))
        data = self.chunks.get(offset, b"")
        return _frame(cmd, _file_payload(self.total_len, data))


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


# --------------------------------------------------------------------------- #
# PG1：bars() 分页
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestBarsPagination:
    """PG1：count>800 自动翻页 / 短页终止 / 空页终止 / 去重与 strict。"""

    def test_single_request_within_limit(self) -> None:
        """count<=800 单请求（不引入额外 RTT——性能护栏）。"""
        pool = _PagePool()
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        bars = client.bars("sh600519", period="day", count=100)
        assert len(pool.requests) == 1
        assert len(bars) == 100

    def test_two_pages_for_1600(self) -> None:
        """count=1600 → 2 次请求：start=0/800，datetime 不重不漏。"""
        pool = _PagePool()
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        bars = client.bars("sh600519", period="day", count=1600)
        assert len(pool.requests) == 2
        assert pool.requests[0] == (0, 800)
        assert pool.requests[1] == (800, 800)
        assert len(bars) == 1600
        dts = [b["datetime"] for b in bars]
        assert len(set(dts)) == 1600  # 无重复

    def test_short_page_stops_without_warning(self) -> None:
        """次页短页（历史耗尽）→ 正常终止、不告警（历史不足≠截断）。"""
        pool = _PagePool(pages={800: 300})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            bars = client.bars("sh600519", period="day", count=1600)
        assert len(bars) == 1100  # 800 + 300
        assert not [x for x in caught if "锚点漂移" in str(x.message)]

    def test_empty_page_stops(self) -> None:
        """次页空响应 → 终止，返回首页 800 根，不告警（次页空才是历史耗尽）。"""
        pool = _PagePool(pages={800: None})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        import warnings as _w

        with _w.catch_warnings(), warning_sink() as caveats:
            _w.simplefilter("error")
            bars = client.bars("sh600519", period="day", count=1600)
        assert len(bars) == 800
        #: 正常耗尽不许变成 wire 上的噪声：空元组是"这次结果干净"的证据。
        assert caveats == []

    def test_empty_first_page_warns(self) -> None:
        """首页即 0 条（服务端 count=0 空桩）不得静默读成"成功取到 0 根"（F-45）。"""
        pool = _PagePool(pages={0: None})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with (
            pytest.warns(UserWarning, match="首页即空响应"),
            warning_sink() as caveats,
        ):
            bars = client.bars("sh600519", period="day", count=100)
        assert bars == []
        assert len(pool.requests) == 1  # 不额外重试：空桩换不来数据
        #: 同一条事实从此有两个读者：stderr 给进程内的人，sink 给执行器与 wire。
        assert [item.code for item in caveats] == [WarningCode.BARS_EMPTY_FIRST_PAGE]

    def test_empty_first_page_strict_raises(self) -> None:
        """strict=True 时空首页与漂移截断同级 → TruncatedDataError。"""
        pool = _PagePool(pages={0: None})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with pytest.raises(TruncatedDataError):
            client.bars("sh600519", period="day", count=100, strict=True)

    def test_async_empty_first_page_warns(self) -> None:
        """异步侧同一判据（同步/异步共享同一模板，告警不得只在一侧生效）。"""

        class _AsyncStubPool(_PagePool):
            async def request(self, cmd, body, timeout=None):  # noqa: ANN001
                return _PagePool.request(self, cmd, body, timeout)

        client = AsyncTdxClient(pool=_AsyncStubPool(pages={0: None}))  # type: ignore[arg-type]
        with pytest.warns(UserWarning, match="首页即空响应"):
            bars = asyncio.run(client.bars("sh600519", period="day", count=100))
        assert bars == []

    def test_drift_dedupe_warns_by_default(self) -> None:
        """整页重复（锚点漂移）→ 去重防死循环 + 默认 UserWarning。"""
        pool = _DriftPool()
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with pytest.warns(UserWarning, match="锚点漂移"):
            bars = client.bars("sh600519", period="day", count=1600)
        assert len(bars) == 800  # 第二页全部重复 → 只有首页有效

    def test_drift_strict_raises(self) -> None:
        """strict=True 时锚点漂移截断 → TruncatedDataError。"""
        pool = _DriftPool()
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with pytest.raises(TruncatedDataError):
            client.bars("sh600519", period="day", count=1600, strict=True)

    def test_index_flag_passthrough(self) -> None:
        """index=True 走同一分页路径（尾部涨跌家数由解析器处理）。"""
        pool = _PagePool(index=True)
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        bars = client.bars("sh000001", period="day", count=900, index=True)
        assert len(bars) == 900

    def test_async_mirror(self) -> None:
        """异步镜像分页语义一致（2 页 + 条数）。"""

        class _AsyncPagePool(_PagePool):
            async def request(self, cmd, body, timeout=None):  # noqa: ANN001
                return _PagePool.request(self, cmd, body, timeout)

        pool = _AsyncPagePool()
        client = AsyncTdxClient(pool=pool)  # type: ignore[arg-type]
        bars = asyncio.run(client.bars("sh600519", period="day", count=1600))
        assert len(pool.requests) == 2
        assert len(bars) == 1600


class _DriftPool:
    """第二页返回与首页完全相同的 datetime（模拟盘中锚点漂移整页重复）。"""

    def __init__(self) -> None:
        self.requests: list[tuple[int, int]] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        start, count = struct.unpack_from("<HH", body, 12)
        self.requests.append((start, count))
        if start == 0:
            return _frame(cmd, _bars_payload(800, start=0))
        # 锚点漂移：第二页 datetime 与首页重叠（start 未生效）
        return _frame(cmd, _bars_payload(count, start=0))


# --------------------------------------------------------------------------- #
# PG2：file_download 多包
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestFileDownloadPackets:
    """PG2：length=0 全量循环 / total_len 校验 / length>0 旧单包语义。"""

    def test_multi_packet_assembly(self) -> None:
        """3 包文件循环拼装：offset 递增、累计达 total_len 即止。"""
        chunk = b"\x01" * 100
        pool = _FilePool(total_len=300, chunks={0: chunk, 100: chunk, 200: chunk})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        data = client.file_download("sh600000", "gsgk.dat")
        assert data == chunk * 3
        assert [r[0] for r in pool.requests] == [0, 100, 200]

    def test_truncation_warns_by_default(self) -> None:
        """服务端只给一半 → 默认 UserWarning（含 total_len 信息）。"""
        pool = _FilePool(total_len=1000, chunks={0: b"\x01" * 500})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with pytest.warns(UserWarning, match="total_len=1000"):
            data = client.file_download("sh600000", "gsgk.dat")
        assert len(data) == 500

    def test_truncation_strict_raises(self) -> None:
        """strict=True → TruncatedDataError。"""
        pool = _FilePool(total_len=1000, chunks={0: b"\x01" * 500})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        with pytest.raises(TruncatedDataError):
            client.file_download("sh600000", "gsgk.dat", strict=True)

    def test_legacy_single_packet(self) -> None:
        """length>0 保持旧单包语义：单次请求返回片段，无循环。"""
        pool = _FilePool(total_len=1000, chunks={100: b"\x02" * 64})
        client = TdxClient(pool=pool)  # type: ignore[arg-type]
        data = client.file_download("sh600000", "gsgk.dat", offset=100, length=64)
        assert data == b"\x02" * 64
        assert len(pool.requests) == 1

    def test_async_mirror(self) -> None:
        """异步镜像：3 包拼装一致。"""

        class _AsyncFilePool(_FilePool):
            async def request(self, cmd, body, timeout=None):  # noqa: ANN001
                return _FilePool.request(self, cmd, body, timeout)

        chunk = b"\x03" * 50
        pool = _AsyncFilePool(total_len=150, chunks={0: chunk, 50: chunk, 100: chunk})
        client = AsyncTdxClient(pool=pool)  # type: ignore[arg-type]
        data = asyncio.run(client.file_download("sh600000", "gsgk.dat"))
        assert data == chunk * 3


# --------------------------------------------------------------------------- #
# DC1：bj 市场编号
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestBjMarketNumber:
    """DC1：北交所独立市场编号 2（0=深 1=沪 2=北）。"""

    def test_prefix_market_bj_is_2(self) -> None:
        assert _PREFIX_MARKET == {"sh": 1, "sz": 0, "bj": 2}

    def test_split_symbol_bj(self) -> None:
        assert to_tdx_market("bj430047") == (2, "430047")
        assert to_tdx_market("833171") == (2, "833171")  # 裸码推断北交所

    def test_quote_request_market_mapping(self) -> None:
        from tstdx.protocol.parsers.std7709 import Market, quote_request_market

        assert quote_request_market(Market.SZ) == 1
        assert quote_request_market(Market.SH) == 0
        # BJ 身份已知但 0x0530 request byte 未定标 → fail-closed，不再回声兜底
        # （ac5e9cd「fail closed on inferred extended request layouts」）。
        with pytest.raises(ParseError) as exc_info:
            quote_request_market(Market.BJ)
        assert exc_info.value.context["market"] == 2
        assert exc_info.value.context["verified_markets"] == [0, 1]
        with pytest.raises(ParseError):
            quote_request_market(3)

    def test_infer_market_bj(self) -> None:
        from tstdx.protocol.parsers.std7709 import Market, infer_market

        assert infer_market("430047") == Market.BJ
        assert infer_market("833171") == Market.BJ
        assert infer_market("920002") == Market.BJ
        assert infer_market("600000") == Market.SH  # 沪不回归
        assert infer_market("000651") == Market.SZ  # 深不回归

    def test_bj_symbol_bars_fails_closed_before_any_request(self) -> None:
        """bj 标的在发出任何请求前 fail-closed——既不写 2，也不静默夹取为 0/1。"""
        pool = _BodyCapture()
        client = TdxClient(pool=pool)  # type: ignore[arg-type]

        with pytest.raises(ParseError) as exc_info:
            client.bars("bj430047", period="day", count=3)

        assert exc_info.value.context["market"] == 2
        assert exc_info.value.context["verified_markets"] == [0, 1]
        assert pool.last_body is None  # 未发生任何传输 I/O


class _BodyCapture:
    """捕获请求体的最小 fake pool（0x052D 回 3 条记录）。"""

    def __init__(self) -> None:
        self.last_body: bytes | None = None

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        self.last_body = body
        return _frame(cmd, _bars_payload(3, start=0))
