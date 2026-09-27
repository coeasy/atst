# atst 能力扩展计划 v3（对标前沿 TDX/行情开源项目）

> 版本：v3 · 2026-09-03
> 上一版：v2（N/P 批次已全部收口，仅 P5 真机定标受阻、J1 mkdocs 常青）
> 目标：继续对齐 GitHub 前沿 TDX 生态（pytdx / mootdx / easyquotation / tdxpy / efinance /
> akshare / OpenStockData）与 2026 新兴工具（QuantDash batch / a-stock-data 七层架构），
> **补全全部功能缺口**。

## 1. 差距分析结论（v3 立项依据）

### 1.1 已对齐（无需动作）

| 竞品能力                                                    | atst 对应                                            | 状态 |
| ------------------------------------------------------- | --------------------------------------------------- | -- |
| pytdx 标准行情 14 项（行情/K线/分时/历史分时/分笔/证券列表/财务/除权/F10）        | `facade/api.py` 60+ 门面方法                            | ✅  |
| pytdx 扩展行情 7727（期货/外盘/港股/指数/期权）                         | `ex_bars`/`ex_quotes`/`goods_bars`/`goods_quotes`   | ✅  |
| pytdx reader（.day/.lc1/.lc5/.fzline/.gpcw/板块 .dat/.blk） | `reader/formats.py`（含 `BlockReader`）                | ✅  |
| easyquotation 四入口（v2 P2 已验证）                            | `all_market`/`bars`/`index_list`/`quotes`           | ✅  |
| mootdx 在线+本地双模式 / 主站池                                   | 多源降级 + verified 主站前置                                | ✅  |
| akshare 特色：龙虎榜/涨停/资金流/板块/新闻/公告/研报/股东/解禁/IPO/大宗/财务       | `longhu`/`limit_pool`/`fund_flow`/`boards`/`news`/… | ✅  |
| 北交所 / 港美 K 线（日/周/月/分钟）                                  | `bj` 市场归一化 + `klines` 三通路                           | ✅  |
| 交易日历 / 板块成分（0x1210）/ 竞价 / 量价分布 / 涨停梯 / 市场宽度 / 北向 / 问财   | `calendar.py` + 各门面方法                               | ✅  |
| 可转债 / 分级基金 / ETF 实时（集思录）                                | `jsl` 源                                             | ✅  |

### 1.2 确认缺口（v3 目标）

| #   | 缺口                                                                    | 对标项目                              | 批次   |
| --- | --------------------------------------------------------------------- | --------------------------------- | ---- |
| G1  | **基金净值历史 / 实时估值 / 基金列表**——无 `nav` 任何实现                                | akshare `fund_em` / OpenStockData | P0-1 |
| G2  | **指数成分股列表**（沪深300/上证50/中证500 等）——只有板块成分 0x1210                        | akshare `index_stock_cons`        | P0-2 |
| G3  | **宏观经济数据**（CPI/PPI/PMI/社融/M2/LPR/国债收益率）                               | akshare `macro_china_*`           | P0-3 |
| G4  | **期权/期货衍生指标**（基差/持仓量变化/跨期价差）——有 ex 原始行情，无指标层                          | akshare futures 衍生                | P0-4 |
| G5  | **百度财经数据源**——用户提供页面 `finance.baidu.com/stock/ab-301086`，接口已实测可用（见 §2） | a-stock-data `baidu_*`            | B0   |
| G6  | **批量并发 K 线**——`quotes_concurrent` 只有行情，`bars` 无 batch                 | QuantDash `klines.batch`          | P1-1 |
| G7  | **运行时自动测速选主站**（bestip）——静态 verified 标记，无运行时测速                         | mootdx `bestip=True`              | P1-2 |
| G8  | **本地增量同步/落盘写回**（日线增量写 .day、断点续传）——只读无写                                | 自建数据仓库惯例                          | P1-3 |
| G9  | **TDX 交易接口**（模拟下单）——纯行情库                                              | pytdx `pytdx.trade`（实验性）          | P2-1 |
| G10 | **雪球 / 同花顺数据源**——未接入（用户技术栈曾用）                                         | akshare `stock_*_xq` / 10jqka     | P2-2 |
| G11 | **舆情热度/关联个股**、**WS 逐笔推送**                                             | akshare 舆情 / a-stock-data 新闻层     | P2-3 |

### 1.3 遗留（v2 转入）

- **P5**：0x0FC5 真机定标（受阻于合规非交易时段采集，v1 I1 前置项）——维持 15B 守卫红线。

- **J1**：mkdocs 文档站（v1 carryover，常青）。

## 2. 百度财经源接入（B0）——接口实测结论

用户提供页面 `https://finance.baidu.com/stock/ab-301086`（鸿富瀚 301086）。
经 2026-09-03 实测（Python urllib + UA），**可直接解析**，无需登录：

### 2.1 可用接口（已实测）

**① 日K线（含 MA 指标）**

```
GET https://finance.pae.baidu.com/selfselect/getstockquotation
    ?code=301086&ktype=1&stockType=ab&group=quotation_kline_ab&finClientType=pc
    [&all=1]  [&all=0&count=250&end_time=<unix>]   # 分页：count≤250 + end_time
```

响应 `Result[0]`：

```json
{"date": "...", "kline": {"open":"93.77","high":"98.69","low":"93.25","close":"95.60",
 "volume":"392932750.00","amount":"29103","preClose":"93.70","netChangeRatio":"+2.03",
 "turnoverratio":"6.15","increase":"+1.90","holdingAmount":"0"},
 "ma5":{"volume":"2811791","avgPrice":"90.37"}, "ma10":..., "ma20":..., "macd":...,
 "key":"301086-1-20260122"}
```

**② 当日分时 / 五档 / 逐笔 / 快照**

```
GET https://finance.pae.baidu.com/selfselect/getstockquotation
    ?code=301086&all=1&stockType=ab&group=quotation_minute_ab&finClientType=pc
```

响应 `Result`（dict）：

- `priceinfo[266]`：分时点 `{time, price, ratio, increase, volume, avgPrice, amount, totalVolume, totalAmount}`

- `buyinfos[5]` / `askinfos[5]`：`{bidprice, bidvolume}` / `{askprice, askvolume}`

- `detailinfos[200]`：逐笔 `{time, volume, price, type, bsFlag, formatTime}`

- `cur`：快照 `{time, price, ratio, increase, volume, avgPrice, amount, totalVolume, totalAmount}`

- `basicinfos`：`{exchange, code, name, stockStatus, stock_market_code}`

**③ 概念板块归属 / 热榜**（OpenStockData 有 `baidu_concept_blocks` / 百度股市通热榜，接入时逆向确认端点）

### 2.2 不可用（已确认下线）

- **资金流** `vapi/v1/fundflow` / `fundsortlist`：2026-05 起返回空（a-stock-data issue #5）→ 百度源**不提供** fund\_flow。

### 2.3 风险与约束

- 接口非官方公开文档，**随时可能改版/下线** → 走 `BaseWebSource` 下线检测 + 多源降级；

- 部分历史接口（`gushitong.baidu.com/opendata` resource\_id=5429）为 2022 逆向，**不采用**，只用已实测的 `selfselect/getstockquotation` 两接口；

- 频率礼貌抓取（复用 `RateLimiter`）。

### 2.4 接入方案

- 新源注册名 `baidu`，capabilities：`kline / minute / tick / quote`（**不含** fund\_flow）；

- 适配器 `atst/web/adapters_baidu.py`（或并入 adapters），统一转 atst 模型
  （`Bar`/`MinutePoint`/`Quote`）；日K量「股」口径与 atst 一致（volume 已为股）；

- 接入 `WebQuoteSession`（`baidu_*` 便捷方法）+ `UnifiedQuoteAPI` 路由候选源。

