# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""东财 JSON 公共基类 _EastmoneyJson：主机池 failover + 黑名单（P10-3 自 base.py 拆出）。

P1 #11/#12：主机池 failover 单一实现 + 失败计数接入；
R3：进程级主机池黑名单（TTL 内优先绕过，跨实例共享）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import ReadTimeout, SourceDeprecated, WebSourceError
from ._base_core import BaseWebSource

__all__ = ["_EastmoneyJson", "reset_em_blacklist"]


# --------------------------------------------------------------------------- #
# 东财主机池进程级黑名单（R3）
# --------------------------------------------------------------------------- #
#: host → TTL 过期时间戳。失败主机 TTL 内绕过，成功/过期即清除。
_EM_HOST_BLACKLIST: dict[str, float] = {}
_EM_HOST_BLACKLIST_LOCK = threading.Lock()
#: 黑名单存活时长（秒）：10 分钟
_EM_HOST_TTL = 600.0


def _em_blacklist_add(host: str) -> None:
    with _EM_HOST_BLACKLIST_LOCK:
        _EM_HOST_BLACKLIST[host] = time.time() + _EM_HOST_TTL


def _em_blacklist_clear(host: str) -> None:
    with _EM_HOST_BLACKLIST_LOCK:
        _EM_HOST_BLACKLIST.pop(host, None)


def _em_blacklist_active() -> set[str]:
    """返回当前仍在 TTL 内的黑名单主机（顺带清理过期项）。"""
    now = time.time()
    with _EM_HOST_BLACKLIST_LOCK:
        expired = [h for h, t in _EM_HOST_BLACKLIST.items() if t < now]
        for h in expired:
            del _EM_HOST_BLACKLIST[h]
        return {h for h, t in _EM_HOST_BLACKLIST.items() if t >= now}


def reset_em_blacklist() -> None:
    """清空东财主机黑名单（测试 / 进程内重置）。"""
    with _EM_HOST_BLACKLIST_LOCK:
        _EM_HOST_BLACKLIST.clear()


# --------------------------------------------------------------------------- #
# 东财 JSON 公共基类（P1 #11/#12：主机池 failover 单一实现 + 失败计数接入）
# --------------------------------------------------------------------------- #
class _EastmoneyJson(BaseWebSource):
    """东财 JSON 接口公共基类：主机池容灾 + 统一 JSON 取数。

    此前 5 份 ``_get_json`` 近似拷贝（fundflow/boards/corporate/ticks）各自
    漂移且绕开 :meth:`fetch` 的失败计数——「下线检测」对自定义取数路径永不
    触发。现上收为本类一处实现：failover 过程计入传输/解析失败桶，
    达阈值同样触发 :class:`~tstdx.errors.SourceDeprecated`。

    子类按需覆盖 ``HOSTS``（主机池）与 :attr:`JSON_LABEL`（错误消息渠道标签）。
    """

    #: 主站 → 备站（92 分流节点）→ 延时镜像（延时渠道，稳定性最高）。
    #: tuple[str, ...]：子类可覆盖为任意长度主机池（如 datacenter 系 2 元组）。
    HOSTS: tuple[str, ...] = (
        "https://push2.eastmoney.com",
        "https://92.push2.eastmoney.com",
        "https://push2delay.eastmoney.com",
    )
    #: URL 前缀（子类覆写；push2 系可直接用 HOSTS[0]，缺省空串防 AttributeError）
    BASE: str = ""
    #: 错误消息中的渠道标签
    JSON_LABEL = "东财"

    encoding = "utf-8"  # 东财 JSON 全系 UTF-8（W10）

    # R3：进程级主机池黑名单（TTL 内优先绕过，跨实例共享）
    def _ordered_hosts(self, host: str | None = None) -> Sequence[str]:
        """按「黑名单过滤 → 原池」返回待尝试主机顺序（R3）。

        失败过的主机 TTL 内（默认 10 分钟）排到队尾，不再每次从 push2 首站
        起试；全部被拉黑时回退完整池（避免死路）。
        """
        if host:
            return (host,)
        blacklisted = _em_blacklist_active()
        if not blacklisted:
            return self.HOSTS
        filtered = tuple(h for h in self.HOSTS if h not in blacklisted)
        return filtered or self.HOSTS

    def _get_json(self, path_query: str, *, host: str | None = None) -> dict[str, Any]:
        """按 host 池顺序请求并解析 JSON（failover，计入失败桶）。"""
        self._check_deprecated()
        hosts: Sequence[str] = self._ordered_hosts(host)
        last_exc: Exception | None = None
        for base in hosts:
            url = f"{base}{path_query}"
            try:
                resp = self.client.get(url, headers=self.headers, timeout=self.timeout)
            except (WebSourceError, ReadTimeout) as exc:
                # 断连 / 超时：换下一主机
                self._record_failure()
                _em_blacklist_add(base)  # R3 失败主机进黑名单
                last_exc = WebSourceError(
                    f"{self.JSON_LABEL}传输失败: {exc}",
                    context={"source": self.source_name, "host": base},
                    cause=exc,
                )
                continue
            if not resp.ok:
                self._record_failure()
                _em_blacklist_add(base)  # R3
                last_exc = WebSourceError(
                    f"{self.JSON_LABEL}请求失败 HTTP {resp.status}",
                    context={"source": self.source_name, "host": base},
                )
                continue
            try:
                payload = resp.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # 解析失败单独入桶（P1 #11），不与传输失败混淆
                self._record_failure(parse=True)
                _em_blacklist_add(base)  # R3：数据异常主机同样降权
                last_exc = SourceDeprecated(
                    f"{self.JSON_LABEL}响应非 JSON",
                    context={
                        "source": self.source_name,
                        "host": base,
                        "sample": resp.text(self.encoding)[:120],
                    },
                    cause=exc,
                )
                continue
            self._reset_failures()
            _em_blacklist_clear(base)  # R3：成功主机移出黑名单
            return payload
        raise (
            last_exc
            if last_exc is not None
            else WebSourceError(
                f"{self.JSON_LABEL}全部主机失败", context={"source": self.source_name}
            )
        )

    @staticmethod
    def _diff(payload: Mapping[str, Any]) -> list[Any]:
        """取 ``data.diff`` 行列表；diff 为 dict（个别端点）时转值列表。"""
        data = payload.get("data") or {}
        rows = data.get("diff") if isinstance(data, Mapping) else None
        if isinstance(rows, Mapping):  # 个别端点 diff 为 dict
            rows = list(rows.values())
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, Mapping)]

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []
