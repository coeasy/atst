"""F4/V2+V6：HTTP 服务面安全加固测试（方法白名单 / 任务上限 / body 上限 / 错误面）。

全部离线：FakeClient + FastAPI TestClient，无网络。
"""

from __future__ import annotations

import threading
import time

import pytest

pytestmark = pytest.mark.unit

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tstdx.integration.http_server import (  # noqa: E402
    MAX_BODY_BYTES,
    SAFE_CLIENT_METHODS,
    TaskStore,
    TaskStoreFull,
    _LazyClient,
    create_app,
)


class FakeClient:
    """鸭子类型客户端 —— 覆盖端点用到的方法。"""

    def quotes(self, codes):
        return [{"code": c, "price": 10.0} for c in codes]

    def security_count(self, market):
        return 42

    def close(self):  # 破坏性方法：应被白名单拒绝
        raise AssertionError("close must never be dispatched")


@pytest.fixture()
def client():
    app = create_app(client=FakeClient())
    with TestClient(app) as tc:
        yield tc


# --------------------------------------------------------------------------- #
# 方法白名单（V2）
# --------------------------------------------------------------------------- #
def test_safe_methods_frozenset_excludes_destructive():
    """白名单是模块级 frozenset，且不含 close/open/request 等破坏性方法。"""
    assert isinstance(SAFE_CLIENT_METHODS, frozenset)
    for bad in ("close", "open", "request"):
        assert bad not in SAFE_CLIENT_METHODS


def test_query_whitelist_allows_quotes(client):
    r = client.post("/query", json={"method": "quotes", "args": [["600519"]]})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"][0]["code"] == "600519"


def test_query_whitelist_rejects_close(client):
    """/query 不能再派发 close() 打瘫共享池（V2 主诉）。"""
    r = client.post("/query", json={"method": "close"})
    assert r.status_code == 404


def test_query_whitelist_rejects_raw_request(client):
    r = client.post("/query", json={"method": "request", "args": [3050, "00"]})
    assert r.status_code == 404


def test_query_bad_args_shape_400(client):
    r = client.post("/query", json={"method": "quotes", "args": "not-a-list"})
    assert r.status_code == 400


def test_query_native_exception_generic(client):
    """原生异常对外只回 internal error，不外泄异常类型与消息（V6）。"""
    app = create_app(
        client=type(
            "Boom",
            (),
            {"quotes": lambda self, codes: (_ for _ in ()).throw(ValueError("secret C:\\x"))},
        )()
    )
    with TestClient(app, raise_server_exceptions=False) as tc:
        r = tc.post("/query", json={"method": "quotes", "args": [["600519"]]})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "internal error"
    assert "secret" not in r.text
    assert "ValueError" not in r.text


def test_tasks_rejects_destructive_method(client):
    """close 是真实客户端已知方法但破坏性 → 403（区别于未知方法的 400）。"""
    r = client.post("/tasks", json={"method": "close"})
    assert r.status_code == 403


def test_tasks_unknown_method_still_400(client):
    r = client.post("/tasks", json={"method": "definitely_not_a_method"})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# 任务上限与结果体上限（V2）
# --------------------------------------------------------------------------- #
def test_taskstore_full_raises_and_maps_409():
    store = TaskStore(max_tasks=1)
    gate = threading.Event()
    release = threading.Event()
    store.submit(gate.wait)  # 占满唯一活跃槽
    with pytest.raises(TaskStoreFull):
        store.submit(release.wait)
    release.set()
    gate.set()
    # 已完结任务占满登记簿时提交不拒绝（逐出最早的完结记录）
    for _ in range(80):
        if all(r["status"] in ("done", "cancelled") for r in store.list()):
            break
        time.sleep(0.02)
    tid = store.submit(lambda: 1)
    assert tid
    store.delete(tid)