- **已完成（2026-09-04）**：`BaiduSource`（`fetch_kline` 分页翻页含 MA 指标 /
  `fetch_minute` / `fetch_ticks` / `fetch_quote` + `parse_*` 出口）已实现并
  注册；`WebQuoteSession.baidu_kline/baidu_minute/baidu_ticks/baidu_quote`
  便捷方法 + `UnifiedQuoteAPI.baidu_*` 门面（try/finally close）+ CLI
  `atst baidu <symbol> [--kind …]` 子命令；口径坑（K线 volume/amount
  交换、分时手→股、oriAmount 优先）由 `tests/web/test_baidu.py` 罐头锁定。

## 3. P0 数据补全

### P0-1 基金净值历史 / 实时估值 / 基金列表（G1）

- 新源 `fund`，capabilities：`fund_nav_history / fund_estimate / fund_list`；

- 东财接口（实测接入时验证）：

  - 历史净值 `https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=100`

  - 实时估值 `https://fundgz.1234567.com.cn/js/{code}.js?rt=...`（jsonp）

  - 基金列表 `https://fund.eastmoney.com/js/fundcode_search.js`（全量 js 数组）

- 门面：`UnifiedQuoteAPI.fund_nav_history(code)` / `fund_estimate(code)` / `fund_list()`；

- 校验：`tests/web/test_baidu_fund_*.py` + CLI `fund` 子命令。

- **已完成（2026-09-04）**：`FundSource`（`fetch_nav_history` 空净值→None /
  `fetch_estimate` jsonp / `fetch_fund_list` JS 数组，约 1.2 万条）已实现并注册
  （`fund` 数据型源，不参与行情降级）；`WebQuoteSession.fund_*` 便捷方法 +
  `UnifiedQuoteAPI.fund_*` 门面 + CLI `atst fund nav|estimate|list`；三方一致性
  门禁补齐（`_ADAPTERS` + `FundNormalizer` + `CAPABILITY_FACADE`）；`tests/web/
  test_fund.py` 9 例 + `tests/facade/test_fund_api.py` 5 例 + CLI 6 例，全量 1976 通过。

### P0-2 指数成分股列表（G2）

- 东财数据中心报表 `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_INDEX_TS_COMPONENT`
  （接入时逆向确认 `TYPE` 指数族过滤；备选：中证指数官网 / 雪球）；

- 门面：`UnifiedQuoteAPI.index_constituents(index)` → `list[dict]`（code/name/weight 若可得）；

- 校验：静态断言已知指数成分数量级（如沪深300≈300）。

- **已完成（2026-09-04）**：`EastmoneyIndexConstituentsSource`（继承
  `EastmoneyDataCenterSource`）经 `RPT_INDEX_TS_COMPONENT` 拉取，`TYPE` 指数族过滤
  （1=沪深300/2=上证50/3=中证500/4=科创50/5=中证A50/6=中证A500/7=中证1000/8=深证50/
  9=深证100/10=北证50/11=上证180/12=中证A100/13=中证2000；2026-09 与中证官网 XLS
  交叉验证 5 族 jaccard=1.0），单页 500、大指数自动分页拉全量；源注册 `index_cons`
  （capability `index_constituents`，数据型源不参与行情降级）+ `WebQuoteSession.
  index_constituents` + `UnifiedQuoteAPI.index_constituents` 门面 + CLI
  `atst index constituents <code>`；三方一致性门禁补齐（`_ADAPTERS` +
  `IndexConstituentsNormalizer` identity + `CAPABILITY_FACADE`）；`tests/web/
  test_index.py` 14 例 + `tests/facade/test_index_api.py` 2 例 + CLI 3 例。

### P0-3 宏观经济数据（G3）

- 东财数据中心 `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_ECONOMY_*`
  （CPI / PPI / PMI / 社融 / M2 / 利率）——接入时逆向 reportName；

- 门面：`UnifiedQuoteAPI.macro(indicator)` → 时间序列；

- 定位：**非 TDX 核心**，作为东财源扩展能力并入，不新增源。

### P0-4 期权/期货衍生指标（G4）

