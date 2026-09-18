# 从 easy_tdx / eltdx 迁移

> **垫片状态**：早期版本提供过 `tstdx.compat.easy_tdx` / `tstdx.compat.eltdx` 兼容垫片，
> 已在 v1.2.0 清理批次中移除（与已删除的 `_async_bridge` 同类归并）。迁移请直接使用
> 原生 `TdxClient` / `AsyncTdxClient` API。

## easy_tdx → tstdx

### 原生 API（推荐）

```python
from tstdx.client import TdxClient, AsyncTdxClient

client = TdxClient()
bars = client.bars("sh600519", period="day", count=80)
quotes = client.quotes(["sh600519", "sz000001"])

# 异步
import asyncio


async def main():
    ac = AsyncTdxClient()
    return await ac.bars("sh600519", period="day", count=80)
```

### category 对照表

| easy_tdx category | 含义 | tstdx period |
|---|---|---|
| 0 | 5 分钟 | `"5m"` |
| 1 | 15 分钟 | `"15m"` |
| 2 | 30 分钟 | `"30m"` |
| 3 | 1 小时 | `"1h"` |
| 4 | 日线 | `"day"` |
| 5 | 周线 | `"week"` |
| 6 | 月线 | `"month"` |
| 7 | 1 分钟 | `"1m"` |
| 8 | 1 分钟 | `"1m"` |
| 9 | 日线 | `"day"` |
| 10 | 季线 | `"quarter"` |
| 11 | 年线 | `"year"` |

未知 category 会发 `CompatibilityWarning` 并回退 `"day"`。

## eltdx → tstdx

### frequency 对照表

| eltdx frequency | tstdx period |
|---|---|
| `"1m"` / `"1min"` | `"1m"` |
| `"5m"` / `"5min"` | `"5m"` |
| `"15m"` | `"15m"` |
| `"30m"` | `"30m"` |
| `"60m"` / `"1h"` | `"1h"` |
| `"day"` / `"d"` | `"day"` |
| `"week"` / `"w"` | `"week"` |
| `"month"` / `"mo"` | `"month"` |

## 行为差异

- 返回结构统一为 tstdx 全局契约（量=股/额=元）
- 连接失败抛 `TdxError`（而非裸 `ConnectionError`），`.advice` 字段给出重试建议
- 不显式 `connect()` 也可直接调用（懒初始化）
