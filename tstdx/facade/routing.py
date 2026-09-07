# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""路由选择与熔断（W11/W12）—— :class:`UnifiedQuoteAPI` 的「壳」层。

P10-2 自 ``facade/api.py`` 拆出（REFACTOR_PLAN_v10）：api.py 保留业务方法
（取数单源委托 :class:`~tstdx.sources.DataSourceRouter`），本模块承载
**路由选择、错误聚合、熔断冷却**三件基础设施。方法体自 api.py 逐字搬移，
行为契约不变（``tests/facade/test_w11_w12_w13.py`` 守护）。

W11 熔断语义
------------
* 某路由连续失败 ≥3 次进入 30s 冷却（实例级），auto 序冷却期内跳过该路由
  （显式路由仍尝试）；成功清零；``close()`` 不复位（进程级体验），
  ``reset_circuit()`` 手动复位。
* 每路由失败记一条 ``tstdx.facade`` warning；全路由失败时重抛**最后一路由**
  异常，并把 ``route_errors={route: "ExcType: msg"}`` 聚合进其 ``context``。
* ``ImportError``/``ModuleNotFoundError`` 一律重抛（依赖缺失不是路由故障，
  不计数不熔断）。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Literal

from ..errors import TdxError

__all__ = ["RouteSelector", "Route"]

_LOG = logging.getLogger("tstdx.facade")

Route = Literal["auto", "local", "tdx", "web"]


