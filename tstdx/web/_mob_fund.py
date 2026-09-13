# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""天天基金移动端共享工具：设备指纹 / 公共参数 / 响应归一化。

供 :mod:`tstdx.web.fund_rank`、:mod:`tstdx.web.fund_manager`、
:mod:`tstdx.web.fund_company` 复用。设备指纹与公共参数与
:mod:`tstdx.web.efinance_fund` **保持一致**（同一移动 App 客户端版本），
两处若需升级 App 版本须同步修改。

设计
----
* :func:`mob_headers` 返回移动端 UA + Referer，供 ``BaseWebSource`` 构造使用。
* :func:`mob_get_json` 统一封装 ``GET {MOB_BASE}/{path}`` + JSON 解析 +
  失败转 :class:`~tstdx.errors.SourceDeprecated`，三源共用同一容错口径。
* :func:`mob_rows` 兼容 ``Datas`` 为 list 或 ``{"fundStocks": [...]}`` 等
  嵌套形态，统一过滤出 dict 行。
* :func:`apply_fields` 按 ``输出键 -> (上游键, 类型)`` 映射表归一化一行，
  避免每个字段写重复的 ``num_f`` / ``num_i`` / ``s`` 判断。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..errors import SourceDeprecated
from .base import num_f, num_i
from .sources import FUND

__all__ = [
    "MOB_BASE",
    "DEVICE",
    "MOB_COMMON",
    "mob_headers",
    "mob_get_json",
    "mob_rows",
    "mob_rows_any",
    "apply_fields",
    "s",
]

#: 天天基金移动端基址。
MOB_BASE = "https://fundmobapi.eastmoney.com/FundMNewApi"
#: 固定设备指纹（移动端接口需要，非隐私字段）。
DEVICE = "3EA024C2-7F22-408B-95E4-383D38160FB3"
#: 公共参数串（直接拼在 query 尾部）。
MOB_COMMON = (
    f"&deviceid={DEVICE}&plat=Iphone&product=EFund&appType=ttjj&serverVersion=6.3.8&version=6.3.8"
)
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_3 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)


def s(value: Any) -> str:
    """None → 空串，其余转字符串。"""
    return "" if value is None else str(value)


def mob_headers() -> dict[str, str]:
    """移动端请求头（UA + Referer）。"""
    return {"User-Agent": _UA, "Referer": "http://fund.eastmoney.com/"}


def apply_fields(r: dict[str, Any], fields: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """按 ``输出键 -> (上游键, 类型)`` 映射表归一化一行。

    类型：``f`` 浮点（:func:`num_f`，默认 ``0.0``）/ ``i`` 整数
    （:func:`num_i`，默认 ``0``）/ 其他按字符串（:func:`s`）。
    """
    out: dict[str, Any] = {}
    for key, (up, kind) in fields.items():
        raw = r.get(up)
        if kind == "f":
            out[key] = num_f(raw)
        elif kind == "i":
            out[key] = num_i(raw)
        else:
            out[key] = s(raw)
    return out


def mob_rows(payload: dict[str, Any], key: str = "Datas") -> list[dict[str, Any]]:
    """从响应中取 ``Datas`` 并过滤出 dict 行。

    兼容三种形态：``Datas: [...]``、``Datas: {"fundStocks": [...]}``、
    ``Datas: {"list": [...]}``。
    """
    data = payload.get(key)
    if data is None:
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def mob_get_json(
    request_text: Callable[..., str],
    path: str,
    *,
    source: str = FUND,
    base: str | None = None,
) -> dict[str, Any]:
    """请求 ``{base}/{path}`` 并解析为 dict。

    Parameters
    ----------
    request_text:
        源的 :meth:`_request_text` 方法（``BaseWebSource`` 提供）。
    path:
        ``"FundMNRank?FundType=0&..."`` 形式（含 query，不含基址）。
    source:
        出错时上报的数据源名。
    base:
        基址覆盖；默认 :data:`MOB_BASE`。``FundMApi`` 与
        ``fundts.eastmoney.com`` 等非 ``FundMNewApi`` 端点需显式传入。
    """
    text = request_text(f"{base or MOB_BASE}/{path}", encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceDeprecated(
            "天天基金移动端接口返回非 JSON",
            context={"source": source, "path": path, "sample": text[:200]},
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise SourceDeprecated(
            "天天基金移动端接口响应非 JSON 对象",
            context={"source": source, "path": path, "sample": text[:200]},
        )
    return payload


def mob_rows_any(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """依次尝试多个 ``Datas`` 键名，返回第一个非空行列表。

    天天基金不同端点用 ``Datas`` 或 ``data`` 作为容器键，本函数统一兼容。
    """
    for key in keys:
        rows = mob_rows(payload, key)
        if rows:
            return rows
    return []
