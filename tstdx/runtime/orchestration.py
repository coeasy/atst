# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Explicit cross-Provider orchestration above the strict runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..error_envelope import to_error_envelope
from ..errors import AllSourcesExhausted, ValidationError
from ..providers import PROVIDERS, resolve_provider
from ..query import QuerySpec
from ..result import QueryResult
from .kernel import UnifiedRuntime

__all__ = [
    "FallbackPolicy",
    "ProviderAttempt",
    "OrchestratedResult",
    "ProviderOrchestrator",
]


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    """Explicit ordered Provider fallback policy."""

    providers: tuple[str, ...]

    @classmethod
    def build(cls, *providers: str) -> FallbackPolicy:
        normalized = tuple(resolve_provider(provider=item) for item in providers)
        if not normalized:
            raise ValidationError(
                "fallback 名单至少要点一个 Provider",
                context={"phase": "wire_validation", "received": []},
            )
        if len(normalized) != len(set(normalized)):
            raise ValidationError(
                "fallback 名单里有重复的 Provider：同一家排两次不会带来第二次机会，"
                "只会让失败次数与名单长度对不上",
                context={"phase": "wire_validation", "received": list(normalized)},
            )
        for provider in normalized:
            PROVIDERS.get(provider)
        return cls(normalized)

    @classmethod
    def from_wire(cls, raw: Any) -> FallbackPolicy | None:
        """线面上 ``fallback`` 的**唯一**解法：没给 → ``None``，给了 → 名单。

        四面原本各抄一份解析（CLI ``_policy``、HTTP ``_policy``、WS ``_policy``，MCP 干脆没有
        这一格）：字符串按逗号切、空白项丢弃这些规则本身没错，错在同一件业务事实有四个持有者——
        实测 WS 那份多认列表、另两份只认字符串，而"名单里有重复项"这件事到谁都一样地抛
        裸 ``ValueError``（见 :meth:`build`）。多形状不是分歧的全部：抄件之间真正危险的是
        **没人要求它们一致**，所以这一格由 :mod:`tests.architecture.test_face_exposure_projection`
        按"同一个值打进四面，内核收到的名单必须相同"将。
        """
        if raw is None or raw == "" or raw == [] or raw == ():
            return None
        if isinstance(raw, str):
            values = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(raw, (list, tuple)):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            raise ValidationError(
                "fallback 必须是 Provider 名单（逗号分隔字符串或数组）",
                context={"phase": "wire_validation", "fallback_shape": type(raw).__name__},
            )
        if not values:
            raise ValidationError(
                "fallback 给了却什么也没剩下：名单里只有分隔符或空白",
                context={
                    "phase": "wire_validation",
                    "received": raw if isinstance(raw, str) else [],
                },
            )
        return cls.build(*values)


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    status: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class OrchestratedResult:
    result: QueryResult[Any]
    attempts: tuple[ProviderAttempt, ...]


class ProviderOrchestrator:
    """Only cross-Provider execution implementation in the v13 architecture."""

    def __init__(self, runtime: UnifiedRuntime | None = None) -> None:
        self.runtime = runtime or UnifiedRuntime()

    @staticmethod
    def _mark_fallback(
        result: QueryResult[Any],
        *,
        requested_provider: str,
        fallback: bool,
    ) -> QueryResult[Any]:
        provenance = replace(
            result.meta.provenance,
            requested_provider=requested_provider,
            fallback=fallback,
        )
        return QueryResult(data=result.data, meta=replace(result.meta, provenance=provenance))

    def execute(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        """Execute one semantic QuerySpec across an explicit Provider order.

        The base QuerySpec's provider, if any, is replaced by each policy
        Provider in turn. All other semantics remain identical, preserving a
        meaningful per-attempt comparison and fingerprint.
        """

        attempts: list[ProviderAttempt] = []
        requested = policy.providers[0]
        base = replace(spec, provider=None)
        for index, provider in enumerate(policy.providers):
            attempt_spec = replace(base, provider=provider)
            try:
                result = self.runtime.execute(attempt_spec)
            except Exception as exc:
                envelope = to_error_envelope(exc)
                attempts.append(ProviderAttempt(provider, "failed", envelope.code))
                continue
            attempts.append(ProviderAttempt(provider, "ok"))
            return OrchestratedResult(
                result=self._mark_fallback(
                    result,
                    requested_provider=requested,
                    fallback=index > 0,
                ),
                attempts=tuple(attempts),
            )
        raise AllSourcesExhausted(
            "explicit Provider fallback policy exhausted",
            context={
                "providers": list(policy.providers),
                "errors": [f"{item.provider}:{item.code}" for item in attempts],
                "fallback": True,
                "capability": spec.capability,
            },
        )

    def quotes(
        self,
        symbols: str | list[str] | tuple[str, ...],
        *,
        policy: FallbackPolicy,
        currentness: str = "live",
    ) -> OrchestratedResult:
        return self.execute(
            QuerySpec.build(
                "quotes",
                symbols=symbols,
                currentness=currentness,
            ),
            policy=policy,
        )

    def bars(
        self,
        symbol: str,
        *,
        policy: FallbackPolicy,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
    ) -> OrchestratedResult:
        return self.execute(
            QuerySpec.build(
                "bars",
                symbols=symbol,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness=currentness,
            ),
            policy=policy,
        )
