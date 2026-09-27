# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""本地 vipdoc 二进制文件解析（§6）：.day / .lc1 / .lc5 / .dat / gpcw*.dat。

路径规则
--------
::

    vipdoc/sh/lday/sh600519.day     沪市日线
    vipdoc/sz/lday/sz000001.day     深市日线
    vipdoc/bj/lday/bj430047.day     北交所日线
    vipdoc/sh/minline/sh600519.lc1  沪市 1 分钟
    vipdoc/sz/fzline/sz000001.lc5   深市 5 分钟
    T0002/hq_cache/block_gn.dat     概念板块
    T0002/hq_cache/block_zs.dat     指数板块
    T0002/hq_cache/block_fg.dat     风格板块
    vipdoc/cw/gpcw.dat              财务数据

记录布局（32 字节，Little-Endian）
---------------------------------
**.day 日线**::

    0-3    uint32  日期 YYYYMMDD
    4-7    uint32  开盘价 × price_scale
    8-11   uint32  最高价 × price_scale
    12-15  uint32  最低价 × price_scale
    16-19  uint32  收盘价 × price_scale
    20-23  float32 成交额（单位由 amount_unit 决定）
    24-27  uint32  成交量（单位由 volume_unit 决定）
    28-31  uint32  上日收盘 × price_scale（**期货为持仓量**）

**.lc1 / .lc5 分钟线**::

    0-1    uint16  日期，lc16 编码：year=n//2048+2004, month=n%2048//100, day=n%2048%100
    2-3    uint16  从 0 点起的分钟数
    4-7    float32 开盘价
    8-11   float32 最高价
    12-15  float32 最低价
    16-19  float32 收盘价
    20-23  float32 成交额
    24-27  uint32  成交量（股）
    28-31  —       保留

.. note::
   不同资料对日线价格缩放（×100 vs ×1000）与成交量单位（股 vs 手）存在分歧。
   本模块**不做硬编码假设**：一切由 :class:`~atst.reader.profile.DataProfile`
   决定，缺省用 :class:`ProfileDetector` 自动探测并给出置信度。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..codec.primitive import decode_gbk, detect_encoding, get_datetime_from_lc, minutes_to_hhmm
from ..domain.finance import GPCW_FIELD_NAMES
from ..domain.models import Bar
from ..errors import DataFileNotFound, FileFormatError, TruncatedRecordError
from .profile import (
    BUILTIN_PROFILES,
    DataProfile,
    Market,
    Period,
    PriceEncoding,
    TimeEncoding,
    detect_profile,
)

__all__ = [
    "BaseFileReader",
    "DayBarReader",
    "MinBarReader",
    "BlockReader",
    "FinanceReader",
    "read_day_file",
    "read_min_file",
    "resolve_vipdoc_path",
]


