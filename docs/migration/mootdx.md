# 从 mootdx 迁移

> **垫片状态**：早期版本提供过 `tstdx.compat.mootdx` 兼容垫片，已在 v1.2.0 清理批次中移除
> （与已删除的 `_async_bridge`/`protocol.requests` 同类归并）。迁移请直接使用原生
> `TdxClient` API。

## 原生 API（推荐）

```python
from tstdx import TdxClient

client = TdxClient()
bars = client.bars("sh600036", period="day", count=100)
quotes = client.quotes(["sh600036", "sz000001"])
```

### frequency → period 对照

| mootdx frequency | 含义 | tstdx period |
|---|---|---|
| 0 | 5 分钟 | `"5m"` |
| 1 | 15 分钟 | `"15m"` |
| 2 | 30 分钟 | `"30m"` |
| 3 | 1 小时 | `"1h"` |
| 4 | 日线 | `"day"` |
| 5 | 周线 | `"week"` |
| 6 | 月线 | `"month"` |
| 7 | 1 分钟 | `"1m"` |
| 8 | 1 分钟（季） | `"1m"` |
| 9 | 日线 | `"day"` |
| 10 | 季线 | `"quarter"` |
| 11 | 年线 | `"year"` |

### symbol 规则

mootdx 的 `symbol="600036"` + 隐式市场；tstdx 推荐 `sh600036`/`sz000001` 前缀写法（不带前缀时按规则推断市场）。

## 本地文件读取

```python
# mootdx: from mootdx.reader import Reader
# tstdx:
from tstdx.reader.formats import read_day_file

bars = read_day_file("C:/new_tdx/vipdoc/sh/lday/sh600036.day")
```

## 行为差异

- **异常体系**：mootdx 返回 `None`/抛裸异常；tstdx 抛 `TdxError` 树（40+ 类），每类带 `RetryAdvice`
- **异步**：tstdx 有完整 `AsyncTdxClient` 镜像
- **降级**：`TdxClient` 为 TDX 主站**直连**（无自动降级，主站不可达时抛 `TdxError`）；需要多级降级（Web 源/本地文件兜底）请使用 `DataSourceRouter`（见 `tstdx.sources`）
