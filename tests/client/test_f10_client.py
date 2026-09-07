"""F10 资料网关客户端测试（N1：catalog 客户端方法 + P9 get_client 显式拒绝）。

覆盖：
* ``F10Client.catalog`` / ``AsyncF10Client.catalog`` —— fake pool 注入合成
  0x0001 目录载荷，断言解析结果与请求体形状；
* ``F10Client.parse_text`` —— GBK 正文栏目切分；
* ``get_client`` 未知 kind 显式抛 ``ValueError``（P9）。
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from tstdx.client import AsyncF10Client, F10Client, TdxClient, get_client
from tstdx.codec.framing import ResponseFrame


def _catalog_payload() -> bytes:
    """合成 0x0001 F10_CATALOG 响应载荷：``<H count>`` + 每条
    ``<8s 栏目名(gbk)><8s 文件名(gbk)>``（RECORD_SIZE=16）。"""
    records = [
        (b"\xb9\xab\xcb\xbe\xb8\xc5\xbf\xf6", b"gsgk.dat"),  # 公司概况
        (b"\xb2\xc6\xce\xf1\xb7\xd6\xce\xf6", b"cwdz.dat"),  # 财务分析
    ]
    body = struct.pack("<H", len(records))
    for title, filename in records:
        body += title.ljust(8, b"\x00") + filename.ljust(8, b"\x00")
    return body


class _FakePool:
    """同步 fake pool：记录请求体并返回合成 0x0001 帧。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.last_cmd: int | None = None
        self.last_body: bytes | None = None

    def request(self, cmd: int, body: bytes, timeout: float = 5.0) -> ResponseFrame:
        self.last_cmd = cmd
        self.last_body = body
        return ResponseFrame(
            magic=0x0074CBB1,
            zip_flag=0,
            seq=0,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=self.payload,
        )


class _AsyncFakePool:
    """异步 fake pool：镜像 _FakePool。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.last_cmd: int | None = None
        self.last_body: bytes | None = None

    async def request(self, cmd: int, body: bytes, timeout: float = 5.0) -> ResponseFrame:
        self.last_cmd = cmd
        self.last_body = body
        return ResponseFrame(
            magic=0x0074CBB1,
            zip_flag=0,
            seq=0,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=self.payload,
        )


@pytest.mark.unit
class TestF10Catalog:
    """F10Client.catalog 客户端方法（N1）。"""

    def test_catalog_parses_rows(self) -> None:
        pool = _FakePool(_catalog_payload())
        client = F10Client(pool=pool)  # type: ignore[arg-type]
        rows = client.catalog("600000")
        assert pool.last_cmd == 0x0001
        assert len(rows) == 2
        assert rows[0]["title"] == "公司概况"
        assert rows[0]["filename"] == "gsgk.dat"
        assert rows[1]["title"] == "财务分析"
        assert rows[1]["filename"] == "cwdz.dat"

    def test_catalog_request_body_prefix(self) -> None:
        """请求体为标准证券前缀 ``<6s code><H market>``。"""
        pool = _FakePool(_catalog_payload())
        client = F10Client(pool=pool)  # type: ignore[arg-type]
        client.catalog("600000")
        assert pool.last_body is not None
        # 沪市 market=1；600000 代码 6 字节 + uint16 market
        code, market = struct.unpack("<6sH", pool.last_body[:8])
        assert code == b"600000"
        assert market == 1

    def test_catalog_shenzhen_market(self) -> None:
        """深市（sz 前缀）→ market=0。"""
        pool = _FakePool(_catalog_payload())
        client = F10Client(pool=pool)  # type: ignore[arg-type]
        client.catalog("sz000001")
        assert pool.last_body is not None
        _, market = struct.unpack("<6sH", pool.last_body[:8])
        assert market == 0

    def test_parse_text_sections(self) -> None:
        """F10Client.parse_text 按【栏目】切分 GBK 正文。"""
        client = F10Client(pool=_FakePool(b""))  # type: ignore[arg-type]
        raw = "【公司概况】茅台【财务分析】营收增长".encode("gbk")
        sections = client.parse_text(raw)
        assert [s.title for s in sections] == ["公司概况", "财务分析"]
        assert "茅台" in sections[0].text

    def test_download_delegates_to_file_download(self) -> None:
        """F10Client.download 委托 file_download（0x06B9）。"""
        pool = _FakePool(b"\x00\x00")
        client = F10Client(pool=pool)  # type: ignore[arg-type]
        client.download("600000", "gsgk.dat")
        assert pool.last_cmd == 0x06B9

    def test_async_catalog_parity(self) -> None:
        """AsyncF10Client.catalog 异步镜像返回同形状数据。"""
        pool = _AsyncFakePool(_catalog_payload())
        client = AsyncF10Client(pool=pool)  # type: ignore[arg-type]

        async def _run() -> list:
            return await client.catalog("600000")

        rows = asyncio.run(_run())
        assert pool.last_cmd == 0x0001
        assert len(rows) == 2
        assert rows[0]["title"] == "公司概况"


@pytest.mark.unit
class TestGetClientUnknownKind:
    """get_client 未知 kind 显式拒绝（P9）。"""

    def test_unknown_kind_raises_value_error(self) -> None:
        # 运行时传入任意 str（绕过 overload 字面量约束）验证显式拒绝
        with pytest.raises(ValueError, match="未知客户端 kind"):
            get_client("bogus")  # type: ignore[call-overload]

    def test_known_kinds_resolve(self) -> None:
        """合法 kind 均解析到对应客户端类型（注入 fake pool 免建连接）。"""
        assert isinstance(get_client("stock", pool=_FakePool(b"")), TdxClient)
        assert isinstance(get_client("f10", pool=_FakePool(b"")), F10Client)
