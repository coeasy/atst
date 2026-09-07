"""C1 HTTP 服务离线测试（fake client + TestClient，无网络）。"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.unit

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tstdx.integration.http_server import TaskStore, create_app  # noqa: E402


class FakeClient:
    """鸭子类型客户端 —— 覆盖端点用到的方法。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def quotes(self, codes):
        self.calls.append(("quotes", codes))
        return [{"code": c, "price": 10.0 + i} for i, c in enumerate(codes)]

    def snapshot(self, codes):
        return [{"code": c} for c in codes]

    def block_quotes(self, block):
        return [{"block": block, "count": 3}]

    def minute_today(self, symbol):
        return [{"time": "09:30", "price": 10.0}]

    def minute_history(self, symbol, date):
        return [{"date": date}]

    def trade_today(self, symbol):
        return [{"price": 10.0, "vol": 100}]

    def volume_price_dist(self, symbol):
        return {"price": [10.0], "vol": [100]}

    def finance_info(self, symbol):
        return {"code": symbol, "roe": 15.0}

    def capital_changes(self, symbol):
        return [{"date": "2024-06-03"}]

    def auction_snapshot(self, codes):
        return [{"code": c} for c in codes]

    def file_download(self, symbol, filename, **kw):
        # 对齐真实签名：TdxClient.file_download(symbol, filename, *, ...)
        return {"symbol": symbol, "filename": filename, "size": 1024}

    def security_list(self, market, start=0):
        return [{"code": "600519"}]

    def security_count(self, market):
        return 42

    def goods_bars(self, symbol, *, period="day", count=320, start=0, as_format="dict"):
        # 对齐真实签名：GoodsClient.goods_bars(symbol, *, period, count, start, as_format)
        return [{"symbol": symbol, "period": period}]

    def goods_quote(self, symbol, as_format="dict"):
        return {"symbol": symbol}

    def ex_bars(self, symbol, *, period="day", count=320, start=0, as_format="dict"):
        return [{"symbol": symbol, "period": period}]

    def ex_quote(self, symbol, as_format="dict"):
        return {"symbol": symbol}

    def mac_quote(self, symbol, as_format="dict"):
        return {"symbol": symbol}


@pytest.fixture()
def client():
    app = create_app(client=FakeClient())
    with TestClient(app) as tc:
        yield tc


def _count_api_routes(app) -> int:
    return sum(
        1
        for r in app.routes
        if hasattr(r, "methods")
        and r.methods
        and not r.path.startswith(("/openapi", "/docs", "/redoc"))
    )


def test_app_has_at_least_32_api_routes():
    app = create_app(client=FakeClient())
    assert _count_api_routes(app) >= 32


def test_quotes_batch_and_single(client):
    r = client.get("/quotes?codes=600519,000001")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2
    r = client.get("/quotes/600519")
    assert r.status_code == 200
    assert r.json()["data"]["code"] == "600519"


def test_quote_group_endpoints(client):
    assert client.get("/snapshot?codes=600519").status_code == 200
    assert client.get("/block_quotes?block=sh_a").status_code == 200
    assert client.get("/minute_today/600519").status_code == 200
    assert client.get("/minute_history/600519?date=2024-06-03").status_code == 200
    assert client.get("/trade_today/600519").status_code == 200
    assert client.get("/volume_price_dist/600519").status_code == 200


def test_fundamental_group(client):
    assert client.get("/finance_info/600519").json()["data"]["roe"] == 15.0
    assert client.get("/capital_changes/600519").status_code == 200
    assert client.get("/auction_snapshot?codes=600519").status_code == 200
    r = client.get("/file_download?symbol=sh600519&filename=gpcw.txt")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "sh600519"
    assert client.get("/security_list?market=1").status_code == 200
    assert client.get("/security_count/1").json()["data"] == 42


def test_goods_group(client):
    # 深审 S#1 回归：端点把 market+code 合成带前缀 symbol 后调用族客户端
    r = client.get("/goods/bars?code=AU2606&market=0")
    assert r.status_code == 200
    assert r.json()["data"][0]["symbol"] == "szAU2606"
    r = client.get("/goods/quote?code=AU2606&market=1")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "shAU2606"
    r = client.post("/goods/quotes", json={"codes": ["shAU2606"]})
    assert r.status_code == 200
    assert r.json()["data"][0]["symbol"] == "shAU2606"
    r = client.get("/goods/summary?code=AU2606&market=0")
    assert r.status_code == 200


def test_extended_group(client):
    assert client.get("/ex/bars?code=CU2606&market=1").status_code == 200
    assert client.get("/ex/quote?code=CU2606&market=0").status_code == 200
    r = client.get("/mac/quote?code=IH2606&market=1")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "shIH2606"
    r = client.get("/download?symbol=sh600519&filename=day.dat")
    assert r.status_code == 200
    assert r.json()["data"]["filename"] == "day.dat"


def test_system_group(client):
    assert client.get("/system/health").json()["status"] == "ok"
    assert "metrics" in client.get("/system/metrics").json()
    specs = client.get("/system/specs").json()
    assert specs["commands"] >= 85
    assert client.get("/system/version").json()["version"]


def test_task_lifecycle(client):
    r = client.post("/tasks", json={"method": "quotes", "args": [["600519"]]})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    for _ in range(50):
        rec = client.get(f"/tasks/{task_id}").json()["data"]
        if rec["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert rec["status"] == "done"
    assert client.get(f"/tasks/{task_id}/result").json()["result"]
    assert client.get("/tasks").json()["data"]
    assert client.post(f"/tasks/{task_id}/cancel").status_code == 404  # 已结束不可取消
    assert client.delete(f"/tasks/{task_id}").status_code == 200
    assert client.get(f"/tasks/{task_id}").status_code == 404


def test_task_unknown_method_rejected(client):
    r = client.post("/tasks", json={"method": "definitely_not_a_method"})
    assert r.status_code == 400


def test_missing_param_returns_422(client):
    assert client.get("/quotes").status_code == 422


def test_lazy_client_defaults():
    from tstdx.integration.http_server import _LazyClient

    lazy = _LazyClient()
    assert callable(lazy.quotes)  # 未触发构造


def test_taskstore_direct():
    store = TaskStore()
    tid = store.submit(lambda: 7)
    for _ in range(50):
        rec = store.get(tid)
        if rec["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert rec["result"] == 7
    assert store.delete(tid)
    assert not store.delete(tid)  # 二次删除 False


def test_family_endpoint_signature_contract():
    """S#1 契约锁：端点调用的族方法与真实客户端签名一致（防签名漂移复发）。

    族客户端方法接受 ``symbol``（市场取自前缀）、**没有** ``market`` 形参；
    ``file_download`` 首参为 symbol。
    """
    import inspect

    from tstdx.client import ExMarketClient, GoodsClient, MacClient, TdxClient

    for cls, name in (
        (GoodsClient, "goods_bars"),
        (GoodsClient, "goods_quote"),
        (ExMarketClient, "ex_bars"),
        (ExMarketClient, "ex_quote"),
        (MacClient, "mac_quote"),
    ):
        params = inspect.signature(getattr(cls, name)).parameters
        assert "symbol" in params, f"{cls.__name__}.{name} 缺 symbol 形参"
        assert "market" not in params, f"{cls.__name__}.{name} 不应含 market 形参"
    fd = inspect.signature(TdxClient.file_download).parameters
    assert list(fd)[1:3] == ["symbol", "filename"], f"file_download 签名漂移: {list(fd)}"
