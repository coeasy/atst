# tstdx 快速开始（v1.0.0）

本文对应 v1.0.0 正式稳定版，要求 Python 3.10 或更高版本。

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

### 6. 主站池与热更新

连接池默认惰性建连，并在请求失败时按主站健康状态切换。需要主动刷新主站排序时，
可使用 `bestip()` 或 CLI 的 `hosts audit`；后台测速只更新排序，不会覆盖真实请求健康
状态。连接池热更新期间，在飞请求会继续使用原 generation 完成，避免旧结果污染新主站。

## 核心概念

### 数据源降级链

```
TDX 主站 → HTTP Web 源 → 本地 vipdoc → 缓存 → 合成数据
```

`DataSourceRouter` 按顺序尝试，任一成功即返回。可通过配置调整顺序或禁用某层。

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

- [Cookbook 食谱集](cookbook/README.md) — 20+ 实战场景
- [FAQ](FAQ.md) — 常见问题
- [故障排查](troubleshooting.md) — 问题诊断
- [API 参考](api/README.md) — 完整 API
