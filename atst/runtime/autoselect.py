# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``provider='auto'``：显式自动选源（能力面 + 运行时健康反馈）。

这不是静默换源。provider-first 不变量（一次请求绑定一个 Provider、跨源只在**显式**
策略下发生）在这里的兑现方式是：自动选源只在调用方**显式写下** ``provider="auto"``
（或把 ``core.default_provider`` 配成 ``auto``）时生效，每个结果仍然带
:class:`~atst.result.Provenance`——``requested_provider='auto'`` + ``fallback``
标记说明实际接了谁、换了第几跳。钉死一个 Provider 的请求永远不经过这里。

候选排序两级：

1. **静态能力面**：只收注册表里 operational 支持该 capability 的 Provider
   （``PROVIDERS.available_providers``），顺序按 ``routing.auto_providers`` 的显式
   偏好，未列出的按注册表登记顺序接在后面；
2. **运行时健康反馈**：同一个 TTL 窗口内失败过的 Provider 沉到队尾（相对次序不变），
   TTL 过后自动复位。

健康反馈只记录**真实请求的结果**——成功清零、失败记时——不主动探测：零额外请求
成本，也不会为"测一下谁快"先打一轮流量。进程内有效，不落盘。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import ValidationError
from ..providers import PROVIDERS, normalize_provider_id

__all__ = [
    "AUTO_PROVIDER",
    "ProviderHealthStore",
    "provider_health",
    "auto_candidates",
    "resolve_auto_provider",
    "is_auto_provider",
]

#: 自动选源的选择器值。别名解析（``qq``/``em`` 那套）不适用于它：
#: 它是一个**策略名**，不是一个 Provider。
AUTO_PROVIDER = "auto"


def is_auto_provider(provider: str | None) -> bool:
    """调用方是否显式要求自动选源。"""
    return provider is not None and str(provider).strip().lower() == AUTO_PROVIDER


@dataclass(frozen=True, slots=True)
class _FailureMark:
    at_ns: int
    code: str


class ProviderHealthStore:
    """进程级 Provider 运行时健康反馈（按 TTL 失效的失败记录）。

    线程安全：``provider='auto'`` 的候选排序与并发请求的成败记录会同时发生。
    只存失败——成功即清除，没有"健康"这一态需要存。这里不存任何行情/查询
    结果，只有"谁刚刚失败过"这一个瞬时判断。
    """

    def __init__(self) -> None:
        self._failures: dict[str, _FailureMark] = {}
        self._lock = threading.Lock()

    def record_failure(self, provider: str, *, code: str = "") -> None:
        key = normalize_provider_id(provider)
        with self._lock:
            self._failures[key] = _FailureMark(at_ns=time.monotonic_ns(), code=str(code or ""))

    def record_success(self, provider: str) -> None:
        key = normalize_provider_id(provider)
        with self._lock:
            self._failures.pop(key, None)

    def is_degraded(self, provider: str, *, ttl_ns: int) -> bool:
        """TTL 窗口内失败过＝降级。``ttl_ns <= 0`` 视为关闭健康反馈。"""
        if ttl_ns <= 0:
            return False
        key = normalize_provider_id(provider)
        with self._lock:
            mark = self._failures.get(key)
        if mark is None:
            return False
        if time.monotonic_ns() - mark.at_ns > ttl_ns:
            with self._lock:
                #: 过期即复位：一次失败不该永久放逐一个 Provider。按**对象身份**比对，
                #: 避免把这段时间里新写入的那条失败标记一起清掉。
                if self._failures.get(key) is mark:
                    self._failures.pop(key, None)
            return False
        return True

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """诊断快照（观测面用）：``{provider: {failed_at_ns, code}}``。"""
        with self._lock:
            return {
                provider: {"failed_at_ns": mark.at_ns, "code": mark.code}
                for provider, mark in sorted(self._failures.items())
            }

    def degraded(self, *, ttl_ns: int) -> dict[str, dict[str, Any]]:
        """当前**仍处 TTL 窗口内**的降级名单：``{provider: {code, age_s}}``。

        与 :meth:`is_degraded` 同一判据、同一复位时机——过期的标记在这里顺手清掉，
        所以运维在健康面上看到的名单，与 ``auto`` 下次排序时看到的名单是同一份。
        ``snapshot()`` 给的是"原始标记"（含已过期未复位、且带 ``monotonic_ns`` 这种
        只在本进程内有意义的时刻），不适合直接贴到线上接口。

        ``ttl_ns <= 0``（健康反馈关闭）时返回空：此时没有任何 Provider 会被降级，
        报一份名单等于凭空造出一个不存在的机制。
        """
        if ttl_ns <= 0:
            return {}
        now = time.monotonic_ns()
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for provider, mark in list(self._failures.items()):
                age_ns = now - mark.at_ns
                if age_ns > ttl_ns:
                    #: 按对象身份比对：这段时间里若已有新的失败标记，不能连它一起清掉。
                    if self._failures.get(provider) is mark:
                        self._failures.pop(provider, None)
                    continue
                out[provider] = {
                    "code": mark.code,
                    "age_s": round(age_ns / 1_000_000_000, 3),
                }
        return dict(sorted(out.items()))

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


