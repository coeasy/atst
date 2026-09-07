# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""股吧个股人气榜 adapter（接口面吸收，tstdx 自有实现）。

接口事实（公开端点，2026-09 实测验证）::

    POST https://emappdata.eastmoney.com/stockrank/getAllCurrentList
    Content-Type: application/json
    {"appId": "appId01", "globalId": "<客户端生成的 uuid>",
     "marketType": "", "pageNo": 1, "pageSize": 100}
    响应: {"status": 0, "data": [{"sc": "SH600127", "rk": 1,
            "rc": 0, "hisRc": 0}, ...]}

设计要点
--------
* **榜单语义**：``rk`` 当前人气名次、``rc`` 较上期名次变动、
  ``hisRc`` 历史名次变动；榜单仅含排名与代码，**不含行情字段**——
  如需行情请以返回 ``symbol`` 回查 :meth:`quotes`。
* **globalId 由本库生成**（uuid4，页面行为即客户端随机 GUID）；
  ``appId`` 沿用页面公开参数。
* **罐头可测**：解析逻辑 :meth:`parse_rank` 是纯函数。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from ..errors import SourceDeprecated, WebSourceError
from .base import BaseWebSource
from .base import num_i as _i
from .sources import HOT_RANK

__all__ = ["EastmoneyHotRankSource"]

_HR_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"


def _i_scalar(value: Any) -> int:
    """标量 / 数组双形态取 int：数组取首元素（如 hisRc 偶发返回 ``[n]``）。"""
    if isinstance(value, (list, tuple)):
        return _i(value[0]) if value else 0
    return _i(value)


class EastmoneyHotRankSource(BaseWebSource):
    """股吧个股人气榜（emappdata ``stockrank``，POST JSON）。

    全市场 A 股人气排名（100 条/页，翻页取更多）。输出按名次升序。
    """

    @property
    def source_name(self) -> str:
        return HOT_RANK

    def build_url(self, symbols: Sequence[str] = (), **kwargs: Any) -> str:
        return _HR_URL

    def fetch_hot_rank(self, *, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """人气榜（单页，按名次升序）。

        Parameters
        ----------
        page:
            页码（1-based）。
        size:
            每页条数（1-100，服务端上限 100）。

        Returns
        -------
        ``[{"rank": 1, "symbol": "sh600127", "code": "600127",
        "market": "sh", "rank_change": 0, "his_rank_change": 0}, ...]``
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        self.rate_limiter.acquire(self.source_name)
        body = json.dumps(
            {
                "appId": "appId01",
                "globalId": str(uuid.uuid4()),
                "marketType": "",
                "pageNo": max(1, int(page)),
                "pageSize": max(1, min(int(size), 100)),
            }
        ).encode("utf-8")
        return self.parse_rank(
            self._request_post(
                _HR_URL,
                body,
                content_type="application/json",
                encoding="utf-8",
                err_msg="人气榜请求失败",
            )
        )

    def parse_rank(self, text: str) -> list[dict[str, Any]]:
        """解析榜单 JSON（纯函数，罐头可测）。"""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "人气榜返回非 JSON",
                context={"source": HOT_RANK, "sample": text[:160]},
                cause=exc,
            ) from exc
        if payload.get("status") != 0:
            raise WebSourceError(
                f"人气榜接口异常: {payload.get('message', '')}",
                context={"source": HOT_RANK, "status": payload.get("status")},
            )
        rows = payload.get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            sc = str(r.get("sc", ""))
            if len(sc) <= 2:
                continue
            out.append(
                {
                    "rank": _i(r.get("rk", 0)),
                    "symbol": sc.lower(),
                    "code": sc[2:],
                    "market": sc[:2].lower(),
                    "rank_change": _i(r.get("rc", 0)),
                    "his_rank_change": _i_scalar(r.get("hisRc", 0)),
                }
            )
        out.sort(key=lambda x: x["rank"])
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        """BaseWebSource 模板方法兼容（榜单非 Quote 语义）。"""
        return self.parse_rank(text)
