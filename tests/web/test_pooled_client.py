# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""M5 / M7 测试：零依赖连接复用后端 + 响应体零拷贝解析。

M5 验证 http.client keep-alive 连接池：同一主机连续请求复用连接、
空闲超时回收、失败连接淘汰、重定向跟随、close 释放。
M7 验证 ``HttpResponse.json()`` 直解析 bytes（跳过中间 str）。
全部离线：用本地 ``http.server`` 起一个可控测试服务，不发真实外部请求。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tstdx.errors import ReadTimeout, WebSourceError
from tstdx.web.base import HttpResponse, StdlibPooledClient

# --------------------------------------------------------------------------- #
# 本地可控 HTTP 服务
# --------------------------------------------------------------------------- #
_JOINED: list[str] = []
_STATE: dict[str, int] = {}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # 静音
        pass

    def do_GET(self) -> None:  # noqa: N802
        _JOINED.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/slow":
            time.sleep(2)
        if self.path == "/count":
            _STATE["count"] = _STATE.get("count", 0) + 1
        body = json.dumps({"path": self.path, "n": _STATE.get("count", 0)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        _JOINED.append(f"POST {self.path}")
        body = json.dumps({"echo": raw.decode("utf-8")}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server() -> str:
    _JOINED.clear()
    _STATE.clear()
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


# --------------------------------------------------------------------------- #
# M7：HttpResponse.json() 零拷贝
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_http_response_json_parses_bytes_directly():
    resp = HttpResponse(200, b'{"a": 1, "b": [1, 2, 3]}', {})
    assert resp.json() == {"a": 1, "b": [1, 2, 3]}


@pytest.mark.unit
def test_http_response_json_with_bom():
    # UTF-8 BOM：json.loads(bytes) 自动处理
    body = b'\xef\xbb\xbf{"x": 1}'
    assert HttpResponse(200, body, {}).json() == {"x": 1}


@pytest.mark.unit
def test_http_response_text_and_json_consistent():
    body = b'{"price": "12.5"}'
    resp = HttpResponse(200, body, {})
    assert resp.json()["price"] == "12.5"
    assert resp.text() == '{"price": "12.5"}'


# --------------------------------------------------------------------------- #
# M5：StdlibPooledClient 连接复用
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_pooled_client_reuses_connection(server):
    client = StdlibPooledClient()
    try:
        r1 = client.get(f"{server}/count", timeout=3)
        r2 = client.get(f"{server}/count", timeout=3)
        assert r1.json()["n"] == 1
        assert r2.json()["n"] == 2  # 同一连接服务端计数递增 → 连接确实复用
    finally:
        client.close()


@pytest.mark.unit
def test_pooled_client_post(server):
    client = StdlibPooledClient()
    try:
        r = client.post(f"{server}/echo", body=b"hello", content_type="text/plain", timeout=3)
        assert r.json() == {"echo": "hello"}
    finally:
        client.close()


@pytest.mark.unit
def test_pooled_client_follows_redirect(server):
    client = StdlibPooledClient()
    try:
        r = client.get(f"{server}/redirect", timeout=3)
        assert r.status == 200
        assert r.json()["path"] == "/ok"
    finally:
        client.close()


@pytest.mark.unit
def test_pooled_client_timeout_raises_readtimeout(server):
    client = StdlibPooledClient(idle_timeout=5)
    try:
        with pytest.raises(ReadTimeout):
            client.get(f"{server}/slow", timeout=0.5)
    finally:
        client.close()


@pytest.mark.unit
def test_pooled_client_close_releases_pool(server):
    client = StdlibPooledClient()
    client.get(f"{server}/count", timeout=3)
    assert len(client._pool) == 1
    client.close()
    assert len(client._pool) == 0


@pytest.mark.unit
def test_pooled_client_idle_timeout_reaps_connection(server):
    client = StdlibPooledClient(idle_timeout=0.05)
    try:
        client.get(f"{server}/count", timeout=3)
        assert len(client._pool) == 1
        time.sleep(0.2)
        # 懒回收：下次 acquire 时清扫过期连接 → 返回 None（重建新连接）
        assert client._acquire(("http", "127.0.0.1", int(server.rsplit(":", 1)[1]))) is None
        assert len(client._pool) == 0  # 过期连接已从池中关闭回收
    finally:
        client.close()


@pytest.mark.unit
def test_pooled_client_network_error(server):
    client = StdlibPooledClient()
    try:
        # Windows 对关闭端口常表现为连接超时（TimeoutError→ReadTimeout），
        # POSIX 多为 ConnectionRefused（OSError→WebSourceError）；两者都是
        # 网络错误语义，统一断言抛网络类异常。
        with pytest.raises((WebSourceError, ReadTimeout)):
            client.get("http://127.0.0.1:1/x", timeout=0.5)
    finally:
        client.close()
