# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP 传输底座：客户端实现 + 限流器（P10-3 自 base.py 拆出）。

HTTP 后端
---------
* 默认用标准库 ``urllib.request`` / ``http.client``，**零依赖**即可工作。
* 若装了 ``httpx``（``pip install tstdx[web]``）则自动优先使用（连接复用、HTTP/2）。

限流
----
* :class:`TokenBucket` 令牌桶（线程安全）+ :class:`RateLimiter` 按源分组；
* 进程级共享桶注册表 :data:`_SHARED_BUCKETS`（W5）也在此维护，
  由 :mod:`tstdx.web.base` re-export 保持历史导入路径。
"""

from __future__ import annotations

import contextlib
import gzip
import http.client
import io
import json
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..errors import (
    DependencyMissingError,
    RetryAdvice,
    WebRateLimited,
    WebSourceError,
)
from ._base_retry import DEFAULT_ACQUIRE_TIMEOUT

__all__ = [
    "num_f",
    "num_i",
    "HttpResponse",
    "HttpClient",
    "UrllibClient",
    "StdlibPooledClient",
    "HttpxClient",
    "build_client",
    "TokenBucket",
    "RateLimiter",
    "shared_bucket",
    "reset_shared_buckets",
]


# --------------------------------------------------------------------------- #
# 数值安全转换（B7/C4：web 层辅助函数唯一实现，13 个文件统一导入）
# --------------------------------------------------------------------------- #
def num_f(value: Any, default: float = 0.0) -> float:
    """字符串 / 数字安全转 float；``NaN``/``Infinity`` 非有限值归 default。

    float32 上游溢出与 JSON ``NaN`` 字面量都会伪装成合法浮点（W14），
    K 线价格 / 量额不接受非有限值（C4：全 web 层统一 ``math.isfinite`` 校验）。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def num_i(value: Any, default: int = 0) -> int:
    """字符串 / 数字安全转 int（B7 上提版，语义与各文件原 ``_i`` 一致）。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------- #
# 进程级限流桶注册表（W5）
# --------------------------------------------------------------------------- #
#: 按 source_name 共享的令牌桶：门面/工具函数每次调用新建源实例时，
#: 限流桶不再「满血复活」——同一上游家族（如全部新浪系）跨实例共享配额。
_SHARED_BUCKETS: dict[str, TokenBucket] = {}
_SHARED_BUCKETS_LOCK = threading.Lock()


def shared_bucket(source: str, rate: float) -> TokenBucket:
    """按 source_name 惰性取（或建）进程级令牌桶（线程安全）。

    深审 M12：旧实现「首建者胜」——先建的桶固定 rate，后续实例携带的
    不同 rate 配置被静默忽略（用户调低限速不生效）。现在 rate 不同即
    显式更新（:meth:`TokenBucket.set_rate` 线程安全，既有令牌保留）。
    """
    with _SHARED_BUCKETS_LOCK:
        bucket = _SHARED_BUCKETS.get(source)
        if bucket is None:
            bucket = _SHARED_BUCKETS[source] = TokenBucket(float(rate))
        elif bucket.rate != float(rate):
            bucket.set_rate(rate)
        return bucket


def reset_shared_buckets() -> None:
    """清空进程级限流桶（测试 / 进程内重置配额用）。"""
    with _SHARED_BUCKETS_LOCK:
        _SHARED_BUCKETS.clear()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        """零拷贝解析 JSON（M7）：直接 ``json.loads(bytes)`` 跳过中间 str。

        Python 的 ``json.loads`` 原生接受 bytes（按 UTF-8/16/32 + BOM 自动
        探测），大响应路径（全市场 / K 线）省去一次全量 decode 的 str 中间
        拷贝，峰值内存更低。UTF-8 系（东财等）直接可用；GBK 系请继续用
        :meth:`text` + ``json.loads``。
        """
        return json.loads(self.body)


class HttpClient:
    """HTTP 客户端接口。"""

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 5.0
    ) -> HttpResponse:  # pragma: no cover
        raise NotImplementedError

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:  # pragma: no cover
        """POST 请求（部分目录型接口如中证指数只接受 POST JSON）。"""
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass


class UrllibClient(HttpClient):
    """标准库实现（零依赖）。"""

    def __init__(self, default_headers: Mapping[str, str] | None = None) -> None:
        self.default_headers = dict(default_headers or {})

    def _merged_headers(
        self, headers: Mapping[str, str] | None, content_type: str | None
    ) -> dict[str, str]:
        hdrs = {
            "User-Agent": _DEFAULT_UA,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            **self.default_headers,
            **(headers or {}),
        }
        if content_type:
            hdrs["Content-Type"] = content_type
        return hdrs

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 5.0
    ) -> HttpResponse:
        req = urllib.request.Request(url, method="GET")
        for k, v in self._merged_headers(headers, None).items():
            req.add_header(k, v)
        return self._open(req, url, timeout)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        req = urllib.request.Request(
            url,
            method="POST",
            data=body if body is not None else b"",
        )
        hdrs = self._merged_headers(headers, content_type or "application/json")
        for k, v in hdrs.items():
            req.add_header(k, v)
        return self._open(req, url, timeout)

    def _open(self, req: urllib.request.Request, url: str, timeout: float) -> HttpResponse:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                body = _decode_body(raw, enc)
                return HttpResponse(resp.status, body, dict(resp.headers))
        except urllib.error.HTTPError as exc:
            body = b""
            with contextlib.suppress(Exception):
                body = exc.read()
            return HttpResponse(exc.code, body, dict(exc.headers or {}))
        except urllib.error.URLError as exc:
            raise WebSourceError(f"网络错误: {exc}", context={"url": url}, cause=exc) from exc
        except TimeoutError as exc:
            from ..errors import ReadTimeout

            raise ReadTimeout(f"请求超时: {url}", context={"url": url}, cause=exc) from exc


class StdlibPooledClient(HttpClient):
    """零依赖连接复用后端（M5）：基于 :mod:`http.client` 的 keep-alive 连接池。

    :class:`UrllibClient` 每请求经 ``urllib.request.urlopen`` 新建 TCP（无
    keep-alive 生效路径）；本实现按 ``(scheme, host, port)`` 维护连接池，
    同一主机的连续请求复用同一 TCP 连接，配合 ``Connection: keep-alive``
    大幅降低建连开销。装了 httpx 的用户不受影响（:func:`build_client` 仍
    优先 httpx）。线程安全：连接池加锁，空闲连接超时自动关闭回收。
    """

    #: 空闲连接存活上限（秒）：超过即关闭回收（防连接泄漏）
    IDLE_TIMEOUT = 60.0
    #: 最大连接数（防止极端并发下无界增长）
    MAX_POOL = 16
    #: 跟随重定向的最大次数（http.client 不自动跟随）
    MAX_REDIRECTS = 5

    def __init__(
        self,
        default_headers: Mapping[str, str] | None = None,
        *,
        idle_timeout: float | None = None,
    ) -> None:
        self.default_headers = dict(default_headers or {})
        self.idle_timeout = idle_timeout or self.IDLE_TIMEOUT
        self._pool: dict[tuple[str, str, int], tuple[http.client.HTTPConnection, float]] = {}
        self._lock = threading.Lock()

    def _merged_headers(
        self, headers: Mapping[str, str] | None, content_type: str | None
    ) -> dict[str, str]:
        hdrs = {
            "User-Agent": _DEFAULT_UA,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            **self.default_headers,
            **(headers or {}),
        }
        if content_type:
            hdrs["Content-Type"] = content_type
        return hdrs

    def _acquire(self, key: tuple[str, str, int]) -> http.client.HTTPConnection | None:
        """取（或重建）连接：过期/失效即关闭并淘汰，顺带清理过期空闲连接。"""
        now = time.monotonic()
        with self._lock:
            # 全池清扫过期连接（懒回收，防止空闲连接堆积）
            stale = [k for k, (_, last) in self._pool.items() if now - last > self.idle_timeout]
            for k in stale:
                c, _ = self._pool.pop(k, (None, 0.0))
                if c is not None:
                    with contextlib.suppress(Exception):
                        c.close()
            entry = self._pool.pop(key, None)
        if entry is None:
            return None
        conn, last = entry
        if now - last > self.idle_timeout or len(self._pool) >= self.MAX_POOL:
            with contextlib.suppress(Exception):
                conn.close()
            return None
        return conn

    def _release(
        self, key: tuple[str, str, int], conn: http.client.HTTPConnection, keep: bool
    ) -> None:
        """归还连接：``keep=False``（失败/服务端关闭）直接关闭。"""
        if not keep:
            with contextlib.suppress(Exception):
                conn.close()
            return
        with self._lock:
            if len(self._pool) < self.MAX_POOL:
                self._pool[key] = (conn, time.monotonic())
            else:
                with contextlib.suppress(Exception):
                    conn.close()

    def _connect(
        self, scheme: str, host: str, port: int, timeout: float
    ) -> http.client.HTTPConnection:
        if scheme == "https":
            return http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ssl.create_default_context()
            )
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def _request_once(
        self,
        url: str,
        method: str,
        *,
        headers: Mapping[str, str] | None,
        timeout: float,
        body: bytes | None,
    ) -> HttpResponse:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        key = (scheme, host, port)

        hdrs = dict(self._merged_headers(headers, None))
        if body is not None:
            hdrs["Content-Length"] = str(len(body))

        conn = self._acquire(key)
        if conn is None:
            conn = self._connect(scheme, host, port, timeout)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            enc = (resp.getheader("Content-Encoding") or "").lower()
            out = HttpResponse(resp.status, _decode_body(raw, enc), dict(resp.getheaders()))
            keep = not resp.will_close
        except (http.client.HTTPException, OSError, TimeoutError) as exc:
            self._release(key, conn, keep=False)
            if isinstance(exc, TimeoutError):
                from ..errors import ReadTimeout

                raise ReadTimeout(f"请求超时: {url}", context={"url": url}, cause=exc) from exc
            raise WebSourceError(f"网络错误: {exc}", context={"url": url}, cause=exc) from exc
        self._release(key, conn, keep=keep)
        return out

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 5.0
    ) -> HttpResponse:
        return self._request_with_redirects(url, "GET", headers=headers, timeout=timeout, body=None)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        hdrs = dict(headers or {})
        if content_type:
            hdrs["Content-Type"] = content_type
        elif "content-type" not in {k.lower() for k in hdrs}:
            hdrs["Content-Type"] = "application/json"
        return self._request_with_redirects(url, "POST", headers=hdrs, timeout=timeout, body=body)

    def _request_with_redirects(
        self,
        url: str,
        method: str,
        *,
        headers: Mapping[str, str] | None,
        timeout: float,
        body: bytes | None,
    ) -> HttpResponse:
        current = url
        for _ in range(self.MAX_REDIRECTS + 1):
            resp = self._request_once(current, method, headers=headers, timeout=timeout, body=body)
            if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                loc = resp.headers["Location"]
                current = urllib.parse.urljoin(current, loc)
                # 303 规范要求改 GET；307/308 保持原方法
                if resp.status == 303 and method != "GET":
                    method = "GET"
                    body = None
                continue
            return resp
        raise WebSourceError(f"重定向次数超过 {self.MAX_REDIRECTS} 上限", context={"url": url})

    def close(self) -> None:
        with self._lock:
            conns = list(self._pool.values())
            self._pool.clear()
        for conn, _ in conns:
            with contextlib.suppress(Exception):
                conn.close()


def _decode_body(raw: bytes, encoding: str) -> bytes:
    if "gzip" in encoding:
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            return raw
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            return raw
    return raw


class HttpxClient(HttpClient):
    """httpx 实现（需 ``pip install tstdx[web]``）。"""

    def __init__(
        self, default_headers: Mapping[str, str] | None = None, http2: bool = False
    ) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise DependencyMissingError(
                "HttpxClient 需要 httpx: pip install 'tstdx[web]'", cause=exc
            ) from exc
        self._httpx = httpx
        self._client = httpx.Client(
            headers={"User-Agent": _DEFAULT_UA, **(default_headers or {})},
            timeout=10.0,
            follow_redirects=True,
            http2=http2,
        )

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 5.0
    ) -> HttpResponse:
        try:
            r = self._client.get(url, headers=dict(headers or {}), timeout=timeout)
            return HttpResponse(r.status_code, r.content, dict(r.headers))
        except Exception as exc:
            raise WebSourceError(f"httpx 请求失败: {exc}", context={"url": url}, cause=exc) from exc

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        """POST 请求：签名与 :meth:`UrllibClient.post` 对齐。

        缺失该覆写会让 httpx 环境（``pip install tstdx[web]``）下所有
        POST 型源（人气榜等）直接抛基类 ``NotImplementedError``。
        """
        hdrs = dict(headers or {})
        if content_type:
            hdrs.setdefault("Content-Type", content_type)
        elif "content-type" not in {k.lower() for k in hdrs}:
            hdrs["Content-Type"] = "application/json"
        try:
            r = self._client.post(
                url, content=body if body is not None else b"", headers=hdrs, timeout=timeout
            )
            return HttpResponse(r.status_code, r.content, dict(r.headers))
        except Exception as exc:
            raise WebSourceError(f"httpx 请求失败: {exc}", context={"url": url}, cause=exc) from exc

    def close(self) -> None:
        self._client.close()


def build_client(
    prefer_httpx: bool = True, default_headers: Mapping[str, str] | None = None
) -> HttpClient:
    if prefer_httpx:
        try:
            return HttpxClient(default_headers)
        except DependencyMissingError:
            pass
    # M5：零依赖后端优先连接复用版（http.client keep-alive 池）；
    # 需要 urllib 语义（redirect 自动跟随等）的调用方可显式构造 UrllibClient。
    return StdlibPooledClient(default_headers)


# --------------------------------------------------------------------------- #
# 限流
# --------------------------------------------------------------------------- #
class TokenBucket:
    """令牌桶限流器（线程安全）。"""

    __slots__ = ("rate", "capacity", "_tokens", "_last", "_lock")

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        rate = float(rate)
        if rate <= 0:
            raise ValueError(f"令牌桶速率必须为正数，收到 rate={rate!r}")
        self.rate = rate
        self.capacity = float(capacity if capacity is not None else max(rate, 1))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(
        self, tokens: float = 1.0, *, block: bool = True, timeout: float | None = None
    ) -> bool:
        """获取令牌；阻塞模式下最多等待 ``timeout`` 秒（缺省 30s，C7-web）。

        Returns
        -------
        bool
            是否成功取得令牌。非阻塞模式拿不到即 ``False``；阻塞模式超时
            同样返回 ``False``（:class:`RateLimiter` 会在该情形抛出带 advice
            的 :class:`~tstdx.errors.WebRateLimited`）。
        """
        if not block:
            return self._try_consume(tokens)
        effective_timeout = DEFAULT_ACQUIRE_TIMEOUT if timeout is None else float(timeout)
        deadline = time.monotonic() + effective_timeout
        while True:
            if self._try_consume(tokens):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(remaining, 1.0 / max(self.rate, 0.1) * 0.2))

    def set_rate(self, rate: float) -> None:
        """线程安全更新速率（M12：共享桶速率需可显式变更）。

        容量随速率收缩（与 transport 侧 :class:`TokenBucket.set_rate` 同策略），
        既有令牌保留但不超过新容量。
        """
        rate = float(rate)
        if rate <= 0:
            raise ValueError(f"令牌桶速率必须为正数，收到 rate={rate!r}")
        with self._lock:
            self.rate = rate
            self.capacity = max(rate, 1.0)
            self._tokens = min(self._tokens, self.capacity)

    def _try_consume(self, tokens: float) -> bool:
        with self._lock:
            return self._consume(tokens)

    def _consume(self, tokens: float) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class RateLimiter:
    """按源分组的限流器。

    ``block=True``（默认）时限流等待受 :data:`DEFAULT_ACQUIRE_TIMEOUT`
    总超时保护：超时抛 :class:`~tstdx.errors.WebRateLimited`（带可重试
    advice），调用方（降级链 / 门面）按 TdxError 正常分诊，不会死等。
    ``block=False`` 保持既有 bool 契约（拿不到令牌返回 ``False``）。
    """

    def __init__(
        self, rates: Mapping[str, int | float] | None = None, default: int | float = 5
    ) -> None:
        self.default = float(default)
        self._buckets: dict[str, TokenBucket] = {}
        for k, v in (rates or {}).items():
            if k != "_default":
                self._buckets[k] = TokenBucket(float(v))

    def acquire(self, source: str, *, block: bool = True, timeout: float | None = None) -> bool:
        bucket = self._buckets.get(source)
        if bucket is None:
            bucket = self._buckets[source] = TokenBucket(self.default)
        ok = bucket.acquire(block=block, timeout=timeout)
        if not ok and block:
            waited = DEFAULT_ACQUIRE_TIMEOUT if timeout is None else float(timeout)
            raise WebRateLimited(
                f"等待 {source!r} 限流令牌超过 {waited:.0f}s，已放弃本次请求",
                context={"source": source, "waited_seconds": waited},
                advice=RetryAdvice(retryable=True, backoff=waited, note="限流等待超时，稍后重试"),
            )
        return ok

    def set_rate(self, source: str, rate: float) -> None:
        self._buckets[source] = TokenBucket(rate)

    def set_bucket(self, source: str, bucket: TokenBucket) -> None:
        """B9：注入共享桶的公共入口（替代对 ``_buckets`` 私有属性的直接写入）。

        门面 / 全局注册表需要把进程级桶挂到源实例上；原先直接写
        ``rate_limiter._buckets[spec.name]`` 存在两个隐患：
        ① 跨文件写私有属性，重构时容易被遗漏；② 若外部误用 ``set_rate`` 会覆盖共享桶。提供显式公共方法，语义清晰。
        """
        self._buckets[source] = bucket
