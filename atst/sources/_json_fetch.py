# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""sources 公共 JSON 取数器（P1-8/F9：4 份独立 ``_get_json`` 上收）。

此前 :mod:`~atst.sources.baidu.adapters`（百度财经）、
:mod:`~atst.sources.efinance_fund`（基金移动端）、
:mod:`~atst.sources.adapters_fund`（基金历史净值）与 :mod:`~atst.sources.esg`
（新浪 ESG）各持一份近似 ``_get_json``：请求 → JSON 解析 → 非 JSON 报错
→（可选）dict 校验，报错文案 / 样本长度 / 失败桶口径各自漂移。

本模块收拢为单一 :func:`fetch_json`：解析与报错一处实现，差异经 hooks 注入——

* ``send``——传输 hook：``url -> HttpResponse``。走重试内核的传
  :meth:`~atst.sources._base_core.BaseWebSource._request_http`；
  单主机直连的（新浪 ESG）传自带传输异常包装的闭包；
* ``label`` / ``non_json_msg`` / ``require_dict``——报错文案与结构校验；
* ``status_error``——HTTP 非 2xx 时的异常构造（含失败桶记账）；
* ``record_parse_failure`` / ``reset_on_success``——失败桶口径
  （新浪 ESG 记解析桶并在成功后清零；其余三处沿用 ``_request_http``
  内部记账，此处不动桶）。

行为契约：改造前后各调用方报错类型、文案、context、失败桶语义完全一致。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ..errors import SourceDeprecated, WebSourceError
from ._base_http import HttpResponse

__all__ = ["fetch_json"]


def fetch_json(
    src: Any,
    url: str,
    *,
    send: Callable[[str], HttpResponse],
    label: str,
    require_dict: bool = True,
    encoding: str = "utf-8",
    error_context: Mapping[str, Any] | None = None,
    status_error: Callable[[HttpResponse], Exception] | None = None,
    record_parse_failure: bool = False,
    reset_on_success: bool = False,
    non_json_msg: str | None = None,
    non_json_sample_len: int = 200,
) -> Any:
    """公共 JSON 取数：``send`` 传输 → 非 2xx 分诊 → JSON 解析 → dict 校验。

    Parameters
    ----------
    src
        失败桶宿主（``BaseWebSource`` 子类）：用其 ``source_name`` 与
        ``_record_failure`` / ``_reset_failures``；鸭子类型，不引入基类依赖。
    send
        传输 hook：``url -> HttpResponse``。断连/超时/非 2xx 的重试与异常
        包装由 hook 自决（``_request_http`` 或直连包装），本函数不重复处理。
    label
        报错文案前缀（渠道标签）。
    require_dict
        解析结果必须是 ``dict``，否则抛 ``f"{label}响应非 JSON 对象"``。
    encoding
        响应体解码编码（错误样本与 ``json.loads`` 共用同一份解码文本）。
    error_context
        额外错误 context（如新浪 ESG 的 ``{"host": target}``）。
    status_error
        HTTP 非 2xx hook：``resp -> Exception``（如新浪 ESG 在此记传输桶
        并给出带 host 的文案）。缺省抛 ``f"{label}请求失败 HTTP {status}"``。
    record_parse_failure
        非 JSON 时是否记**解析**失败桶（新浪 ESG 口径）。
    reset_on_success
        成功后是否清零失败桶（新浪 ESG 口径）。
    non_json_msg
        非 JSON 文案；缺省 ``f"{label}响应非 JSON"``。
    non_json_sample_len
        非 JSON 错误样本截断长度。
    """
    resp = send(url)
    if not resp.ok:
        exc = (
            status_error(resp)
            if status_error is not None
            else WebSourceError(
                f"{label}请求失败 HTTP {resp.status}",
                context={"url": url, "source": src.source_name},
            )
        )
        raise exc
    # P1-8/F9：body 兼容 bytes / str（离线测试罐头常用 str 罐体）；
    # bytes 按 encoding 解码（errors=replace），与旧实现 ``resp.text(encoding)``
    # 口径一致；esg 旧实现走 ``resp.json()``，对 UTF-8 罐体结果相同。
    body = resp.body
    text = (
        body.decode(encoding, errors="replace")
        if isinstance(body, (bytes, bytearray))
        else str(body)
    )
    context: dict[str, Any] = {"source": src.source_name, "sample": text[:non_json_sample_len]}
    if error_context:
        context.update(error_context)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        if record_parse_failure:
            src._record_failure(parse=True)  # noqa: SLF001 —— 同包内失败桶契约
        raise SourceDeprecated(
            non_json_msg or f"{label}响应非 JSON",
            context=context,
            cause=exc,
        ) from exc
    if require_dict and not isinstance(payload, dict):
        raise SourceDeprecated(f"{label}响应非 JSON 对象", context=context)
    if reset_on_success:
        src._reset_failures()
    return payload
