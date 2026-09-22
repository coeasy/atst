# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""解码告警的通用接线：``bars`` 之外的分派点也必须把判断送上 wire（F-63①）。

第 26 步（F-51）只把 ``bars`` 一条命令的解码判断接进了告警通道，其余 14 个
``dispatch`` 点仍然是 ``return result.rows``——``guarded_count`` 在 8 个解析器模块
里有 43 个调用点会产出"声明 N 实收 M"这类判断，它们全部落在 ``ParseResult.warnings``
袋里没人读。这里挑 **非 bars** 的三张面（通用 ``request`` 口、单跳的
``capital_changes``、异步侧）各打一条真实形状，全部离线（fake pool 注入传输层）。
"""

from __future__ import annotations

import inspect
import json
import struct
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from tstdx.client import AsyncTdxClient  # noqa: E402
from tstdx.client.sync import MacClient, TdxClient  # noqa: E402
from tstdx.codec.framing import ResponseFrame  # noqa: E402
from tstdx.diagnostics import WarningCode, warning_sink  # noqa: E402

_MAGIC = 0x0074CBB1

#: 声明 300 条却只回 30 个字节：任何走 ``guarded_count`` 的解析器都会因容量不足
#: 钳制，并留下一条判断——这是线路可复现的形状（0x052D 空桩即此族，见 F-60）。
_CLAMP = struct.pack("<H", 300) + b"\x00" * 30


def _frame(cmd: int, payload: bytes) -> ResponseFrame:
    return ResponseFrame(
        magic=_MAGIC, zip_flag=0, seq=1, method=cmd, zip_size=0, unzip_size=0, payload=payload
    )


class _TablePool:
    """按命令号回固定载荷；未预置的命令直接报错（不许测试悄悄走别的面）。"""

    def __init__(self, table: dict[int, bytes]) -> None:
        self.table = table
        self.requests: list[int] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001, ARG002
        if cmd not in self.table:
            raise AssertionError(f"未预置命令 {cmd:#06x}")
        self.requests.append(cmd)
        return _frame(cmd, self.table[cmd])


@pytest.mark.unit
class TestDecodeCaveatWiring:
    """F-63①：非 bars 分派点的解码判断要能上 wire，且不改动数据本身。"""

    def test_request_mouth_forwards_the_clamp_verdict(self) -> None:
        """``request`` 通用口（十几个公共方法共用）：判断要在 sink 里，不只是在袋里。

        0x120F 的记录是 10B，声明 300 条只够 3 条——钳制判断此前随 ``result.rows``
        一起返回， ``warnings`` 那格没人读。
        """
        client = MacClient(pool=_TablePool({0x120F: _CLAMP}))  # type: ignore[arg-type]
        with warning_sink() as caveats:
            rows = client.block_list(0, 0)
        assert [item.code for item in caveats] == [WarningCode.DECODE_CAVEAT]
        assert caveats[0].message == (
            "request(0x120f) 解码：count 失真已钳制：声明 300 条，按剩余字节 10B/条 只能容纳 3 条"
        )
        #: 接线只是把判断搬到明面上：取回的数据一条不多一条不少。
        assert len(rows) == 3

    def test_clean_page_stays_silent(self) -> None:
        """声明数与载荷相符 → 一条告警都不许有：接线不能变成 stderr 上的噪声。"""
        client = MacClient(pool=_TablePool({0x120F: struct.pack("<H", 2) + b"\x00" * 20}))  # type: ignore[arg-type]
        with warning_sink() as caveats:
            rows = client.block_list(0, 0)
        assert caveats == []
        assert len(rows) == 2

    def test_degradation_verdicts_reach_the_wire_too(self) -> None:
        """L1 精确解析失败 → 降级启发式这一族判断同样在袋里：现在要看得见。

        ``capital_changes``（0x000F）是 F-37 未闭合的那半边（字段布局错位）——这条
        判据因此也是它的取证口：以前只能读 ``ParseResult.warnings``，现在公共 API
        的调用方与 wire 上的 ``meta.warnings`` 都能读到。
        """
        client = TdxClient(pool=_TablePool({0x000F: _CLAMP}))  # type: ignore[arg-type]
        with warning_sink() as caveats:
            client.capital_changes("sh600000")
        assert caveats, "0x000F 的畸形载荷不可能干净解析，一条判断都没有＝接线断了"
        assert {item.code for item in caveats} == {WarningCode.DECODE_CAVEAT}
        assert all(
            item.message.startswith("capital_changes('sh600000') 0x000f 解码：") for item in caveats
        ), [item.message for item in caveats]
        assert any("降级" in item.message for item in caveats), [item.message for item in caveats]

    def test_caveat_blames_the_caller_not_the_library(self) -> None:
        """归属行必须是发起调用的那一行：``_op_call`` 嵌套多跳也不许留在库里。

        这条判据盯的是转发口的 ``stacklevel``：写死常量在单跳（``_t_bars``）恰好对，
        在 ``block_list`` → ``request`` → ``request_result`` 这条三跳路径上会把缺陷
        指给 :file:`_mixin.py` 自己。
        """
        client = MacClient(pool=_TablePool({0x120F: _CLAMP}))  # type: ignore[arg-type]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            marker_line = _marker_line()
            client.block_list(0, 0)
        decode = [w for w in caught if str(w.message).startswith("[decode_caveat]")]
        assert len(decode) == 1
        assert decode[0].filename == __file__, (
            f"告警归属到了 {decode[0].filename}:{decode[0].lineno}，公共 API 的调用方看不到自己该看的那行"
        )
        assert decode[0].lineno == marker_line + 1

    def test_async_side_forwards_the_same_fact(self) -> None:
        """异步侧同一判据：同步/异步共享同一模板，告警不得只在一侧生效。"""
        import asyncio

        class _AsyncTablePool(_TablePool):
            async def request(self, cmd, body, timeout=None):  # noqa: ANN001
                return _TablePool.request(self, cmd, body, timeout)

        client = AsyncTdxClient(pool=_AsyncTablePool({0x000F: _CLAMP}))  # type: ignore[arg-type]
        with warning_sink() as caveats:
            asyncio.run(client.capital_changes("sh600000"))
        assert [item.code for item in caveats] == [WarningCode.DECODE_CAVEAT] * len(caveats)
        assert caveats and all(
            item.message.startswith("capital_changes('sh600000') 0x000f 解码：") for item in caveats
        )


def _marker_line() -> int:
    """返回调用它的那一行行号（下一行才是被测调用，见唯一调用点）。"""
    return inspect.getframeinfo(inspect.currentframe().f_back).lineno


# --------------------------------------------------------------------------- #
# G7：值域尺子的判断也要从同一个转发口上 wire
# --------------------------------------------------------------------------- #

_GOLDEN_QUOTATION = Path(__file__).resolve().parents[1] / "golden" / "quotation"


def _real_payload(sample_dir: str) -> tuple[int, bytes]:
    """取该样本目录里第一份实采载荷。

    只认 ``self-captured``：synthetic 副本的 payload 是解析器自己产出的形状，拿它当
    证据等于让被告给原告作证——这条越域告警的两端（该响的要响、不该响的不响）都必须
    由真机样本说话。
    """
    from tstdx.tools.golden_audit import ORIGIN_REAL, classify_origin

    for meta_path in sorted((_GOLDEN_QUOTATION / sample_dir).glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if classify_origin(meta.get("source")) != ORIGIN_REAL:
            continue
        return int(meta["command"], 16), (meta_path.parent / "payload.bin").read_bytes()
    raise AssertionError(f"{sample_dir} 里没有实采样本，本判据的正/负控都失去证据")


@pytest.mark.unit
class TestDomainCaveatReachesTheWire:
    """F-37/G3 的余下半边（G7）：布局错位解出的值，调用方此前只能读源码才知道。

    盘前一轮真机复现（2026-09-22 10:45）里 ``capital_changes``/``finance`` 回的是
    ``provenance.kind=DIRECT``、``warnings`` 为空的错值——形状与一条干净结果一字不差。
    """

    @pytest.mark.parametrize(
        ("sample_dir", "method"),
        [
            ("0x000f_capital_changes_600000", "capital_changes"),
            ("0x0010_finance_info_600000", "finance_info"),
        ],
    )
    def test_a_real_misaligned_page_says_so_on_the_wire(self, sample_dir, method) -> None:
        cmd, payload = _real_payload(sample_dir)
        client = TdxClient(pool=_TablePool({cmd: payload}))  # type: ignore[arg-type]
        with warning_sink() as caveats:
            rows = getattr(client, method)("sh600000")
        assert rows, "实采样本解不出行，本判据的负例那一半是空的"
        codes = [item.code for item in caveats]
        assert WarningCode.FIELD_OUT_OF_DOMAIN in codes, (
            f"{sample_dir} 的实采载荷解出了越域值，告警却没上 wire：{codes}"
        )
        caveat = next(item for item in caveats if item.code is WarningCode.FIELD_OUT_OF_DOMAIN)
        assert f"0x{cmd:04x}" in caveat.message, caveat.message
        #: 例子要能定位：只有计数的告警等于把 250 行错值写成一句"有问题"。
        assert "row[" in caveat.message, caveat.message

    #: 反向证据（"别给干净结果制造噪声"）分两处判：本文件既有的
    #: :meth:`TestDecodeCaveatWiring.test_clean_page_stays_silent` 钉住公共 API 一条告警
    #: 都不许多发，:func:`tests.unit.test_domain_integrity.test_the_ruler_stays_silent_on_every_other_captured_command`
    #: 钉住全部实采样本。
