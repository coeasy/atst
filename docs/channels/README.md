# tstdx 数据源 / Channel 接口目录

> Status: v12 target contract.  
> Source of truth target: `SourceRegistry + ChannelRegistry + CapabilityRegistry`.  
> 任何表格与 API 示例在落地阶段必须由 registry/conformance test 双向校验。

## 1. 命名规则

`tstdx` 从 v12 开始严格区分：

- **Source**：真实数据提供方，如 `tdx`、`tencent`、`sina`、`eastmoney`。
- **Channel**：Source 内部的数据通路，如 `quotation`、`kline`、`fund_flow`。
- **Market**：市场范围，如 `cn_a`、`hk`、`us`、`fund`、`fx`。
- **Endpoint/Host**：Source/Channel 内部的物理端点。

生产 Query 只能绑定一个 Source；同一 Source 内允许按明确规则做 endpoint failover，禁止跨 Source 静默替代。

## 2. Source 总览

| Source | 角色 | 主要 Channel | 典型特有数据 | 默认跨源替代 |
|---|---|---|---|---|
| `tdx` | primary live | quotation / extended / goods / f10 / mac | TDX 二进制行情、扩展市场、商品、F10 | 禁止 |
| `tencent` | auxiliary live | quote / kline / minute / ticks / global / market_stat | 港美行情、逐笔、全球/大盘统计 | 禁止 |
| `sina` | auxiliary live/info | quote / history_kline / suggest / boards / fund_flow / news | 联想、板块、新闻、资金流 | 禁止 |
| `eastmoney` | auxiliary live/info | quote / kline / trends / rank / fund_flow / limit_pool / northbound / corporate / longhu / hot_rank / margin / index_constituents / fund | 资金流、涨跌停池、异动、人气、两融、龙虎榜、公司资料 | 禁止 |
| `baidu` | auxiliary live | quote / kline / minute / ticks | 百度财经行情通道 | 禁止 |
| `jsl` | auxiliary info | bond / etf | 可转债、ETF/基金特色数据 | 禁止 |
| `boc` | auxiliary info | fx | 外汇牌价 | 禁止 |
| `iwencai` | auxiliary info | screening | 自然语言选股 | 禁止 |
| `vipdoc` | local historical | day / minute | 本地历史 day/lc1/lc5 | 不参与实时替代 |

Golden Replay / Synthetic 仅属于测试运行时，不是生产 Source。

## 3. 统一 API

统一 API 只覆盖真正具有共同语义的 capability：

```python
md.quotes(["sh600519"], source="tdx")
md.quotes(["sh600519"], source="tencent")
md.bars("sh600519", period="day", source="tdx")
md.bars("sh600519", period="day", source="eastmoney")
```

`source=None` 由 CapabilityRegistry 的确定性 `default_source` 选择一次，不做 fallback。

## 4. Direct API

每个 Source 必须提供独立 namespace：

```python
md.tdx.*
md.tencent.*
md.sina.*
md.eastmoney.*
md.baidu.*
md.jsl.*
md.boc.*
md.iwencai.*
md.vipdoc.*
```

Direct API 保留 Provider-specific data，不强行压平到通用 Quote/Bar。

## 5. TDX Channel

| Channel | 协议/范围 | 主要能力 | Direct API 目标 |
|---|---|---|---|
| `quotation` | 7709 | quotes / bars / minute / trades / security metadata / finance / capital changes | `md.tdx.quotation.*` |
| `extended` | 7727 | 扩展市场列表/标的/行情/K线 | `md.tdx.extended.*` |
| `goods` | GOODS | 商品行情/K线 | `md.tdx.goods.*` |
| `f10` | F10 | F10 目录、资料下载 | `md.tdx.f10.*` |
| `mac` | MAC | MAC 协议族能力 | `md.tdx.mac.*` |

同一 Channel 内可切 TDX host；不同 Channel 只有 Registry 明确声明等价时才可内部切换；永不跨到 Web Provider。

## 6. Tencent Channel

| Channel | Market | 能力 |
|---|---|---|
| `quote` | cn_a / hk / us | 实时行情 |
| `kline` | cn_a 等明确支持市场 | 日/历史 K线 |
| `minute_kline` | 当前主要 cn_a | 1/5/15/30/60 分钟 K线 |
| `minute` | cn_a | 当日分时 |
| `ticks` | cn_a | 逐笔 |
| `global` | global | 外盘/全球行情 |
| `market_stat` | cn_a | 大盘统计 |
| `board_rank` | cn_a | 板块排行 |

旧 `hk/us/kline/minute/ticks` 顶层 source ID 逐步迁移为 `source=tencent + channel/market`。

## 7. Sina Channel

| Channel | Market | 能力 |
|---|---|---|
| `quote` | cn_a / hk | 实时行情 |
| `history_kline` | cn_a | 历史 K线 |
| `suggest` | 多市场 | 证券联想 |
| `industry_board` | cn_a | 行业板块 |
| `board_list` | cn_a | 板块列表 |
| `board_member` | cn_a | 板块成员 |
| `fund_flow` | cn_a | 资金流 |
| `news` | cn_a 为主 | 个股新闻 |

## 8. Eastmoney Channel

| Channel | 能力 |
|---|---|
| `quote` | 实时行情 |
| `kline` | 历史 K线 |
| `trends` | 当日趋势/分时 |
| `rank` | 通用排行 |
| `fund_flow` | 个股/板块资金流 |
| `limit_pool` | 涨停/跌停/炸板池 |
| `stock_changes` | 盘中异动 |
| `northbound` | 沪深港通资金 |
| `hot_rank` | 股吧人气榜 |
| `corporate` | 基本面/公告/股东/大宗/解禁/业绩等 |
| `longhu` | 龙虎榜 |
| `margin` | 融资融券 |
| `index_constituents` | 指数成分 |
| `fund` | 基金历史净值/估值/列表 |

## 9. Auxiliary Source

### Baidu

```text
quote / kline / minute / ticks
```

### JSL

```text
bond / etf
```

### BOC

```text
fx
```

### iWencai

```text
screening
```

### Vipdoc

```text
day / minute historical files
```

## 10. 每个 Source 文档的强制模板

每份 `docs/channels/<source>.md` 必须包含：

1. Source 定位与角色；
2. 支持 Market；
3. Channel 列表；
4. capability；
5. Unified API；
6. Direct API；
7. Provider-specific model；
8. 输入/输出 schema；
9. 单位归一；
10. freshness evidence；
11. auth/referer/cookie；
12. rate/batch limit；
13. endpoint failover 规则；
14. 错误码；
15. production/experimental/degraded 状态；
16. tests/fixtures；
17. benchmark case；
18. registry entry。

## 11. CI 文档门禁

```text
source registry -> source doc exists
channel registry -> channel documented
direct api -> registry mapping exists
registry direct api -> callable exists
capability mapping -> conformance exists
freshness profile -> documented
source status -> docs status matches
```

该目录最终应由 registry 自动生成矩阵、人工维护说明，避免代码与文档长期漂移。