class RouteSelector:
    """路由选择 + 熔断冷却状态机（组合进 :class:`UnifiedQuoteAPI`）。

    子类须提供 ``default_route`` 属性；``__init__`` 初始化熔断状态
    （``_route_fail_counts`` / ``_route_cooldown_until`` 实例属性名不变，
    测试与运维直接检查）。
    """

    #: 熔断参数：某路由连续失败达阈值 → 进入冷却，auto 序冷却期内跳过。
    _ROUTE_FAIL_LIMIT = 3
    _ROUTE_COOLDOWN_SECONDS = 30.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # W11 路由熔断状态（实例级；close() 不复位，reset_circuit() 手动复位）
        self._route_fail_counts: dict[str, int] = {}
        self._route_cooldown_until: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    default_route: Route = "auto"

    def _iter_routes(self, route: Route | None) -> tuple[str, ...]:
        r = route or self.default_route
        if r != "auto":
            return (r,)
        return ("local", "tdx", "web")

    @staticmethod
    def _route_caller_name() -> str:
        """取 ``_try_routes`` 调用方方法名（仅用于错误文案，尽力而为）。"""
        try:
            return sys._getframe(2).f_code.co_name
        except Exception:  # noqa: BLE001 —— 文案辅助，取名失败绝不报错
            return ""

    def _route_cooling(self, r: str) -> bool:
        """路由是否处于熔断冷却期。"""
        until = self._route_cooldown_until.get(r)
        return until is not None and time.monotonic() < until

    def _note_route_failure(self, r: str, exc: BaseException) -> None:
        """记录一次路由失败：告警一条；连续失败达阈值则熔断进入冷却。

        进入冷却时失败计数清零——冷却期满后需重新连续失败达阈值才会
        再次熔断（半开重试语义，避免单次抖动立即重开）。
        """
        n = self._route_fail_counts.get(r, 0) + 1
        if n >= self._ROUTE_FAIL_LIMIT:
            self._route_cooldown_until[r] = time.monotonic() + self._ROUTE_COOLDOWN_SECONDS
            self._route_fail_counts[r] = 0
            _LOG.warning(
                "facade 路由 %r 连续失败达 %d 次，熔断进入 %.0fs 冷却（最近失败: %s: %s）",
                r,
                self._ROUTE_FAIL_LIMIT,
                self._ROUTE_COOLDOWN_SECONDS,
                type(exc).__name__,
                exc,
            )
        else:
            self._route_fail_counts[r] = n
            _LOG.warning(
                "facade 路由 %r 调用失败（连续第 %d 次）: %s: %s",
                r,
                n,
                type(exc).__name__,
                exc,
            )

    def _note_route_success(self, r: str) -> None:
        """路由成功：连续失败计数清零并解除冷却。"""
        self._route_fail_counts[r] = 0
        self._route_cooldown_until.pop(r, None)

    def reset_circuit(self) -> None:
        """清空路由熔断状态（连续失败计数与冷却窗口），便于测试与运维复位。"""
        self._route_fail_counts.clear()
        self._route_cooldown_until.clear()

    @staticmethod
    def _attach_route_errors(exc: BaseException, route_errors: dict[str, str]) -> None:
        """把 ``route_errors`` 聚合进最终异常的 ``context``（尽量就地挂载）。"""
        if isinstance(exc, TdxError):
            exc.context["route_errors"] = route_errors
            return
        ctx = getattr(exc, "context", None)
        if not isinstance(ctx, dict):
            ctx = {}
            try:
                exc.context = ctx  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 —— 少数异常类型不可挂属性
                return
        ctx["route_errors"] = route_errors

    def _try_routes(self, route: Route | None, fn: dict[str, Any]) -> Any:
        """按路由顺序执行 ``fn[route]()``，返回第一个成功结果。

        成功判定：路由函数**不抛异常**即视为成功（含合法空结果，如停牌日的
        空 K 线 / 空行情列表）。仅当某路由抛异常时才继续尝试下一路由——这
        修正了旧实现用 ``if out:`` 把合法空结果误判为「失败」的 bug。

        W11 增强：

        * 每路由失败记一条 ``tstdx.facade`` warning；连续失败 ≥3 次进入 30s
          冷却，auto 序冷却期内跳过该路由（显式路由仍尝试）；成功清零。
        * 全路由失败时重抛**最后一路由**异常（既有契约保持），并把
          ``route_errors={route: "ExcType: msg"}`` 聚合进其 ``context``。
        * ``ImportError``/``ModuleNotFoundError`` 一律重抛（依赖缺失不是
          路由故障，不计数不熔断）。
        """
        requested: Route = route or self.default_route
        auto = requested == "auto"
        route_errors: dict[str, str] = {}
        cooldown_skipped: list[str] = []
        executed = False
        last_exc: BaseException | None = None
        for r in self._iter_routes(route):
            f = fn.get(r)
            if f is None:
                continue
            if auto and self._route_cooling(r):
                cooldown_skipped.append(r)
                continue
            executed = True
            try:
                out = f()
            except (ImportError, ModuleNotFoundError):
                raise
            except TdxError as exc:
                self._note_route_failure(r, exc)
                route_errors[r] = f"{type(exc).__name__}: {exc}"
                last_exc = exc
            except Exception as exc:  # noqa: BLE001
                self._note_route_failure(r, exc)
                route_errors[r] = f"{type(exc).__name__}: {exc}"
                last_exc = exc
            else:
                self._note_route_success(r)
                return out
        if last_exc is not None:
            self._attach_route_errors(last_exc, route_errors)
            raise last_exc
        if cooldown_skipped and not executed:
            raise TdxError(
                f"路由 {requested!r} 均处于熔断冷却期（连续失败 ≥{self._ROUTE_FAIL_LIMIT} "
                f"次后 {self._ROUTE_COOLDOWN_SECONDS:.0f}s），可调用 reset_circuit() 复位",
                context={"route": requested, "cooldown_routes": cooldown_skipped},
            )
        name = self._route_caller_name()
        where = f" 在方法 {name}" if name else ""
        avail = ", ".join(fn) if fn else "无"
        raise TdxError(
            f"路由 {requested!r}{where} 无对应实现，可用: {avail}",
            context={
                "route": requested,
                "available": list(fn),
                "method": name or None,
                "route_errors": route_errors,
            },
        )

    @staticmethod
    def _require_tdx_route(route: Route | None) -> None:
        """W12：仅 tdx 能力的方法，显式传入其他路由时立即拒绝（收缩承诺）。

        ``None``（未指定）与 ``"auto"`` 合法——auto 序只会执行有实现的通路。
        """
        if route is not None and route not in ("tdx", "auto"):
            raise ValueError("该方法仅支持 tdx 路由")