- 计算层（不新增数据源）：基于已有 `ex_bars`/`goods_quotes` 现货/期货数据；

  - `futures_basis(symbol)`：期货价 − 现货指数价；

  - `futures_oi_change(symbol)`：持仓量变化（goods 行情含持仓）；

  - `futures_spread(near, far)`：跨期价差；

- 门面新增 3 方法 + 文档 + 测试。

## 4. P1 工程能力

### P1-1 批量并发 K 线（G6）

- `UnifiedQuoteAPI.bars_batch(symbols, period="day", count=320, workers=8)`：
  复用 `quotes_concurrent` 线程池模式，并发拉多标的 K 线，返回 `dict[symbol, list[Bar]]`；

- 对标 QuantDash `klines.batch`（分块+并发+合并）；失败单标的不影响整体；

- 校验：`tests/facade/test_bars_batch.py`（fake 计数并发）。

### P1-2 运行时 bestip 自动测速（G7）

- `TdxClient` 增 `bestip()`：并行测候选主站连接耗时（复用 async 探测），排序后更新主站池
  （热更新，不重启）；`open(bestip=True)` 触发；

- 对标 mootdx `bestip=True`；保留 verified 静态前置作为 fallback；

- 校验：离线 fake 主站延迟表断言排序。

### P1-3 本地增量同步 / 落盘写回（G8）

- 新增 `atst/sink/local_day.py`：`LocalDaySink`——下载日线**增量**写入 `.day`
  （读末日期断点续传，`<H date> ... <If prev_close>` 与 reader 同构）；

- 门面：`UnifiedQuoteAPI.sync_daily(symbols, root)`；

- 校验：写入后 `DayBarReader` 回读一致 + 增量幂等（重跑不重复）。

## 5. P2 生态与交易

### P2-1 TDX 交易接口（G9）⚠️ 范围与风险

- 纯行情库现状；pytdx `pytdx.trade` 仅实验性（客户端模拟，无实盘保证）；

- **已完成**：独立可选模块 `atst/trade/`，TDX 客户端交易协议探测（登录/查询/
  委托帧**洁净室推断**），**不承诺实盘**：

  - `frames.py`：8 字节帧头（`<H total_len><H cmd><H seq><H flags|status>`）
    自洽编解码 + 登录/心跳/登出/查询/委托/撤单 body（价格以分、长度前缀 GBK 串、
    6 字节 ASCII 代码）；

  - `simulator.py`：`TradeSimulator` 纯内存模拟券商（资金/持仓/委托生命周期/
    冻结解冻）+ `SimTransport` 协议回路（模拟器 ↔ 客户端端到端）；

  - `client.py`：`TradeClient`（登录/查询/委托/撤单/心跳 + `buy`/`sell` 助手）；
    `SocketTransport` **红线**——连接即抛 `TradingUnavailable`，绝不连接真实
    券商通道；

  - `PROTOCOL_SPEC/TRADE/` 6 份 **draft** spec（命令未登记 `commands.py` 账本）；

  - 校验：`tests/trade/` 56 例（帧/口令/模拟器/客户端/红线）。

- **范围红线**（不变）：不接真实资金账户、不下单执行，仅协议层研究与模拟器；

- 若用户确认实盘需求，另行立项（券商通道合规）。

### P2-2 雪球 / 同花顺数据源（G10）

- 雪球 `xq_*`：需 `xq_a_token` cookie（登录态）——标注 `use_cookie` 源；

- 同花顺 `10jqka_*`：部分接口需 UA/鉴权头（`hexin-v`）；

- 建议：作为**可选增强源**，能力 `quote/kline`，接入多源降级候选池，带 cookie 注入 seam。

### P2-3 舆情热度 / WS 逐笔推送（G11）

- 舆情：东财热点题材 / 百度股市通热榜（B0 ③）→ `UnifiedQuoteAPI.hot_topics()`；

- WS 逐笔：基于 `detailinfos`/腾讯逐笔轮询 → `ws_server` 增 `subscribe_ticks` 订阅
  （复用 `detailinfos` 200 条缓冲，低频轮询推送）。

## 6. 批次与进度

