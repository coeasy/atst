# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Shared transport serialization for canonical v13 result contracts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from ..runtime.orchestration import OrchestratedResult

__all__ = ["jsonable", "serialize_result"]


def jsonable(value: Any) -> Any:
    #: 先看 ``to_dict``：Bar / Quote 恢复成 dataclass 之后 ``is_dataclass`` 同样为真，
    #: 但公开出口是扁平化的 dict（``extra`` 展开成顶层键），不能退化成 ``asdict``
    #: 的 ``extra`` 嵌套形状；两者的路径选择必须由契约出口优先。
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def _query_result(result: Any) -> dict[str, Any]:
    meta = result.meta
    provenance = meta.provenance
    provenance_fields = {
        "kind": provenance.kind.value,
        "observed_at_ns": provenance.observed_at_ns,
        "cache_tier": provenance.cache_tier,
        "fallback": provenance.fallback,
        "requested_provider": provenance.requested_provider,
    }
    #: P1-4（provider 身份修复）：upstream 只在聚合转手时下发——与 provider 同源
    #: 时保持旧 wire 形状（键缺失，而不是 null），已有消费者不必面对一个恒 None 的字段。
    if provenance.upstream is not None:
        provenance_fields["upstream"] = provenance.upstream
    return {
        "data": jsonable(result.data),
        "meta": {
            "provider": meta.provider,
            "channel": meta.channel,
            "capability": meta.capability,
            "fingerprint": meta.fingerprint,
            "provenance": provenance_fields,
            # 结果侧的"这条数据有瑕疵"必须和请求侧的入参一样逐字段到达 wire：
            # 缺一个键，HTTP/WS/MCP 的调用方就永远只能靠翻服务端日志知道这件事。
            "warnings": [item.to_dict() for item in meta.warnings],
        },
    }


def serialize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, OrchestratedResult):
        payload = _query_result(result.result)
        payload["attempts"] = [jsonable(item) for item in result.attempts]
        return payload
    return _query_result(result)
