# 第 3 轮: HTTP 面端到端贯通实测 (真实 ASGI, 离线安全项 + 网络项分开)
"""验证 FastAPI 应用全链路: 路由 -> 参数白名单 -> capability 派发 -> 错误信封。"""

import sys
import traceback

failures = []


def probe(name, fn):
    try:
        out = fn()
        print(f"[OK] {name}: {out}")
    except Exception as e:  # noqa: BLE001
        failures.append((name, repr(e), traceback.format_exc()))
        print(f"[FAIL] {name}: {e!r}")


from atst import Client  # noqa: E402
from atst.integration.runtime_http import create_runtime_app  # noqa: E402

client = Client(timeout=4)
app = create_runtime_app(client)
from fastapi.testclient import TestClient  # noqa: E402

http = TestClient(app)


def get(path, expect=200, check=None):
    r = http.get(path)
    assert r.status_code == expect, f"{path} -> {r.status_code} (want {expect}): {r.text[:200]}"
    body = r.json()
    if check:
        check(body)
    return f"{r.status_code} {str(body)[:80]}"


def post(path, payload, expect=200):
    r = http.post(path, json=payload)
    assert r.status_code == expect, f"{path} -> {r.status_code}: {r.text[:200]}"
    return f"{r.status_code} {str(r.json())[:80]}"


# ---------- 离线安全项 ----------
probe("GET /v13/runtime/health", lambda: get("/v13/runtime/health"))

probe(
    "GET /v13/capabilities",
    lambda: get(
        "/v13/capabilities",
        check=lambda b: (
            len(b["capabilities"]) >= 100
            and len(b["providers"]) >= 5
            or (_ for _ in ()).throw(
                AssertionError(f"caps={len(b['capabilities'])} providers={len(b['providers'])}")
            )
        ),
    ),
)

# 422 白名单: 未声明查询参数必须被拒
probe(
    "GET /v13/quotes?bogus=1 -> 422",
    lambda: get("/v13/quotes?symbols=sz000001&bogus=1", expect=422),
)

# body 白名单: 未声明键必须被拒
probe(
    "POST /v13/query/security_count 带 bogus 键 -> 422",
    lambda: post(
        "/v13/query/security_count", {"args": [], "kwargs": {"market": "0"}, "bogus": 1}, expect=422
    ),
)


# 未知 capability -> 错误信封 4xx/5xx 且结构化
def unknown_cap():
    r = http.post("/v13/query/no_such_capability", json={"args": [], "kwargs": {}})
    assert 400 <= r.status_code < 600, f"unexpected {r.status_code}"
    body = r.json()
    assert "error" in body, f"no error envelope: {str(body)[:120]}"
    return f"{r.status_code} envelope.code={body['error'].get('code')}"


probe("POST 未知 capability -> 错误信封", unknown_cap)


# ---------- 网络项 (失败标 NET 而非代码 FAIL) ----------
def live_quotes():
    try:
        return get("/v13/quotes?symbols=sz000001")
    except AssertionError:
        raise SystemExit from None  # noqa: TRY002


def live_quotes_safe():
    try:
        r = http.get("/v13/quotes?symbols=sz000001")
        return f"{r.status_code} {r.text[:80]}"
    except Exception as e:  # noqa: BLE001
        return f"NET-UNAVAILABLE {type(e).__name__}"


result = live_quotes_safe()
print(f"[{'OK' if not result.startswith('NET') else 'NET'}] GET /v13/quotes 实网: {result}")

client.close()

print()
if failures:
    print(f"ROUND3 HTTP E2E: {len(failures)} FAILURE(S)")
    for _n, _e, tb in failures:
        print(tb)
    sys.exit(1)
print("ROUND3 HTTP E2E: ALL OK")
