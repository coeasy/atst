# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""官方通达信客户端 ``T0002/hq_cache`` 文本配置读取（只读，离线）。

覆盖两类 GBK 文本表，均来自本机 ``TdxW.exe`` 落盘数据，不启动 GUI、不抓内存：

``tdxbk.cfg``
    ``类型|简称|全称|标记``：板块名表。实测类型 ``1=概念``、``2=风格``、``3=指数``。
``tdxhy.cfg``
    ``市场|代码|行业码|||细分码``：股票→行业映射。市场 ``0=深``、``1=沪``、``2=北``。

这两张表**只提供名称与分类映射，不含成分股，也不含行情**：成分股文件
``block_gn.dat`` / ``block_zs.dat`` 在本版客户端未落盘。因此本模块刻意不伪装成
行情源，只把官方分类表变成结构化字典，调用方自行决定如何与在线数据对接。

设计约束
--------
* 只做解析：不新增 Provider、不新增 capability、不接入执行面（严格门禁计数不变）。
* 不静默猜测：字段数不符、市场码未知、代码非法一律按 ``strict`` 决定抛错或告警跳过。
* 量纲无关：本模块没有任何价格 / 成交量字段，不存在缩放口径问题。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..codec.primitive import decode_gbk, detect_encoding
from ..domain.symbol import normalize_symbol
from ..errors import DataFileNotFound, FileFormatError

__all__ = [
    "BoardConfigReader",
    "IndustryConfigReader",
    "BOARD_TYPE_NAMES",
    "MARKET_PREFIXES",
]

#: ``tdxbk.cfg`` 类型码 → 语义名（2026-11-25 本机实测样本）。
BOARD_TYPE_NAMES: dict[str, str] = {
    "1": "concept",
    "2": "style",
    "3": "index",
}

#: ``tdxhy.cfg`` 市场码 → ``atst`` 市场前缀（与 :meth:`Symbol.tdx_market` 同口径）。
MARKET_PREFIXES: dict[str, str] = {
    "0": "sz",
    "1": "sh",
    "2": "bj",
}


class _ConfigReader:
    """两类文本配置的公共骨架：编码探测、逐行切分、严格模式。"""

    EXPECTED_FIELDS: int = 0
    FILE_LABEL: str = ""

    def __init__(self, *, strict: bool = True, encoding: str | None = None) -> None:
        self.strict = strict
        self.encoding = encoding
        self.warnings: list[str] = []

    def _read_lines(self, path: str | Path) -> list[str]:
        file = Path(path)
        if not file.exists():
            raise DataFileNotFound(
                f"{self.FILE_LABEL}配置文件不存在: {file}", context={"path": str(file)}
            )
        raw = file.read_bytes()
        charset = self.encoding or detect_encoding(raw[:512])
        text = decode_gbk(raw, charset, strip_nul="tail")
        return text.splitlines()

    def _split_fields(self, line: str, lineno: int) -> list[str] | None:
        fields = [part.strip() for part in line.split("|")]
        if len(fields) != self.EXPECTED_FIELDS:
            message = (
                f"{self.FILE_LABEL}第 {lineno} 行字段数 {len(fields)} "
                f"不等于预期 {self.EXPECTED_FIELDS}: {line!r}"
            )
            if self.strict:
                raise FileFormatError(message, context={"line": lineno, "path": self.FILE_LABEL})
            self.warnings.append(message)
            return None
        return fields

    @staticmethod
    def _emit(rows: list[dict[str, Any]], output: str) -> Any:
        if output == "dict":
            return rows
        if output == "tuple":
            return [tuple(row[name] for name in rows[0]) for row in rows] if rows else []
        if output in ("df", "dataframe"):
            from ..domain.models import to_dataframe

            return to_dataframe(rows)
        return rows


class BoardConfigReader(_ConfigReader):
    """读取 ``tdxbk.cfg``：官方板块名表（概念 / 风格 / 指数）。

    输出字段：``type``（原始类型码）、``name``（简称）、``full_name``（全称）、
    ``flag``（客户端原始标记）、``kind``（类型码的语义名，未知类型回落 ``unknown``）。
    """

    EXPECTED_FIELDS = 4
    FILE_LABEL = "tdxbk.cfg"

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        rows: list[dict[str, Any]] = []
        for lineno, line in enumerate(self._read_lines(path), 1):
            if not line.strip():
                continue
            fields = self._split_fields(line, lineno)
            if fields is None:
                continue
            raw_type, name, full_name, flag = fields
            kind = BOARD_TYPE_NAMES.get(raw_type)
            if kind is None:
                message = f"tdxbk.cfg 第 {lineno} 行未知类型码 {raw_type!r}，不静默猜测"
                if self.strict:
                    raise FileFormatError(message, context={"line": lineno, "type": raw_type})
                self.warnings.append(message)
            if not name:
                message = f"tdxbk.cfg 第 {lineno} 行简称为空，跳过"
                if self.strict:
                    raise FileFormatError(message, context={"line": lineno})
                self.warnings.append(message)
                continue
            rows.append(
                {
                    "type": raw_type,
                    "name": name,
                    "full_name": full_name or name,
                    "flag": flag,
                    "kind": kind or "unknown",
                }
            )
        return self._emit(rows, output)


class IndustryConfigReader(_ConfigReader):
    """读取 ``tdxhy.cfg``：股票→行业映射。

    输出字段：``symbol``（canonical，如 ``sz000001``）、``market``（原始市场码）、
    ``code``（6 位代码）、``industry``（行业码）、``sub_industry``（细分码）。
    市场码未知或代码非法在 ``strict=True`` 下抛 :class:`FileFormatError`。
    """

    EXPECTED_FIELDS = 6
    FILE_LABEL = "tdxhy.cfg"

    def read(self, path: str | Path, *, output: str = "dict") -> Any:
        rows: list[dict[str, Any]] = []
        for lineno, line in enumerate(self._read_lines(path), 1):
            if not line.strip():
                continue
            fields = self._split_fields(line, lineno)
            if fields is None:
                continue
            raw_market, code, industry, _blank1, _blank2, sub_industry = fields
            prefix = MARKET_PREFIXES.get(raw_market)
            if prefix is None:
                message = f"tdxhy.cfg 第 {lineno} 行未知市场码 {raw_market!r}"
                if self.strict:
                    raise FileFormatError(message, context={"line": lineno, "market": raw_market})
                self.warnings.append(message)
                continue
            try:
                symbol = normalize_symbol(code, market=prefix)
            except Exception as exc:
                message = f"tdxhy.cfg 第 {lineno} 行非法代码 {code!r}: {exc}"
                if self.strict:
                    raise FileFormatError(message, context={"line": lineno, "code": code}) from exc
                self.warnings.append(message)
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "market": raw_market,
                    "code": code,
                    "industry": industry,
                    "sub_industry": sub_industry,
                }
            )
        return self._emit(rows, output)
