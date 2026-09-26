# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP 行情源基类 BaseWebSource：生命周期 + 模板方法（P10-3 自 base.py 拆出）。

子类实现 :meth:`BaseWebSource.build_url` 与 :meth:`BaseWebSource.parse`；
基类负责限流、反爬头、重试退避（重试内核见 :mod:`tstdx.web._base_retry`）、
错误分诊、归一化。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ..domain.models import Quote
from ..errors import (
    AntiSpiderBlocked,
    ReadTimeout,
    SourceDeprecated,
    WebRateLimited,
    WebSourceError,
)
from ._base_http import HttpClient, HttpResponse, RateLimiter, build_client, shared_bucket
from ._base_retry import (
    MAX_RETRIES_CAP,
    _backoff_delay,
    _BaseRetryMixin,
    _is_retryable_status,
)
from .normalize import (
    normalize_amount as _normalize_amount,
)
from .normalize import (
    normalize_price as _normalize_price,
)
from .normalize import (
    normalize_volume as _normalize_volume,
)
from .sources import get_source

__all__ = ["BaseWebSource"]


# --------------------------------------------------------------------------- #
# 源基类
# --------------------------------------------------------------------------- #
class BaseWebSource(_BaseRetryMixin):
    """HTTP 行情源基类（模板方法）。

    子类实现 :meth:`build_url` 与 :meth:`parse`；
    基类负责限流、反爬头、重试退避、错误分诊、归一化。

    失败计数（W3/W4）
    -----------------
    * ``_failures``——**传输失败桶**：HTTP 非 2xx、断连、超时等；
      达 :attr:`DEPRECATE_AFTER_FAILURES` 判定源下线。
    * ``_parse_failures``——**解析失败桶**：响应可取但解析失败（非 JSON、
      结构变更）；达 :attr:`DEPRECATE_AFTER_PARSE_FAILURES`（默认为传输
      阈值的 2 倍）判定源下线。两桶独立计数，避免「解析 bug」与「源下线」
      互相污染（§2 P1 #11）；任一次成功请求双桶清零。

    双桶计数与阈值常量实现见 :class:`~tstdx.web._base_retry._BaseRetryMixin`。
    """

    #: :meth:`fetch` 响应体解码编码（W10）：新浪/腾讯系 GBK，东财 JSON 系 UTF-8，
    #: 子类按上游真实编码覆盖
    encoding: str = "gbk"

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        max_retries: int = 2,
        rate_limit: float | None = None,
        cookie: str | None = None,
    ) -> None:
        spec = get_source(self.source_name)
        self.spec = spec
        self.timeout = timeout
        # W4: 钳制到 [0, MAX_RETRIES_CAP]，负值不再产生空 range + 裸断言
        self.max_retries = max(0, min(int(max_retries), MAX_RETRIES_CAP))
        self.cookie = cookie
        self._failures = 0
        self._parse_failures = 0

        merged_headers = dict(headers or {})
        if spec.needs_referer and "Referer" not in merged_headers:
            merged_headers["Referer"] = "https://finance.sina.com.cn"
        if cookie:
            merged_headers["Cookie"] = cookie
        elif spec.needs_cookie:
            raise WebSourceError(f"{spec.name} 需要提供 cookie", context={"source": spec.name})
        self.headers = merged_headers
        # B6：注入的 client 归调用方所有（close 不释放），仅内部自建时拥有
        self.client = client or build_client(default_headers=merged_headers)
        self._owns_client = client is None
        # W5: 限流桶按 source_name 挂到进程级注册表——门面每次调用新建源
        # 实例时令牌不再「满血复活」，同一上游跨实例共享防封配额。
        rate = rate_limit if rate_limit is not None else spec.default_rate
        self.rate_limiter = RateLimiter()
        # B9：通过公共方法注入共享桶，避免跨文件写 _buckets 私有属性
        self.rate_limiter.set_bucket(spec.name, shared_bucket(spec.name, float(rate)))

    # -- 子类实现 ---------------------------------------------------------- #
    @property
    def source_name(self) -> str:
        raise NotImplementedError

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        """解析响应体（多态出口：不同源返回 Bar / Quote / Tick / MinutePoint）。"""
        raise NotImplementedError

    # -- 编排 -------------------------------------------------------------- #
    def fetch(self, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        self._check_deprecated()

        url = self.build_url(symbols, **kwargs)
        last_exc: BaseException | None = None

        for attempt in range(self.max_retries + 1):
            self.rate_limiter.acquire(self.source_name)
            try:
                resp = self.client.get(url, headers=self.headers, timeout=self.timeout)
            except (WebSourceError, ReadTimeout) as exc:
                # W3: 断连/超时是最典型的瞬态故障，纳入重试集合
                self._record_failure()
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(_backoff_delay(attempt))
                continue

            if resp.status == 403:
                self._record_failure()
                last_exc = AntiSpiderBlocked(
                    f"{self.source_name} 返回 403（疑似反爬）",
                    context={"url": url, "source": self.source_name},
                )
            elif resp.status == 429:
                self._record_failure()
                last_exc = WebRateLimited(
                    f"{self.source_name} 返回 429（限流）",
                    context={"url": url, "source": self.source_name},
                )
            elif not resp.ok:
                self._record_failure()
                last_exc = WebSourceError(
                    f"{self.source_name} HTTP {resp.status}",
                    context={"url": url, "source": self.source_name},
                )
                if not _is_retryable_status(resp.status):
                    # W4: 404/400 等确定性失败直接抛出，不再空转重试
                    raise last_exc
            else:
                try:
                    quotes = self.parse(resp.text(self.encoding), symbols, **kwargs)
                    self._reset_failures()
                    return quotes
                except SourceDeprecated:
                    raise
                except Exception as exc:
                    self._record_failure(parse=True)
                    last_exc = WebSourceError(
                        f"{self.source_name} 解析失败: {exc}",
                        context={"url": url, "source": self.source_name},
                        cause=exc,
                    )

            if attempt < self.max_retries and last_exc is not None:
                # 指数退避 + 抖动（封顶 8s，W4）
                time.sleep(_backoff_delay(attempt))

        assert last_exc is not None
        raise last_exc

    # -- 自定义 fetch_* 复用：带容错的原始 GET/POST ----------------------- #
    def _request_http(
        self,
        url: str,
        *,
        op: Literal["get", "post"] = "get",
        body: bytes | None = None,
        content_type: str | None = None,
        err_cls: type[WebSourceError] = WebSourceError,
        err_msg: str | None = None,
        retries: int | None = None,
    ) -> HttpResponse:
        """传输层重试内核：限流 + 退避重试 + 失败桶累计 + 下线检测前置。

        v6 A3：自定义 ``fetch_*`` 曾散落 ``self.client.get/post`` 直调，
        绕过本内核 —— 无重试、无退避、不累计 ``_failures``，导致接口下线
        检测 ``SourceDeprecated`` 在这些路径永不触发。现统一收口到此处，
        GET / POST / JSON 三个薄包装共用同一份重试逻辑（消除重复）。

        行为：失败计入传输桶（达阈值触发下线检测）；传输异常（断连/超时）
        与 403/429/5xx 按指数退避 + 抖动重试；403/429 给出更精确异常类型；
        404/400 等确定性失败不重试。

        Parameters
        ----------
        op
            ``"get"`` 或 ``"post"``（POST 需配 ``body``，可选 ``content_type``）。
        err_cls / err_msg
            非 2xx 且非 403/429 时的异常类型与文案；``err_msg`` 提供时
            抛 ``f"{err_msg} HTTP {status}"``。
        retries
            覆盖 ``self.max_retries``。多 host 故障切换循环应传 ``0``：
            外层已按 host 维度重试，内层再重试会放大请求量
            （N host × (R+1) 次）。
        """
        self._check_deprecated()
        n_retries = self.max_retries if retries is None else max(0, retries)
        last_exc: BaseException | None = None
        for attempt in range(n_retries + 1):
            self.rate_limiter.acquire(self.source_name)
            try:
                if op == "post":
                    resp = self.client.post(
                        url,
                        headers=self.headers,
                        timeout=self.timeout,
                        body=body,
                        content_type=content_type,
                    )
                else:
                    resp = self.client.get(url, headers=self.headers, timeout=self.timeout)
            except (WebSourceError, ReadTimeout) as exc:
                self._record_failure()
                last_exc = exc
                if attempt < n_retries:
                    time.sleep(_backoff_delay(attempt))
                continue
            if resp.ok:
                self._reset_failures()
                return resp
            self._record_failure()
            if resp.status == 403:
                last_exc = AntiSpiderBlocked(
                    f"{self.source_name} 返回 403（疑似反爬）",
                    context={"url": url, "source": self.source_name},
                )
            elif resp.status == 429:
                last_exc = WebRateLimited(
                    f"{self.source_name} 返回 429（限流）",
                    context={"url": url, "source": self.source_name},
                )
            else:
                last_exc = err_cls(
                    (err_msg + f" HTTP {resp.status}")
                    if err_msg
                    else f"{self.source_name} HTTP {resp.status}",
                    context={"url": url, "source": self.source_name},
                )
            if not _is_retryable_status(resp.status):
                # W4: 404/400 等确定性失败直接抛出，不再空转重试
                raise last_exc
            if attempt < n_retries:
                time.sleep(_backoff_delay(attempt))
        # 循环至少执行一轮且末轮必然已赋值或提前 return/raise
        assert last_exc is not None
        raise last_exc

    def _request_text(
        self,
        url: str,
        *,
        encoding: str = "utf-8",
        err_cls: type[WebSourceError] = WebSourceError,
        err_msg: str | None = None,
        retries: int | None = None,
    ) -> str:
        """带重试 / 限流 / 下线检测的原始 GET，返回解码文本。

        自定义 ``fetch_*``（K 线 / 分时 / 新闻 / 逐笔 / 全市场等）统一复用本方法，
        避免绕过基类 :meth:`fetch` 的容错（旧实现单次请求、无退避、不累计
        ``_failures``，导致接口下线检测 ``SourceDeprecated`` 永不触发）。

        行为：失败计入传输桶（达阈值触发下线检测）；传输异常（断连/超时）
        与 403/429/5xx 按指数退避 + 抖动重试 ``max_retries`` 次（封顶 8s）；
        403/429 给出更精确异常类型；404/400 等确定性失败不重试。

        Parameters
        ----------
        err_msg
            自定义失败文案（如 ``"新浪板块列表请求失败"``）。提供时若
            HTTP 非 2xx，以 ``f"{err_msg} HTTP {status}"`` 抛出 ``err_cls``。
        retries
            覆盖 ``self.max_retries``（多 host 故障切换循环应传 ``0``）。
        """
        resp = self._request_http(url, err_cls=err_cls, err_msg=err_msg, retries=retries)
        return resp.text(encoding)

    def _request_post(
        self,
        url: str,
        body: bytes,
        *,
        content_type: str = "application/json",
        encoding: str = "utf-8",
        err_msg: str | None = None,
        retries: int | None = None,
    ) -> str:
        """带重试 / 限流 / 下线检测的 POST，返回解码文本。

        语义与 :meth:`_request_text` 完全一致，仅传输方法为 POST。
        """
        resp = self._request_http(
            url,
            op="post",
            body=body,
            content_type=content_type,
            err_msg=err_msg,
            retries=retries,
        )
        return resp.text(encoding)

    # -- 归一化 ------------------------------------------------------------ #
    def normalize_quote(self, q: Quote) -> Quote:
        """按 SourceSpec 的系数把原始值换算到全局契约（股 / 元）。

        Delegates to :mod:`tstdx.web.normalize` which uses a centralized
        registry of :class:`~tstdx.web.normalize.VolumeNormalizer` instances.
        If the source has no registered normalizer, falls back to
        :class:`VolumeNormalizer` (identity scales).
        """
        src = self.source_name
        q.price = _normalize_price(src, q.price)
        q.last_close = _normalize_price(src, q.last_close)
        q.open = _normalize_price(src, q.open)
        q.high = _normalize_price(src, q.high)
        q.low = _normalize_price(src, q.low)
        q.volume = int(round(_normalize_volume(src, q.volume)))
        q.amount = _normalize_amount(src, q.amount)
        for side in (q.bid, q.ask):
            for lv in side:
                lv.price = _normalize_price(src, lv.price)
                lv.volume = int(round(_normalize_volume(src, lv.volume)))
        for k in ("limit_up", "limit_down", "avg_price"):
            if k in q.extra:
                q.extra[k] = _normalize_price(src, q.extra[k])
        return q

    def close(self) -> None:
        # B6：注入 client（如会话层进程级共享池）时归调用方所有，跳过释放
        if self._owns_client:
            self.client.close()
