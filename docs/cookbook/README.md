# Cookbook 实战食谱（v1.0.0）

> 本目录随 v1.0.0 稳定版发布。下方早期 v0.4.0/v0.7.0 标记保留为食谱演进历史。

## 核心食谱（v0.4.0）

| # | 食谱 | 场景 |
|---|---|---|
| 01 | [批量拉取全市场 K 线](01_bulk_kline.md) | 建本地数据库 |
| 02 | [实时行情监控 + 显式跨源](02_realtime_fallback.md) | 盘中监控 |
| 03 | [本地 vipdoc 离线解析](03_offline_vipdoc.md) | 无网环境 |
| 04 | [流式订阅与断线恢复](04_streaming.md) | 准实时推送 |
| 05 | [数据落地三件套](05_sinks.md) | DataFrame/Parquet/DuckDB |
| 06 | [自定义协议命令](06_custom_command.md) | 扩展未知命令 |

## 单一执行内核食谱（v16/v17）

| # | 食谱 | 场景 |
|---|---|---|
| 07 | [单内核实战](07_single_kernel_queries.md) | 溯源审计 / 批量三态 / 显式跨源 / Typed Query / Domain Record / 假执行体测试 |

## 规划中（v0.7.0）

- 复权因子与前/后复权
- 板块数据与资金流
- F10 资料全文提取
- 商品期货连续合约
- 港股行情（ boc/hk 源）
- 集思录转债数据
- 多主站负载均衡
- Prometheus + Grafana 监控
- MCP 接入 LLM 工作流
- HTTP 网关部署（Docker）
- 交易日历与交割日计算
- 大文件流式解析（内存可控）
- 反爬应对与礼貌抓取

## 分时 / 分钟 K 线 capability 选型（N6 澄清）

三个易混 capability（原 `UnifiedQuoteAPI` 门面方法已随 v16 删除；现经
`client.call(capability, ...)` 或 `client.minute(...)` 调用，每次绑定恰好一个 Provider）：

| capability | 返回 | 可用 Provider | 适用 |
|---|---|---|---|
| `minute` | 当日 1 分钟分时快照 | tencent / eastmoney / baidu（tdx 声明但**已下线**，一调即抛 `NotImplementedFeature`） | 盘中盯当日分时 |
| `minute_web` | 当日分时（同源，不经 TDX）| tencent | 明确只要 HTTP 源 |
| `minute_klines` | 跨日分钟 **K 线**（Bar 序列）| tencent / eastmoney | 历史多日分钟 K 线 |

一句话：**要 K 线用 `minute_klines`，要当日分时用 `minute`，要强制 HTTP 分时用 `minute_web`**；
换源只能显式指定 `provider=` 或 `FallbackPolicy`，不会自动发生。

```python
from tstdx import Client

with Client() as client:
    # 当日分时只能走 Web Provider：tdx 的 0x0537 仍是 inferred 命令，发包前即拦下。
    points = client.minute("000001", provider="tencent").data
    ticks = client.trades("000001", provider="tencent").data
```

`minute` / `trades` 在 Web Provider 上是**单只代码一次请求**：`Client` 的语义字段会按实现
自己的形参绑定，一批代码不会被悄悄拆成第一只，实现不收的字段（例如 `trades` 的
`count` / `start`——腾讯逐笔按 `max_pages` 分页）会当场报 `ValidationError` 而不是被丢掉。
分时与逐笔都属"盘中才有意义"的 Web 源，上游反爬时抛 `AntiSpiderBlocked`（`E7010`）或
`WebSourceError`（`E7000`），不会伪装成空数组。
