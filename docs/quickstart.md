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
from tstdx.client import TdxClient

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
from tstdx.client import AsyncTdxClient


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

# DataFrame（需 pandas；sink 由扩展名/前缀推断，内存对象显式传 fmt）
from tstdx.output import to_dataframe

df = to_dataframe(bars)

# Parquet（需 pyarrow）
write(bars, "kline_600519.parquet")

# DuckDB（需 duckdb）
write(bars, "duckdb:market.db@kline")
```

### 5. CLI

```bash
tstdx bars sh600519 --period day --count 20
tstdx quotes sh600519 sz000001
tstdx server-test          # 主站测速
tstdx stream sh600519      # 流式订阅
```

### 6. 统一查询内核（`Client`，172 项 capability）

`Client` 是唯一业务入口；它把每次请求编译为**单 Provider / 单 Channel** 的
`QueryPlan`，零缓存直调绑定实现，并在结果里携带 provenance。

```python
from tstdx import Client, FallbackPolicy, QuerySpec
from tstdx.typed_query import FundHoldingsQuery

client = Client()

# 便捷方法：返回 QueryResult，自带 provider / channel 溯源
bars = client.bars("sh600519", period="day", count=30)
print(len(bars.data), bars.meta.provider, bars.meta.channel)

# 通用面：任何 capability 走同一入口，参数在规划期按真实签名校验
result = client.execute(QuerySpec.build("bars", symbols="sh600519", period="day", count=30))

# 类型化糖衣：冻结 dataclass 契约 → 内核 → 强类型 Domain Record
typed = client.typed(FundHoldingsQuery(code="000001"))

# 批量：逐 symbol 三态（ok/missing/failed），按请求顺序审计
batch = client.quotes_batch(["sh600519", "sz000001", "sz999999"])
print(batch.status_counts)          # {'ok': n, 'failed': n, 'missing': n, 'not_attempted': n}
print(batch.items["sh600519"].status, batch.items["sh600519"].value.data[0]["price"])

# 跨源只在显式策略下发生（默认永不发生）
policy = FallbackPolicy(providers=("tdx", "tencent"))
orchestrated = client.quotes("sh600519", policy=policy)
print(orchestrated.result.meta.provider, [(a.provider, a.status) for a in orchestrated.attempts])
```

异步镜像是 `AsyncClient`（`async with AsyncClient() as client: ...`）。

### 7. 主站池与热更新

连接池默认惰性建连，并在请求失败时按主站健康状态切换。需要主动刷新主站排序时，
可使用 `bestip()` 或 CLI 的 `hosts audit`；后台测速只更新排序，不会覆盖真实请求健康
状态。连接池热更新期间，在飞请求会继续使用原 generation 完成，避免旧结果污染新主站。

## 核心概念

### Provider 绑定（禁止跨 Provider 静默降级）

v13 clean break 之后，规划器只为每次请求选定**一个** Provider / channel，
不存在按顺序逐层尝试的聚合路由器（`DataSourceRouter` 已随 v16 删除）：

- 每次请求绑定**恰好一个** Provider / channel，并由 `UnifiedRuntime` 零缓存直调该绑定；
- TDX 失败**不会**自动改走 Web 源、本地 vipdoc、缓存或合成数据；provenance 与请求
  Provider 不一致时直接抛错，而不是悄悄换源；
- 需要跨 Provider 容错时，必须显式构造 `FallbackPolicy`，由 `ProviderOrchestrator`
  逐源尝试并返回 `OrchestratedResult`，其 `attempts` 留下每次
  `provider / status / code` 审计痕迹。

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

- [API 参考](api/README.md) — 统一内核层与全部模块索引
- [架构说明](ARCHITECTURE.md) — 唯一执行路径与分层
- [Cookbook 食谱集](cookbook/README.md) — 实战场景
- [FAQ](FAQ.md) — 常见问题
- [故障排查](troubleshooting.md) — 问题诊断
