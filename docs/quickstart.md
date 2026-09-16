# tstdx 快速开始

本文对应当前 `1.0.0` Draft 开发线；最新已发布稳定版是 `v1.0.0`。要求 Python 3.10 或更高版本。

## 安装

```bash
# 基础安装（零依赖）
pip install tstdx

# 完整功能
pip install "tstdx[all]"
```

## 5 分钟上手

### 1. 获取 K 线数据

```python
from tstdx import TdxClient

client = TdxClient()

# 日线，最近 80 根
bars = client.bars("sh600519", period="day", count=80)
for bar in bars[:5]:
    print(f"{bar.date} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")
```

### 2. 实时行情

```python
quotes = client.quotes(["sh600519", "sz000001"])
for q in quotes:
    print(f"{q.code} {q.name}: 价格={q.price} 涨跌={q.change_pct:+.2f}%")
```

### 3. 异步并发

```python
import asyncio
from tstdx import AsyncTdxClient


async def main():
    client = AsyncTdxClient()
    bars, quotes = await asyncio.gather(
        client.bars("sh600519", period="day", count=80),
        client.quotes(["sh600519", "sz000001"]),
    )
    print(f"K线 {len(bars)} 条，行情 {len(quotes)} 只")


asyncio.run(main())
```

### 4. 数据落地

```python
from tstdx.output import write

bars = client.bars("sh600519", period="day", count=250)

# DataFrame（需 pandas）
write(bars, "output://dataframe")

# Parquet（需 pyarrow）
write(bars, "parquet://kline_600519.parquet")

# DuckDB（需 duckdb）
write(bars, "duckdb://market.db?table=kline")
```

### 5. CLI

```bash
tstdx bars sh600519 --period day --count 20
tstdx quotes sh600519 sz000001
tstdx server-test          # 主站测速
tstdx stream sh600519      # 流式订阅
```

### 6. v14 Runtime（编排内核 + 批量执行）

v14 Runtime 是统一编排入口，支持单次查询、批量执行（语义缓存去重 + 并发）
和流式订阅。所有网关（CLI/REST/WS/MCP）均委托 Runtime 执行。

```python
from tstdx.runtime import RuntimeGateway, create_runtime, QueryRequest
from tstdx.cache_semantic import SemanticResultCache

# 创建带语义缓存的 Runtime
gateway = RuntimeGateway(
    create_runtime(
        semantic_cache=SemanticResultCache(),
        default_cache_ttl=60.0,
    )
)

# 单次查询
resp = gateway.bars("sh600519", period="day", count=30)
if resp.success:
    print(resp.data[0]["close"])

# 批量执行（3 只股票，语义缓存自动去重）
reqs = [
    QueryRequest(operation="bars", args=("sh600000",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh600519",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh000001",), params={"count": 30}),
]
results = gateway.execute_batch(reqs, max_concurrent=4)
for r in results:
    print(r.data[0]["symbol"] if r.success else r.error)

# 缓存诊断
print(gateway.semantic_cache_stats())
# {'enabled': True, 'tier': 'l1', 'size': 3}
```

### 7. 主站池与热更新

连接池默认惰性建连，并在请求失败时按主站健康状态切换。需要主动刷新主站排序时，
可使用 `bestip()` 或 CLI 的 `hosts audit`；后台测速只更新排序，不会覆盖真实请求健康
状态。连接池热更新期间，在飞请求会继续使用原 generation 完成，避免旧结果污染新主站。

## 核心概念

### Provider 绑定（禁止跨 Provider 静默降级）

v13 clean break 之后，`DataSourceRouter` 退化为**单 Provider 选择器**，不再按顺序逐层尝试：

- 每次请求绑定**恰好一个** Provider / channel；
- TDX 失败**不会**自动改走 Web 源、本地 vipdoc、缓存或合成数据；
- 需要跨 Provider 容错时，必须显式构造 `FallbackPolicy` 交给 `ProviderOrchestrator`，
  并在返回值 provenance 中留下 `requested_provider` / `fallback` 审计痕迹。

禁止跨 Provider silent fallback；任何降级都必须是调用方的显式选择。

### 三态输出

```python
client.bars("sh600519", as_format="dict")  # list[dict]
client.bars("sh600519", as_format="tuple")  # list[tuple]
client.bars("sh600519", as_format="dataframe")  # pandas.DataFrame
```

### 错误处理

```python
from tstdx.errors import TdxError, advice_for

try:
    bars = client.bars("sh600519")
except TdxError as e:
    print(f"错误码: {e.code}")
    advice = advice_for(e)
    if advice.switch_host:
        print("建议切换主站重试")
```

## 下一步

- [v14 Runtime API 参考](api/v14-runtime.md) — 编排内核完整 API
- [Cookbook 食谱集](cookbook/README.md) — 20+ 实战场景
- [FAQ](FAQ.md) — 常见问题
- [故障排查](troubleshooting.md) — 问题诊断
- [API 参考](api/README.md) — 完整 API
