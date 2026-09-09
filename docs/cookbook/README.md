# Cookbook 实战食谱（v1.0.0）

> 本目录随 v1.0.0 稳定版发布。下方早期 v0.4.0/v0.7.0 标记保留为食谱演进历史。

## 核心食谱（v0.4.0）

| # | 食谱 | 场景 |
|---|---|---|
| 01 | [批量拉取全市场 K 线](01_bulk_kline.md) | 建本地数据库 |
| 02 | [实时行情监控 + 降级](02_realtime_fallback.md) | 盘中监控 |
| 03 | [本地 vipdoc 离线解析](03_offline_vipdoc.md) | 无网环境 |
| 04 | [流式订阅与断线恢复](04_streaming.md) | 准实时推送 |
| 05 | [数据落地三件套](05_sinks.md) | DataFrame/Parquet/DuckDB |
| 06 | [自定义协议命令](06_custom_command.md) | 扩展未知命令 |

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

## 分时 / 分钟 K 线 API 选型（N6 澄清）

门面（`UnifiedQuoteAPI`）三个易混入口，按需选择：

| 入口 | 返回 | 通路 | 适用 |
|---|---|---|---|
| `minute(symbol)` | 当日 1 分钟分时快照 | 0x0537 双路（tdx 失败自动 HTTP） | 盘中盯当日分时 |
| `minute_web(symbol)` | 当日分时（同源） | 强制 HTTP（腾讯/东财），不经 TDX | TDX 不可用 / 强制 web |
| `minute_klines(symbol, period, count)` | 跨日分钟 **K 线**（Bar 序列） | HTTP：A 股腾讯 mkline / 港美东财 push2his | 历史多日分钟 K 线 |

一句话：**要 K 线用 `minute_klines`，要当日分时用 `minute`，要强制 HTTP 分时用 `minute_web`。**
