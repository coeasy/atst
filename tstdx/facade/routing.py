# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Legacy route compatibility on top of the v12 Provider model.

``route=`` is retained for compatibility, but it is no longer a fallback chain.
The formal domain boundary is Provider (TDX/Tencent/Sina/Eastmoney/...).

Fail-closed rules
-----------------
* ``route='tdx'`` executes TDX only. TDX may fail over between TDX hosts, but
  this layer never switches to a Web provider.
* ``route='web'`` executes the legacy Web path only; provider-specific APIs
  should use an explicit provider instead of relying on Web internal ordering.
* ``route='local'`` executes the local path only.
* ``route='auto'`` is deterministic: if the capability has a TDX implementation,
  TDX is selected once. If TDX is not implemented and exactly one route exists,
  that implementation is selected. No failure triggers another route.

This is the compatibility bridge while public APIs migrate from ``route=`` /
``source=`` to ``provider=`` + ProviderRegistry.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Literal

from ..errors import SourceUnavailable, TdxError

__all__ = ["RouteSelector", "Route"]

_LOG = logging.getLogger("tstdx.facade")

Route = Literal["auto", "local", "tdx", "web"]


class RouteSelector:
    """Compatibility selector with provider-bound, fail-closed execution."""

    _ROUTE_FAIL_LIMIT = 3
    _ROUTE_COOLDOWN_SECONDS = 30.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._route_fail_counts: dict[str, int] = {}
        self._route_cooldown_until: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    default_route: Route = "auto"

    @staticmethod
    def _route_caller_name() -> str:
        try:
            return sys._getframe(2).f_code.co_name
        except Exception:  # noqa: BLE001 - diagnostics only
            return ""

    @staticmethod
    def _select_auto_route(fn: dict[str, Any]) -> str | None:
        """Select one deterministic implementation; never build a fallback list."""
        if "tdx" in fn:
            return "tdx"
        if len(fn) == 1:
            return next(iter(fn))
        # Compatibility for old Web-only capability groups. This is selection,
        # not fallback: once selected, an error is returned to the caller.
        if "web" in fn:
            return "web"
        if "local" in fn:
            return "local"
        return next(iter(fn), None)

    def _iter_routes(self, route: Route | None, fn: dict[str, Any] | None = None) -> tuple[str, ...]:
        """Return at most one route under v12 fail-closed semantics."""
        r = route or self.default_route
        if r != "auto":
            return (r,)
        selected = self._select_auto_route(fn or {})
        return (selected,) if selected else ()

    def _route_cooling(self, r: str) -> bool:
        until = self._route_cooldown_until.get(r)
        return until is not None and time.monotonic() < until

    def _note_route_failure(self, r: str, exc: BaseException) -> None:
        """Track health of the selected route without switching Provider."""
        n = self._route_fail_counts.get(r, 0) + 1
        if n >= self._ROUTE_FAIL_LIMIT:
            self._route_cooldown_until[r] = time.monotonic() + self._ROUTE_COOLDOWN_SECONDS
            self._route_fail_counts[r] = 0
            _LOG.warning(
                "facade provider path %r 连续失败达 %d 次，进入 %.0fs 冷却；"
                "不会切换到其它 Provider（最近失败: %s: %s）",
                r,
                self._ROUTE_FAIL_LIMIT,
                self._ROUTE_COOLDOWN_SECONDS,
                type(exc).__name__,
                exc,
            )
        else:
            self._route_fail_counts[r] = n
            _LOG.warning(
                "facade provider path %r 调用失败（连续第 %d 次）: %s: %s；不会跨 Provider 兜底",
                r,
                n,
                type(exc).__name__,
                exc,
            )

    def _note_route_success(self, r: str) -> None:
        self._route_fail_counts[r] = 0
        self._route_cooldown_until.pop(r, None)

    def reset_circuit(self) -> None:
        self._route_fail_counts.clear()
        self._route_cooldown_until.clear()

    @staticmethod
    def _attach_route_errors(exc: BaseException, route_errors: dict[str, str]) -> None:
        if isinstance(exc, TdxError):
            exc.context["route_errors"] = route_errors
            return
        ctx = getattr(exc, "context", None)
        if not isinstance(ctx, dict):
            ctx = {}
            try:
                exc.context = ctx  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                return
        ctx["route_errors"] = route_errors

    def _try_routes(self, route: Route | None, fn: dict[str, Any]) -> Any:
        """Execute exactly one selected implementation.

        The old implementation retried ``local -> tdx -> web``. v12 deliberately
        forbids that behavior because data provenance changes across providers.
        TDX host failover remains inside :class:`TdxClient` / ConnectionPool.
        """
        requested: Route = route or self.default_route
        candidates = self._iter_routes(route, fn)
        if not candidates:
            name = self._route_caller_name()
            raise TdxError(
                f"路由 {requested!r} 在方法 {name or '<unknown>'} 无对应实现",
                context={"route": requested, "available": list(fn), "method": name or None},
            )

        selected = candidates[0]
        call = fn.get(selected)
        if call is None:
            name = self._route_caller_name()
            avail = ", ".join(fn) if fn else "无"
            raise TdxError(
                f"路由 {requested!r} 在方法 {name or '<unknown>'} 无对应实现，可用: {avail}",
                context={
                    "route": requested,
                    "selected": selected,
                    "available": list(fn),
                    "method": name or None,
                },
            )

        if requested == "auto" and self._route_cooling(selected):
            raise SourceUnavailable(
                f"选定 Provider path {selected!r} 当前处于冷却期",
                context={
                    "provider_path": selected,
                    "route": requested,
                    "fallback": False,
                    "cooldown_seconds": self._ROUTE_COOLDOWN_SECONDS,
                },
            )

        try:
            out = call()
        except (ImportError, ModuleNotFoundError):
            raise
        except Exception as exc:  # TdxError and provider adapter errors included
            self._note_route_failure(selected, exc)
            route_errors = {selected: f"{type(exc).__name__}: {exc}"}
            self._attach_route_errors(exc, route_errors)
            raise
        else:
            self._note_route_success(selected)
            return out

    @staticmethod
    def _require_tdx_route(route: Route | None) -> None:
        """Reject explicit non-TDX routes for TDX-only capabilities."""
        if route is not None and route not in ("tdx", "auto"):
            raise ValueError("该方法仅支持 tdx 路由")