_PROVIDER_HEALTH = ProviderHealthStore()


def provider_health() -> ProviderHealthStore:
    """进程级健康存储的唯一入口。"""
    return _PROVIDER_HEALTH


def _normalized_prefer(prefer: Sequence[str] | None) -> tuple[str, ...]:
    if not prefer:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in prefer:
        key = normalize_provider_id(item)
        if key not in seen:
            seen.add(key)
            out.append(key)
    unknown = sorted(set(out) - set(PROVIDERS.ids()))
    if unknown:
        #: 静默丢弃拼错的偏好名 = 选源顺序悄悄退回登记序，调用方无从得知。
        raise ValidationError(
            f"auto_providers 含未知 provider: {unknown}",
            context={"unknown_providers": unknown, "known_providers": sorted(PROVIDERS.ids())},
        )
    return tuple(out)


def auto_candidates(
    capability: str,
    *,
    prefer: Sequence[str] | None = None,
    exclude: frozenset[str] | set[str] = frozenset(),
    ttl_ns: int = 0,
) -> tuple[str, ...]:
    """排出 ``provider='auto'`` 的候选顺序（ operational + 偏好 + 健康反馈）。

    Parameters
    ----------
    capability:
        目标能力；只收注册表里 operational 支持它的 Provider。
    prefer:
        显式偏好顺序（ ``routing.auto_providers`` ）；未列出但 operational 的
        Provider 按注册表登记顺序接在后面，不会因为没被点名就消失。
    exclude:
        本轮排除的 Provider（如未配置 ``vipdoc_root`` 时的 ``local_vipdoc``）——
        需要本地数据根的 Provider 排进来也只会得到一次配置错误。
    ttl_ns:
        健康反馈有效期；``0`` = 关闭反馈，纯静态排序。
    """
    cap = str(capability).strip().lower()
    available = [p for p in PROVIDERS.available_providers(cap) if p not in exclude]
    if not available:
        return ()
    ordered: list[str] = [p for p in _normalized_prefer(prefer) if p in available]
    ordered += [p for p in available if p not in ordered]
    if ttl_ns > 0:
        healthy = [p for p in ordered if not provider_health().is_degraded(p, ttl_ns=ttl_ns)]
        degraded = [p for p in ordered if p not in healthy]
        return tuple(healthy + degraded)
    return tuple(ordered)


def resolve_auto_provider(
    capability: str,
    *,
    prefer: Sequence[str] | None = None,
    default: str | None = None,
    exclude: frozenset[str] | set[str] = frozenset(),
    ttl_ns: int = 0,
) -> str:
    """把 ``auto`` 解析成一个具体 Provider（静态选源，无跨源重试）。

    没有 operational 候选时**不抛**：退回声明口径的缺省 Provider，让该 Provider
    自己的 fail-fast 异常（``CommandOffline`` / ``NotImplementedFeature`` /
    "provider 不支持 capability"）保持可观测——自动选源不该把"这条路没有源"
    改写成另一种说法。
    """
    candidates = auto_candidates(capability, prefer=prefer, exclude=exclude, ttl_ns=ttl_ns)
    if candidates:
        return candidates[0]
    fallback = normalize_provider_id(default or PROVIDERS.default_provider)
    return fallback
