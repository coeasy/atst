# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""统一 API 响应形态（接口面吸收，tstdx 自有实现）。

形态对齐业界通行约定：``success / error / data / extra`` 四要素 + 可选
``.df``（pandas）转换。这是**接口契约**而非协议实现——tstdx 的协议/数据
路径完全保持自有实现，仅对外统一返回包裹，便于跨库迁移与工具链对接。

设计要点
--------
* **零硬依赖**：pandas 仅在调用 ``.df`` 时才惰性导入（未装则抛
  :class:`~tstdx.errors.DependencyMissingError`）。
* **可序列化**：:meth:`to_dict` 输出纯 JSON 友好结构（dataclass/Quote/
  datetime 递归转换）。
* **空结果合法**：``success=True`` 且 ``data=[]`` 是合法状态（如停牌日
  空 K 线），与「请求失败」（``success=False``）严格区分。
* **真值语义**：``bool(resp) == resp.success``。

用法
----
::

    from tstdx.facade.response import ok, err, ApiResponse

    resp = ok(data=rows, extra={"source": "tencent"})   # success=True
    resp = err("主站不可达", code="E2000")               # success=False
    assert resp.success and resp.data and resp.df is not None
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..errors import DependencyMissingError, TdxError

__all__ = [
    "ApiResponse",
    "ok",
    "err",
    "from_result",
    "wrap",
]


def _serialize(value: Any) -> Any:
    """递归转成 JSON 友好值（dict/list/dataclass/datetime/枚举）。"""
    if isinstance(value, Mapping):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _serialize(value.to_dict())
    # dataclass / 普通对象：尝试 asdict
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return _serialize(asdict(value))
    return value


@dataclass
class ApiResponse:
    """统一 API 响应。

    Attributes
    ----------
    success:
        请求是否成功（含「成功但空」）。
    error:
        失败原因（``success=True`` 时通常为空串）。
    data:
        结果数据：``list[dict]`` / ``dict`` / 标量 / ``None``。
    extra:
        附加元信息（源、延迟、服务端计数等）。
    code:
        可选错误码（tstdx 错误体系编码，如 ``E2000``）。
    """

    success: bool = True
    error: str = ""
    data: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
    code: str = ""

    def __bool__(self) -> bool:
        return self.success

    @property
    def df(self) -> Any:
        """转换为 pandas DataFrame（惰性导入；未装 pandas 抛异常）。

        规则：``list[dict]`` → DataFrame；``dict`` → 单行 DataFrame；
        其他类型 → 空 DataFrame。
        """
        try:
            import pandas as pd
        except ImportError as exc:
            raise DependencyMissingError(
                "ApiResponse.df 需要 pandas: pip install 'tstdx[dataframe]'",
                cause=exc,
            ) from exc
        if (
            isinstance(self.data, list)
            and self.data
            and all(isinstance(i, Mapping) for i in self.data)
        ):
            # 显式传入「键并集」列：不依赖 pandas 版本的列推断行为——
            # 空 dict 行不丢列，跨版本行为稳定（P2 打磨）。
            columns: list[Any] = []
            for item in self.data:
                for k in item:
                    if k not in columns:
                        columns.append(k)
            if columns:
                return pd.DataFrame(self.data, columns=columns)
            return pd.DataFrame(self.data)
        if isinstance(self.data, list):
            return pd.DataFrame(self.data)
        if isinstance(self.data, Mapping):
            return pd.DataFrame([dict(self.data)])
        return pd.DataFrame()

    def to_dict(self) -> dict[str, Any]:
        """JSON 友好序列化（不含 DataFrame 转换）。"""
        return {
            "success": self.success,
            "error": self.error,
            "code": self.code,
            "data": _serialize(self.data),
            "extra": _serialize(self.extra),
        }

    def __repr__(self) -> str:
        n = len(self.data) if isinstance(self.data, (list, tuple)) else "-"
        return (
            f"ApiResponse(success={self.success}, error={self.error!r}, "
            f"data_count={n}, extra={list(self.extra)})"
        )


def ok(data: Any = None, *, extra: Mapping[str, Any] | None = None, code: str = "") -> ApiResponse:
    """构造成功响应（含合法空结果）。"""
    return ApiResponse(
        success=True,
        error="",
        data=data,
        extra=dict(extra or {}),
        code=code,
    )


def err(
    message: str, *, code: str = "", extra: Mapping[str, Any] | None = None, data: Any = None
) -> ApiResponse:
    """构造失败响应。"""
    return ApiResponse(
        success=False,
        error=str(message),
        data=data,
        extra=dict(extra or {}),
        code=code,
    )


def from_result(
    result: Any, *, extra: Mapping[str, Any] | None = None, code: str = ""
) -> ApiResponse:
    """把任意调用结果包裹成 :class:`ApiResponse`。

    本函数**自身不做异常捕获**，只按传入值的类型归类（P2 边界澄清）：

    - ``ApiResponse`` 原样返回。
    - **异常实例**（``BaseException`` 实例——注意含 ``KeyboardInterrupt`` /
      ``SystemExit`` 等非 ``Exception`` 成员，仅当调用方显式传入实例时才会
      走到这里）：``TdxError`` 实例转 ``success=False``（保留 code/extra）；
      其他异常实例转通用错误（``code="E9999"``）。是否捕获异常由调用方
      决定——:func:`wrap` 与 ``UnifiedQuoteAPI.query`` 只捕 ``Exception``，
      ``BaseException`` 其余成员会照常外溢。
    - 其余正常值 → ``success=True``。
    """
    if isinstance(result, ApiResponse):
        return result
    if isinstance(result, BaseException):
        if isinstance(result, TdxError):
            return err(
                str(result),
                code=getattr(result, "code", "") or code,
                extra={**dict(extra or {}), **dict(getattr(result, "context", {}) or {})},
            )
        return err(str(result), code=code or "E9999", extra=dict(extra or {}))
    return ok(result, extra=extra, code=code)


def wrap(fn: Any, *args: Any, **kwargs: Any) -> ApiResponse:
    """调用 ``fn(*args, **kwargs)`` 并把结果/异常统一包裹成响应。

    供高层门面作为「响应化」薄封装：::

        resp = wrap(api.quotes, ["sh600519"])
    """
    try:
        return from_result(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 —— 门面层统一兜底
        return from_result(exc)