class BaseFileReader:
    """本地文件读取器基类。"""

    RECORD_SIZE: int = 32

    def __init__(
        self,
        profile: DataProfile | str | None = None,
        *,
        auto_detect: bool = True,
        strict: bool = True,
        encoding: str | None = None,
    ) -> None:
        """
        Parameters
        ----------
        profile:
            ``DataProfile`` 实例或内置档案名。为 None 时使用默认档案。
        auto_detect:
            True 时在解析前用 :class:`ProfileDetector` 探测规格并合并到 profile。
        strict:
            True 时文件末尾不完整记录抛异常；False 时截断并告警。
        """
        self.profile = self._resolve_profile(profile)
        self.auto_detect = auto_detect
        self.strict = strict
        self.encoding = encoding
        self.warnings: list[str] = []

    @staticmethod
    def _resolve_profile(profile: DataProfile | str | None) -> DataProfile:
        if profile is None:
            return BUILTIN_PROFILES["a_share_day"]
        if isinstance(profile, str):
            if profile not in BUILTIN_PROFILES:
                raise KeyError(f"未知 profile: {profile}")
            return BUILTIN_PROFILES[profile]
        return profile

    # -- IO ---------------------------------------------------------------- #
    def _read_file(self, path: str | Path) -> bytes:
        p = Path(path)
        if not p.exists():
            raise DataFileNotFound(f"数据文件不存在: {p}", context={"path": str(p)})
        return p.read_bytes()

    def _prepare(self, raw: bytes) -> DataProfile:
        profile = self.profile
        if self.auto_detect:
            try:
                detected = detect_profile(raw, hint=profile)
                # 探测结果覆盖布局相关字段，市场/品种等语义字段沿用 hint
                profile = profile.with_overrides(
                    record_size=detected.record_size,
                    price_scale=detected.price_scale,
                    price_encoding=detected.price_encoding,
                    volume_unit=detected.volume_unit,
                    time_encoding=detected.time_encoding,
                    charset=detected.charset,
                    confidence=detected.confidence,
                )
                if detected.confidence < 0.8:
                    self.warnings.append(
                        f"规格探测置信度偏低 ({detected.confidence:.2f})，建议显式指定 profile"
                    )
            except Exception as exc:  # ProfileUndetectable
                self.warnings.append(f"自动探测失败，使用默认档案: {exc}")
        if self.encoding:
            profile = profile.with_overrides(charset=self.encoding)
        return profile

    def _iter_records(self, raw: bytes, size: int) -> Iterator[bytes]:
        n = len(raw)
        count = n // size
        remainder = n % size
        if remainder:
            msg = f"文件末尾存在 {remainder} 字节不完整记录（记录大小 {size}）"
            if self.strict:
                raise TruncatedRecordError(msg, context={"size": n, "remainder": remainder})
            self.warnings.append(msg + "，已截断")
        for i in range(count):
            yield raw[i * size : (i + 1) * size]

    # -- 输出三态 ----------------------------------------------------------- #
    @staticmethod
    def _output(rows: Sequence[Bar], output: str):
        from ..domain.models import to_dataframe, to_dicts, to_tuples

        if output == "dict":
            return to_dicts(rows)
        if output == "tuple":
            return to_tuples(rows)
        if output in ("df", "dataframe"):
            return to_dataframe(rows)
        if output == "model":
            return list(rows)
        raise ValueError(f"未知输出格式: {output!r}（可选 dict/tuple/dataframe/model）")


