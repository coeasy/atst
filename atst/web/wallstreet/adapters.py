# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""华尔街见闻（wallstreetcn）快讯适配器。

端点（2026-10-02 真机验证）
--------------------------
``https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&client=pc&limit=N``

只登记 ``breaking_news`` 一个能力：实测 ``channel=global-channel`` 稳定返回，
而 ``macro-channel`` 返回空 ``items``（该频道未提供快讯流）。与其挂三个
名字、两个永远为空，不如只留一个能发数的。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..base import BaseWebSource
from ..sources import WALLSTREET

__all__ = ["WallstreetSource"]

_BASE = "https://api-one.wallstcn.com/apiv1/content/lives"

#: 快讯正文是 HTML 片段，取纯文本时剥标签
_TAG_RE = re.compile(r"<[^>]+>")


class WallstreetSource(BaseWebSource):
    """华尔街见闻全球快讯源。"""

    encoding = "utf-8"

    @property
    def source_name(self) -> str:
        return WALLSTREET

    def build_url(self, symbols: Any, **kwargs: Any) -> str:
        return f"{_BASE}?channel=global-channel&client=pc&limit=20"

    def parse(self, text: str, symbols: Any, **kwargs: Any) -> list[Any]:
        return self._parse(json.loads(text))

    # -- 能力出口（executor 按 binding.method 分派） ------------------------ #

    def fetch_breaking_news(
        self, *, limit: int = 20, channel: str = "global-channel"
    ) -> list[dict[str, Any]]:
        """全球快讯流（最近 N 条）。

        快讯是**全市场口径**，上游没有按标的分流的频道，因此本方法不接受
        ``symbol``——传了也无从过滤，不如在签名上就拒绝。
        """
        self._check_deprecated()
        self.rate_limiter.acquire(self.source_name)
        url = f"{_BASE}?channel={channel}&client=pc&limit={int(limit)}"
        text = self._request_text(url, encoding="utf-8", err_msg="华尔街见闻快讯请求失败")
        return self._parse(json.loads(text))[:limit]

    @staticmethod
    def _parse(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = ((payload.get("data") or {}).get("items")) or []
        out: list[dict[str, Any]] = []
        for item in rows:
            text = item.get("content_text") or _strip_tags(item.get("content") or "")
            out.append(
                {
                    "id": str(item.get("id") or item.get("uri") or ""),
                    "title": (item.get("title") or "").strip(),
                    "text": text.strip(),
                    "author": ((item.get("author") or {}).get("display_name") or ""),
                    "publish_time": _ts_to_iso(item.get("display_time")),
                    "channels": list(item.get("channels") or []),
                    "uri": item.get("uri") or "",
                    "source": "wallstreet",
                }
            )
        return out


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html)


def _ts_to_iso(ts: Any) -> str:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
