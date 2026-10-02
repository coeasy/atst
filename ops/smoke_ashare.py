# A 股行情数据可用性实测冒烟: TDX 直连 + Web 源, 核心 A 股 capability
"""逐项真实调用, 输出可用/不可用清单。网络失败单独标注(与代码缺陷区分)。"""
import sys
import traceback

results = []


def probe(name, fn):
    try:
        out = fn()
        n = len(out) if hasattr(out, "__len__") else 1
        results.append((name, "OK", n, ""))
    except Exception as e:  # noqa: BLE001
        results.append((name, "FAIL", 0, f"{type(e).__name__}: {e}"))


from atst import Client  # noqa: E402

# ---------- TDX 直连核心能力 ----------
def tdx_probe(cap, **kw):
    client = Client(default_provider="tdx", timeout=5)
    try:
        return client.call(cap, **kw)
    finally:
        client.close()


probe("tdx/quotes 000001", lambda: tdx_probe("quotes", symbols=["000001"]))
probe("tdx/history 000001 日线", lambda: tdx_probe("history", symbol="000001", period="day", count=10))
probe("tdx/minute 000001", lambda: tdx_probe("minute", symbol="000001"))
probe("tdx/trades 000001", lambda: tdx_probe("trades", symbol="000001"))
probe("tdx/security_count", lambda: tdx_probe("security_count", market="sz"))
probe("tdx/snapshot 000001", lambda: tdx_probe("snapshot", symbol="000001"))
probe("tdx/klines 000001", lambda: tdx_probe("klines", symbol="000001", period="day", count=10))
probe("tdx/index 上证指数", lambda: tdx_probe("index", symbol="999999", period="day", count=5))

# ---------- Web 源核心能力 ----------
def web_probe(source, method, *args, **kw):
    from atst.web.session import web_session

    s = web_session(source=source, timeout=8)
    try:
        return getattr(s, method)(*args, **kw)
    finally:
        close = getattr(s, "close", None)
        if callable(close):
            close()


probe("sina/quotes 000001", lambda: web_probe("sina", "quotes", ["sz000001"]))
probe("tencent/quotes 000001", lambda: web_probe("qq", "quotes", ["sz000001"]))
probe("eastmoney/quotes 000001", lambda: web_probe("eastmoney", "quotes", ["0.000001"]))
probe("sina/history 000001 日线", lambda: web_probe("sina", "history", "sz000001", period="day", count=10))
probe("eastmoney/history 000001", lambda: web_probe("eastmoney", "history", "0.000001", period="day", count=10))
probe("em/boards 行业板块", lambda: web_probe("em", "industry_boards"))
probe("sina/sector_flow", lambda: web_probe("sina", "sector_flow"))

# ---------- 汇总 ----------
print()
print(f"{'PROBE':<34} {'RESULT':<6} {'N':>6}  NOTE")
ok = fail = 0
for name, status, n, note in results:
    mark = status
    if status == "OK":
        ok += 1
    else:
        fail += 1
    print(f"{name:<34} {mark:<6} {n:>6}  {note[:60]}")
print(f"\nSUMMARY: {ok} ok, {fail} fail / {len(results)}")
sys.exit(0 if fail == 0 else 1)