def test_task_submit_over_limit_maps_409_via_http(monkeypatch):
    """活跃任务达到 max_tasks → submit 抛 TaskStoreFull → HTTP 409。"""

    class FullStore(TaskStore):
        def submit(self, fn, *args, **kw):
            raise TaskStoreFull("too many active tasks; max_tasks=1")

    monkeypatch.setattr("tstdx.integration.http_server.TaskStore", FullStore)
    app = create_app(client=FakeClient())
    with TestClient(app) as tc:
        r = tc.post("/tasks", json={"method": "quotes", "args": [["600519"]]})
        assert r.status_code == 409


def test_task_result_truncated_for_huge_list():
    store = TaskStore()
    tid = store.submit(lambda: [{"i": i} for i in range(5000)])
    for _ in range(100):
        rec = store.get(tid)
        if rec["status"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert rec["status"] == "done"
    assert rec["result"]["truncated"] is True
    assert rec["result"]["total_rows"] == 5000
    assert len(rec["result"]["rows"]) == 1000


def test_task_error_message_is_safe():
    """任务失败记录不外泄原生异常细节（V6）。"""
    store = TaskStore()

    def _boom():
        raise ValueError("secret internal detail")

    tid = store.submit(_boom)
    for _ in range(100):
        rec = store.get(tid)
        if rec["status"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert rec["status"] == "failed"
    assert rec["error"] == "internal error"
    assert "secret" not in rec["error"]


# --------------------------------------------------------------------------- #
# 请求体上限（V2）
# --------------------------------------------------------------------------- #
def test_post_body_over_4mb_returns_413(client):
    huge = "x" * (MAX_BODY_BYTES + 16)
    r = client.post(
        "/query",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_post_body_under_limit_normal(client):
    r = client.post("/query", json={"method": "quotes", "args": [["600519"]]})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# 解析异常面 500 → 400（V2/V6）
# --------------------------------------------------------------------------- #
def test_stock_changes_invalid_types_returns_400(client):
    r = client.get("/stock_changes?types=abc,def")
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# N1：/f10/{symbol}/catalog 端点（F10 族能力，注入 F10Client 鸭子类型）
# --------------------------------------------------------------------------- #
def test_f10_catalog_endpoint_with_f10_client():
    class F10Fake:
        def f10_catalog(self, symbol: str):
            return [
                {"title": "公司概况", "filename": "gsgk.dat"},
                {"title": "财务分析", "filename": "cwbj.dat"},
            ]

    app = create_app(client=F10Fake())
    with TestClient(app) as tc:
        r = tc.get("/f10/sh600519/catalog")
    assert r.status_code == 200
    assert r.json()["data"][0]["title"] == "公司概况"
    assert r.json()["data"][1]["filename"] == "cwbj.dat"


def test_f10_catalog_in_safe_methods():
    """f10_catalog 属只读白名单方法，可经 /query 派发。"""
    assert "f10_catalog" in SAFE_CLIENT_METHODS
    app = create_app(
        client=type(
            "F", (), {"f10_catalog": lambda self, s: [{"title": "t", "filename": "f.dat"}]}
        )()
    )
    with TestClient(app) as tc:
        r = tc.post("/query", json={"method": "f10_catalog", "args": ["sh600519"]})
    assert r.status_code == 200
    assert r.json()["success"] is True


# --------------------------------------------------------------------------- #
# _LazyClient 存在性探测（V2）
# --------------------------------------------------------------------------- #
def test_lazy_client_unknown_attribute_raises():
    lazy = _LazyClient()
    with pytest.raises(AttributeError):
        _ = lazy.definitely_not_a_method


def test_lazy_client_known_method_still_lazy_callable():
    lazy = _LazyClient()
    assert callable(lazy.quotes)  # 存在性探测通过；未触发实例化
    assert lazy._client is None


# --------------------------------------------------------------------------- #
# 原生异常 → 500 generic（V6）
# --------------------------------------------------------------------------- #
def test_endpoint_native_exception_returns_generic_500():
    app = create_app(
        client=type(
            "Boom",
            (),
            {"quotes": lambda self, codes: (_ for _ in ()).throw(RuntimeError("db password"))},
        )()
    )
    with TestClient(app, raise_server_exceptions=False) as tc:
        r = tc.get("/quotes/600519")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "E9001"
    assert body["error"]["message"] == "internal error"
    assert "password" not in r.text