# --------------------------------------------------------------------------- #
# 日线
# --------------------------------------------------------------------------- #
class DayBarReader(BaseFileReader):
    """``.day`` 日线文件读取器。"""

    RECORD_SIZE = 32

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        raw = self._read_file(path)
        profile = self._prepare(raw)
        bars: list[Bar] = []
        for rec in self._iter_records(raw, self.RECORD_SIZE):
            bars.append(self._decode_record(rec, profile))
        return self._output(bars, output)

    def read_bytes(self, raw: bytes, *, profile: DataProfile | None = None) -> list[Bar]:
        prof = profile or self._prepare(raw)
        return [self._decode_record(r, prof) for r in self._iter_records(raw, self.RECORD_SIZE)]

    def _decode_record(self, rec: bytes, p: DataProfile) -> Bar:
        if len(rec) < self.RECORD_SIZE:
            raise FileFormatError(f"日线记录长度不足: {len(rec)} < {self.RECORD_SIZE}")
        (date_raw,) = struct.unpack_from("<I", rec, 0)
        if p.price_encoding == PriceEncoding.FLOAT32:
            o, h, lo, c = struct.unpack_from("<ffff", rec, 4)
            amount_raw = struct.unpack_from("<f", rec, 20)[0]
        else:
            o_raw, h_raw, lo_raw, c_raw = struct.unpack_from("<IIII", rec, 4)
            o, h, lo, c = (
                o_raw / p.price_scale,
                h_raw / p.price_scale,
                lo_raw / p.price_scale,
                c_raw / p.price_scale,
            )
            amount_raw = struct.unpack_from("<f", rec, 20)[0]
        (volume_raw,) = struct.unpack_from("<I", rec, 24)
        (last_field,) = struct.unpack_from("<I", rec, 28)

        volume = p.to_volume(volume_raw)
        amount = p.to_amount(amount_raw)

        extra: dict[str, Any] = {}
        if p.extra_fields and "open_interest" in p.extra_fields:
            extra["open_interest"] = int(last_field)
        else:
            extra["prev_close"] = round(
                last_field / p.price_scale
                if p.price_encoding != PriceEncoding.FLOAT32
                else last_field,
                4,
            )

        return Bar(
            datetime=self._decode_date(date_raw, p),
            open=round(float(o), 4),
            high=round(float(h), 4),
            low=round(float(lo), 4),
            close=round(float(c), 4),
            volume=int(volume),
            amount=round(float(amount), 2),
            extra=extra,
        )

    @staticmethod
    def _decode_date(raw: int, p: DataProfile) -> str:
        if p.time_encoding == TimeEncoding.LC16:
            y, m, d = get_datetime_from_lc(raw & 0xFFFF)
            return f"{y:04d}-{m:02d}-{d:02d}"
        if p.time_encoding == TimeEncoding.DATETIME32:
            y = (raw >> 20) & 0xFFF
            mo = (raw >> 16) & 0x0F
            d = (raw >> 11) & 0x1F
            hh = (raw >> 6) & 0x1F
            mi = raw & 0x3F
            return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mi:02d}"
        # 默认 YYYYMMDD
        y, mo, d = raw // 10000, (raw // 100) % 100, raw % 100
        if not (1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
            # 回退位域
            y = (raw >> 20) & 0xFFF
            mo = (raw >> 16) & 0x0F
            d = (raw >> 11) & 0x1F
        return f"{y:04d}-{mo:02d}-{d:02d}"


# --------------------------------------------------------------------------- #
# 分钟线
# --------------------------------------------------------------------------- #
class MinBarReader(BaseFileReader):
    """``.lc1`` / ``.lc5`` 分钟线文件读取器。"""

    RECORD_SIZE = 32

    def __init__(self, *args: Any, interval: int = 1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.interval = interval  # 1 或 5（分钟）

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        raw = self._read_file(path)
        profile = self._prepare(raw)
        bars = [self._decode_record(r, profile) for r in self._iter_records(raw, self.RECORD_SIZE)]
        return self._output(bars, output)

    def read_bytes(self, raw: bytes, *, profile: DataProfile | None = None) -> list[Bar]:
        prof = profile or self._prepare(raw)
        return [self._decode_record(r, prof) for r in self._iter_records(raw, self.RECORD_SIZE)]

    def _decode_record(self, rec: bytes, p: DataProfile) -> Bar:
        (date_raw,) = struct.unpack_from("<H", rec, 0)
        (minutes,) = struct.unpack_from("<H", rec, 2)
        year, month, day = get_datetime_from_lc(date_raw)
        hh, mm = minutes_to_hhmm(minutes)

        if p.price_encoding == PriceEncoding.FLOAT32:
            o, h, lo, c, amount_raw = struct.unpack_from("<fffff", rec, 4)
        else:
            o_raw, h_raw, lo_raw, c_raw = struct.unpack_from("<IIII", rec, 4)
            scale = p.price_scale or 1
            o, h, lo, c = o_raw / scale, h_raw / scale, lo_raw / scale, c_raw / scale
            (amount_raw,) = struct.unpack_from("<f", rec, 20)
        (volume_raw,) = struct.unpack_from("<I", rec, 24)

        return Bar(
            datetime=f"{year:04d}-{month:02d}-{day:02d} {hh:02d}:{mm:02d}",
            open=round(float(o), 4),
            high=round(float(h), 4),
            low=round(float(lo), 4),
            close=round(float(c), 4),
            volume=int(p.to_volume(volume_raw)),
            amount=round(float(p.to_amount(amount_raw)), 2),
        )


# --------------------------------------------------------------------------- #
# 板块文件
# --------------------------------------------------------------------------- #
class BlockReader(BaseFileReader):
    """``block_*.dat`` 板块文件读取器（概念/指数/风格）。

    支持两种模式：

    ``flat``
        扁平结构：每个板块 = ``名称(定长) + uint16 数量 + 代码(6 字节 × n)``
    ``group``
        分组结构：板块之间可嵌套（通达信自定义板块）

    ``name_length`` 默认为 9 字节（GBK，NUL 填充）；若文件结构不自洽，
    自动在 ``AUTO_NAME_LENGTHS`` 候选中探测。
    """

    #: 板块名定长候选（字节）
    AUTO_NAME_LENGTHS: tuple[int, ...] = (9, 8, 16, 32, 24)

    def __init__(
        self, *args: Any, name_length: int | None = None, group: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.name_length = name_length
        self.group = group

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        raw = self._read_file(path)
        charset = self.encoding or detect_encoding(raw[:512])
        blocks = self._parse(raw, charset)
        if output == "dict":
            return [{"name": n, "count": len(c), "codes": c} for n, c in blocks]
        if output == "tuple":
            return [(n, tuple(c)) for n, c in blocks]
        if output in ("df", "dataframe"):
            from ..domain.models import to_dataframe

            return to_dataframe([{"block": n, "code": c} for n, codes in blocks for c in codes])
        return blocks

    def _parse(self, raw: bytes, charset: str) -> list[tuple[str, list[str]]]:
        name_len = self.name_length or self._detect_name_length(raw, charset)
        blocks: list[tuple[str, list[str]]] = []
        pos = 0
        n = len(raw)
        while pos + name_len + 2 <= n:
            name = decode_gbk(raw[pos : pos + name_len], charset)
            pos += name_len
            if pos + 2 > n:
                break
            (count,) = struct.unpack_from("<H", raw, pos)
            pos += 2
            need = count * 6
            if pos + need > n:
                self.warnings.append(f"板块 {name!r} 声明 {count} 个代码但数据不足，已截断")
                need = n - pos
                count = need // 6
            codes: list[str] = []
            for _ in range(count):
                code = decode_gbk(raw[pos : pos + 6], charset)
                pos += 6
                if code:
                    codes.append(code)
            if name or codes:
                blocks.append((name, codes))
            # group 模式：每个板块块之间有 2 字节分隔
            if self.group and pos + 2 <= n:
                pos += 2
        return blocks

    def _detect_name_length(self, raw: bytes, charset: str) -> int:
        """选择能产生最多可解码板块名的定长。"""
        best_len, best_score = self.AUTO_NAME_LENGTHS[0], -1.0
        for name_len in self.AUTO_NAME_LENGTHS:
            blocks = self._try_parse(raw, name_len, charset)
            if not blocks:
                continue
            score = sum(
                1 for name, codes in blocks if name and all(c.isprintable() for c in name) and codes
            )
            if score > best_score:
                best_len, best_score = name_len, score
        return best_len

    def _try_parse(self, raw: bytes, name_len: int, charset: str):
        saved = self.name_length, self.warnings
        self.name_length, self.warnings = name_len, []
        try:
            return self._parse(raw, charset)
        except Exception:
            return []
        finally:
            self.name_length, self.warnings = saved


# --------------------------------------------------------------------------- #
# 财务数据
# --------------------------------------------------------------------------- #
class FinanceReader(BaseFileReader):
    """``gpcw*.dat`` 财务数据读取器。

    文件为 float32 数组的扁平序列，每只证券占用固定数量的字段。
    字段数因通达信版本而异，候选集见 :data:`FIELD_COUNTS`；
    也可用 ``fields_per_record`` 显式指定。
    """

    FIELD_COUNTS: tuple[int, ...] = (28, 30, 32, 36, 40, 44, 50, 58, 67, 71, 79, 88, 100)

    #: 语义字段序（F1：gpcw 语义化，见 :mod:`atst.domain.finance`）
    FIELD_NAMES = GPCW_FIELD_NAMES

    def __init__(self, *args: Any, fields_per_record: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields_per_record = fields_per_record

    def _read_records(self, path: str | Path) -> tuple[list[list[float]], int]:
        """读取 gpcw 文件，返回 ``(每记录 float 数组, 字段数)``。"""
        raw = self._read_file(path)
        n = len(raw)
        count = self.fields_per_record or self._detect_fields(n)
        record_size = count * 4
        records: list[list[float]] = []
        for rec in self._iter_records(raw, record_size):
            records.append([round(x, 6) for x in struct.unpack(f"<{count}f", rec)])
        return records, count

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        records, count = self._read_records(path)
        if output == "tuple":
            return [tuple(r) for r in records]
        if output in ("df", "dataframe"):
            from ..domain.models import to_dataframe

            return to_dataframe(
                [dict(zip((f"f{i}" for i in range(count)), r, strict=False)) for r in records]
            )
        return [{"values": r} for r in records]

    def read_indicators(self, path: str | Path, *, output: str = "dict") -> Any:
        """gpcw 语义化读取（F1）：每条记录映射为带字段名的财务指标。

        输出字段名见 :data:`FinanceReader.FIELD_NAMES`（与
        :data:`atst.domain.finance.GPCW_FIELD_NAMES` 同源）；未被
        FIELD_NAMES 覆盖的索引保留为 ``f{n}``。

        ``output``：``dict``（默认，list[dict]）/ ``tuple`` / ``dataframe`` /
        ``model``（list[:class:`~atst.domain.models.Bar`]，datetime 置空）。
        """
        from ..domain.finance import map_finance_values

        records, _ = self._read_records(path)
        named = [
            {**{"code": "", "date": ""}, **map_finance_values(r, self.FIELD_NAMES)} for r in records
        ]
        if output == "tuple":
            return [tuple(r.values()) for r in named]
        if output in ("df", "dataframe"):
            from ..domain.models import to_dataframe

            return to_dataframe(named)
        if output == "model":
            from ..domain.models import Bar

            return [Bar(datetime=str(d.get("date", "")), extra=dict(d)) for d in named]
        return named

    def _detect_fields(self, n: int) -> int:
        for c in self.FIELD_COUNTS:
            if n % (c * 4) == 0 and n // (c * 4) >= 1:
                return c
        # 回退：假设单条记录
        return max(1, n // 4)


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #
def resolve_vipdoc_path(
    vipdoc_root: str | Path,
    code: str,
    period: str = Period.DAY,
    *,
    market: str | None = None,
) -> Path:
    """按通达信目录规则拼出数据文件路径。

    Parameters
    ----------
    vipdoc_root:
        ``.../vipdoc`` 目录。
    code:
        形如 ``sh600519`` / ``600519`` / ``600519.SH`` / ``sz000001``。
        统一经 :func:`atst.domain.symbol.split_symbol` 归一化后拼路径
        （单一事实源；此前内联 ``code[:2]`` 判断会把 ``600519.SH`` 拼成
        含后缀的错路径，审计 §2-1）。
    period:
        :class:`~atst.reader.profile.Period` 之一。
    """
    from ..domain.symbol import parse_symbol
    from ..errors import TdxError

    code = code.strip()
    if market is None:
        try:
            sym = parse_symbol(code)
            market, symbol = sym.market, sym.code
        except TdxError:
            # 无法解析的代码退回启发式（保持旧行为：默认按沪市处理）
            market, symbol = _guess_market(code), code
    else:
        try:
            sym = parse_symbol(code, market=market)
            market, symbol = sym.market, sym.code
        except TdxError:
            symbol = code

    root = Path(vipdoc_root)
    if period == Period.DAY:
        return root / market / "lday" / f"{market}{symbol}.day"
    if period == Period.M1:
        return root / market / "minline" / f"{market}{symbol}.lc1"
    if period == Period.M5:
        return root / market / "fzline" / f"{market}{symbol}.lc5"
    raise ValueError(f"周期 {period} 无对应本地文件格式（仅 day/1min/5min 有本地文件）")


def _guess_market(code: str) -> str:
    """按代码前缀猜测市场（委托统一符号引擎，见 :mod:`atst.domain.symbol`）。

    委托动机（审计 §2-1）：旧实现的 ``lstrip("sz")`` 是**字符集**剥离而非
    前缀剥离，且 ``9`` 前缀把北交所 ``920001`` 误判沪市——与符号引擎给出
    相反市场号。无法解析时保持旧兜底（沪市）。
    """
    from ..domain.symbol import parse_symbol
    from ..errors import TdxError

    try:
        return parse_symbol(code).market
    except TdxError:
        return Market.SH


def read_day_file(
    path: str | Path, *, profile: DataProfile | str | None = None, output: str = "dict"
) -> Any:
    return DayBarReader(profile).read(path, output=output)


def read_min_file(
    path: str | Path,
    *,
    interval: int = 1,
    profile: DataProfile | str | None = None,
    output: str = "dict",
) -> Any:
    return MinBarReader(profile, interval=interval).read(path, output=output)