| 批次   | 内容                | 状态                                                                                                                                                                                                                                                                                        |
| ---- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B0   | 百度财经源（G5，接口已实测）   | ✅ 已完成（`atst/web/adapters_baidu.py` `BaiduSource` kline/minute/tick/quote + 源注册 + `WebQuoteSession.baidu_*` + `UnifiedQuoteAPI.baidu_*` 门面 + CLI `atst baidu` 子命令；fund\_flow 不提供）                                                                                                        |
| P0-1 | 基金净值/估值/列表（G1）    | ✅ 已完成（`atst/web/adapters_fund.py` `FundSource` 历史净值/实时估值/基金列表 + 源注册 `fund` + `WebQuoteSession.fund_*` + `UnifiedQuoteAPI.fund_*` 门面 + CLI `atst fund` 子命令）                                                                                                                              |
| P0-2 | 指数成分股（G2）         | ✅ 已完成（`atst/web/adapters_index.py` `EastmoneyIndexConstituentsSource` 经 datacenter `RPT_INDEX_TS_COMPONENT` 拉取，`TYPE` 指数族过滤 + 大指数分页 + 中证官网交叉验证 + 源注册 `index_cons` + `WebQuoteSession.index_constituents` + `UnifiedQuoteAPI.index_constituents` 门面 + CLI `atst index constituents` 子命令） |
| P0-3 | 宏观数据（G3）          | ⬜ 待实现                                                                                                                                                                                                                                                                                     |
| P0-4 | 期货/期权衍生指标（G4）     | ⬜ 待实现                                                                                                                                                                                                                                                                                     |
| P1-1 | 批量并发 K 线（G6）      | ⬜ 待实现                                                                                                                                                                                                                                                                                     |
| P1-2 | 运行时 bestip（G7）    | ✅ 已完成（ConnectionPool.update\_hosts 热更新 + TdxClient.bestip + open(bestip=True)，同步/异步双轨 + 测试）                                                                                                                                                                                               |
| P1-3 | 本地增量落盘（G8）        | ✅ 已完成（atst/sink/local\_day.py LocalDaySink 断点续传幂等写 .day + UnifiedQuoteAPI.sync\_daily 门面 + 回读校验测试）                                                                                                                                                                                       |
| P2-1 | TDX 交易接口（G9，协议探测） | ✅ 已完成（`atst/trade/` 可选模块：帧编解码 + 内存模拟券商 + `TradeClient` + `SocketTransport` 实盘红线 + PROTOCOL\_SPEC/TRADE 6 份 draft spec + 56 例测试）                                                                                                                                                          |
| P2-2 | 雪球/同花顺源（G10）      | ⬜ 待实现                                                                                                                                                                                                                                                                                     |
| P2-3 | 舆情热度 + WS 逐笔（G11） | ⬜ 待实现                                                                                                                                                                                                                                                                                     |

## 7. 修改记录

| 日期         | 记录                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-09-03 | v3 立项：完成差距分析（1.1 已对齐 / 1.2 十一项缺口）；实测验证百度财经 `selfselect/getstockquotation` 两接口（日K+MA / 分时+五档+逐笔+快照），资金流确认下线；按用户选择建四批次路线图（P0 数据补全 / P1 工程能力 / P2 交易 / P2 生态）。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |
| 2026-09-03 | 完成 P1-2 运行时 bestip：`ConnectionPool.update_hosts`（同步/异步）热更新主站池（复用连接、丢弃移除主站）、`TdxClient.bestip()` / `AsyncTdxClient.bestip()`（并行测速 → 排序 → 热更新）、`open(bestip=True)` 触发；新增 `tests/client/test_bestip.py` 9 例。                                                                                                                                                                                                                                                                                                                                                                                                                                                             | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |
| 2026-09-03 | 完成 P1-3 本地增量落盘：`atst/sink/local_day.py` `LocalDaySink`（断点续传 + 多窗口回退 + 幂等 + prev\_close 链 + 期货持仓量末字段；编码为 `DayBarReader` 解码精确逆变换）+ `UnifiedQuoteAPI.sync_daily` 批量门面 + 模块级 `sync_daily`；新增 `tests/sink/test_local_day.py` 10 例。补登记百度源 identity normalizer（`BaiduNormalizer`，修复 web 三方一致性门禁）。                                                                                                                                                                                                                                                                                                                                                                           | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |
| 2026-09-03 | 完成 P2-1 交易协议探测（用户确认"先优化通达信相关接口，百度财经后续再加入"，范围仅 bestip/本地落盘/交易协议探测三项）：新增 `atst/trade/` 可选模块——`constants.py`/`errors.py`/`security.py`/`frames.py`（8 字节帧头自洽编解码 + 登录/心跳/登出/查询/委托/撤单 body）+ `simulator.py`（`TradeSimulator` 纯内存模拟券商 + `SimTransport` 协议回路）+ `client.py`（`TradeClient` + `SocketTransport` 实盘红线）；`PROTOCOL_SPEC/TRADE/` 6 份 draft spec + README 补交易族说明；`tests/trade/` 56 例全绿，ruff/mypy 通过。                                                                                                                                                                                                                                                                 | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |
| 2026-09-04 | 完成 B0 百度财经源收口：`UnifiedQuoteAPI` 新增 `baidu_kline` / `baidu_minute` / `baidu_ticks` / `baidu_quote` 门面（转发 `WebQuoteSession` 便捷方法，try/finally 保证 close）；CLI 新增 \`atst baidu <symbol> \[--kind kline                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | minute   | ticks                                                                                                                                                                                          | quote] ` 子命令（`--period/--count/--end-time`/`--limit`/`--json`）；新增 ` tests/facade/test\_baidu\_api.py`6 例 +`tests/unit/test\_cli\_semantics.py` ` TestB0BaiduSubcommand\` 6 例；全量 1953 例通过。 |
| 2026-09-04 | 完成 P0-1 基金净值（G1）：新增 `atst/web/adapters_fund.py` `FundSource` 数据型源（`fetch_nav_history` 历史净值 / `fetch_estimate` fundgz jsonp / `fetch_fund_list` 全量 JS 数组）+ 源注册 `fund`（capabilities fund\_nav\_history/fund\_estimate/fund\_list，不参与行情降级）+ `WebQuoteSession.fund_*` 便捷方法 + `UnifiedQuoteAPI.fund_*` 门面 + CLI \`atst fund nav                                                                                                                                                                                                                                                                                                                                          | estimate | list ` 子命令；补齐三方一致性门禁（`\_ADAPTERS`/`FundNormalizer`/`CAPABILITY\_FACADE`）；新增 ` tests/web/test\_fund.py`9 例 +`tests/facade/test\_fund\_api.py`5 例 + CLI`TestP01FundSubcommand\` 6 例；全量 1976 例通过。 | <br />                                                                                                                                                                                    |
| 2026-09-04 | 完成 P0-2 指数成分股（G2）：新增 `atst/web/adapters_index.py` `EastmoneyIndexConstituentsSource`（继承 `EastmoneyDataCenterSource`，经 datacenter `RPT_INDEX_TS_COMPONENT` 拉取）——`TYPE` 指数族过滤（13 族映射，2026-09 与中证官网 XLS 交叉验证 5 族 jaccard=1.0）、单页 500、中证1000/中证2000 等大指数自动分页拉全量、weight 仅部分指数族提供；源注册 `index_cons`（capability `index_constituents`，数据型源不参与行情降级）+ `WebQuoteSession.index_constituents` + `UnifiedQuoteAPI.index_constituents` 门面 + CLI `atst index constituents <code>` 子命令（`--json`）；补齐三方一致性门禁（`_ADAPTERS`/`IndexConstituentsNormalizer`/`CAPABILITY_FACADE`）；新增 `tests/web/test_index.py` 14 例 + `tests/facade/test_index_api.py` 2 例 + CLI 3 例 + 一致性门禁 1 例，全量 1996 例通过。 | <br />   | <br />                                                                                                                                                                                         | <br />                                                                                                                                                                                    |

