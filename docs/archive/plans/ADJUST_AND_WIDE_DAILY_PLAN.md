# 复权与宽表日线：数据获取现状、缺口清单与补齐方案

> **文档性质**：实现方案（plan），基于 **2026-10-07 实机探测**的代码与接口证据，不是二手推断。
> 对标基准 atst v1.4.3；对标参照 free-stockdb（日线 21 字段宽表 + `cum` 复权因子）。
> 落在 `docs/archive/plans/`（既有方案文档同目录），不进 `active_docs()` 射程；
> 现行契约口径以 [interfaces.md](../../api/interfaces.md) 为准。

---

## 落地状态（2026-10-08 更新）

| 项 | 状态 | 说明 |
|----|------|------|
| **B1** 事件分页拉全 | ✅ 已落地 | `_adjusted_events` 内 `page_all()`：显式 `size=100`，翻页到尽；触顶发 `WEB_EASTMONEY_PAGE_LIMIT` |
| **B2** `prev_close` 注入 | ✅ 已落地（改在引擎侧） | `adjust._prev_close_before()` 按**事件日**二分定位；`event_before_window` 把两种成因分开报 |
| **B3** 配股事件源 | ✅ 已落地 | 真报表名 **`RPT_IPO_ALLOTMENT`**（非猜测）；`capital_changes_from_dividends_and_rights()` 按除权日合成 |
| **B4** 因子落盘复用 | ⬜ 未做 | 仍为每次全量重算；**唯一遗留项** |
| **B5** `0x000F` golden | ⬜ 未做 | `reject_implausible_changes()` 闸已在位；2026-10-08 复测 `sh600519` **250/250 条记录字段全部域外** |
| **C1–C3 / C5** 宽表 `daily_enriched` | ✅ 已落地 | 新模块 `atst/domain/enrich.py` + composed 能力；24 列；`count>500` 显式拒绝 |
| **C4** `is_st` 历史 | ⬜ 未做 | 目前输出名称**前缀**启发式并标 `is_st_source="name_heuristic"`；无简称时 `is_st=null` |

**方案之外新查实并已修复的根因（本文件 §2 未覆盖）**：

1. **hfq / 定点复权的因子随请求的 `count` 漂移**（2026-10-08 第一轮）——窗口盖不住早期事件时
   那批事件拿不到前收盘价，只能走 `1/(1+S+R)` 降级口径（现金红利被丢），实测 600519 hfq 因子
   1.045（`count=5`）与 1.642（`count=2000`）并存。已用 `_extend_window_to_events()` 倍数延伸取数
   窗口关闭。修后 600519/601398/000001/600601 在 `count=5/60/2000` 下因子完全一致。
2. **`event_source="tdx"` 整条路断链**（第二轮）——`_adjusted_events` 对已是 `CapitalChange` 的
   返回值再套 `to_capital_changes()`（吃 dict），AttributeError 被兜成 `E9000 InternalError`。
   调用方读到的是一句内部黑话，而不是"这条路不通，请用 eastmoney"。已改为按类型分派。
3. **宽表的 `turnover` / `vol_ratio` 曾用复权后的 volume 当分子**（第二轮）——分子乘了成交量因子、
   分母（流通股本）没乘，比值无意义。已让 `AdjustEngine.apply` 把复权前的量留在
   `extra["raw_volume"]`，宽表读它。
4. **`is_st` 在拿不到简称时返回 `false`**（第二轮）——"不知道"被当成"不是 ST"。已改为 `null`，
   并把启发式从"名称含 ST"收紧为**前缀**匹配（`ST` / `*ST` / `SST` / `S*ST`）。
5. **组合能力上的 `provider` 是条谁都用不上的参数**（第二轮）——`Client.call` 把它当路由字段递进
   planner，于是 `Client.adjusted_bars(..., provider="tdx")` 在规划期就 E1010，而执行体里读
   `kwargs["provider"]` 的那一行永远读不到东西。已让 `Client.call` 把它归位进 `options.kwargs`
   （`COMPOSED_CAPABILITIES` 判定）。
6. **`capital_changes_from_dividends` 与新合并且函数是两份实现**（第二轮）——前者不按日归并，
   同日多行会各算一次。已改为薄壳委托，消除分叉。
7. **`EastmoneyMarginSource.parse_rows` 接了 `allow_empty` 却不用**（第二轮）——空结果的语义在该源上
   静默失效。已共用 `is_empty_result()` 单点判据。
8. **`_extend_window_to_events` 静默吞异常**（第二轮）——延伸失败会悄悄退回原窗口，因子漂移无人知。
   已发 `adjust_window_extend_failed` 告警。
9. **CLI 完全没有宽表入口**（第三轮）——`adjusted-bars` 也拿不到 `--start` / `--provider` /
   `--event-source` / `--anchor-date`（定点复权在 CLI 上不可用）。已补 `atst daily-enriched` 与四个旗标。

**验收证据（不看引擎自身算术）**：raw 与 hfq 的日收益率在**非除权日逐根完全一致**（0 处不符）；
每次事件反推的每股分红与公告精确对上（如 2024-06-19 = 308.76 元/10 股、
2011 年 `10送1派23` 的 `1/k = 1.1/(1-2.3/212.63) = 1.112029`，观测值 1.112029）。

---

## 0. 结论速览

| 问题 | 结论 |
|------|------|
| **复权数据怎么拿** | **已经能拿，且默认路径就是对的**：`Client.call("adjusted_bars", sym, method="qfq"\|"hfq"\|"fixed"\|"none")`，`event_source` **默认 `"eastmoney"`** → `dividend_history`（东财 `RPT_SHAREBONUS_DET`，真机列名已对拍）+ `rights_issue`（`RPT_IPO_ALLOTMENT`）→ `capital_changes_from_dividends_and_rights()` → `AdjustEngine`。实测 `sh600519` 后复权价格因子 **7.39106838**、前复权归一到最新 = **1.0**，结果正确。（本行原文写的 **1.331 是错的值**——那是窗口漂移把 25 年现金红利丢光后的读数，见上方落地状态第 1 条。） |
| **但复权有 4 个实测缺陷**（B1–B3 已于 2026-10-08 修复，见上方落地状态；B4 未做） | ① **事件分页截断**（默认只取 20 条，600519 实有 28 条，末条只到 2009-07-01）；② **`prev_close` 缺失**导致现金红利被忽略（告警已复现）；③ **配股恒 0**；④ **因子不落盘**，每次全量重算。 |
| **宽表日线怎么拿** | **字段基本齐了，缺的是"拼装"**：`valuation_history`（东财 `RPT_VALUEANALYSIS_DET`，**日频**）实测已返回 `PE_TTM` `PB_MRQ` `TOTAL_SHARES` `FREE_SHARES_A` `TOTAL_MARKET_CAP` `NOTLIMITED_MARKETCAP_A` `CLOSE_PRICE` `CHANGE_RATE` `TRADE_DATE`，叠加 `bars` 的 OHLCV/amount，21 字段里 **17 个可得**，另外 3 个可自算（`pct_chg` `amplitude` `turnover`）。 |
| **真正拿不到的** | `is_st` 历史快照、`name` 历史（更名）、配股事件、指数/成分股历史截面。前两个**无免费源**（`st_list` 自己就写着"最新快照，无历史基准日"）。 |
| **能否补齐** | 复权 4 条缺陷 **全部可补**（工程问题，无需新源）；宽表 **可补齐到 20/21 字段**，仅 `is_st` 历史需外挂源或用带标注的启发式；其余缺失见 §4 分级表。 |

---

## 1. 复权：现状链路（代码级）

```
Client.call("adjusted_bars", symbol, method=..., period=..., count=..., start=...)
  或 CLI: atst adjusted-bars
        │
        ▼ DirectProviderExecutor._composed_call        atst/runtime/executor.py:698
        ├─ _adjusted_raw_bars(symbol, raw_provider="tdx")     → 原始 K 线
        └─ _adjusted_events(symbol, event_source, ...)        atst/runtime/executor.py:657
              ├─ event_source="eastmoney"（默认）
              │     → QuerySpec.build("dividend_history", provider="eastmoney")
              │     → RPT_SHAREBONUS_DET
              │     → capital_changes_from_dividends()   atst/domain/finance.py:167
              │         · 只取有 ex_dividend_date 的行（用 report_date 会提前一整个季度）
              │         · bonus_ratio = BONUS_RATIO(送股) + IT_RATIO(转增)
              │         · dividend    = PRETAX_BONUS_RMB（每 10 股）
              │         · rights_*    = 0（该表无配股列）
              └─ event_source="tdx"（须显式点名）
                    → 0x000F → to_capital_changes() → reject_implausible_changes()
                      （布局未 golden 锁定，错位记录就地判死，不许进引擎）
        ▼ AdjustEngine.apply(bars, events, method, anchor_date)   atst/domain/adjust.py
              · 除权参考价 P_ref = (P - D + Pr·R) / (1 + S + R)
              · 价格因子按 1/k 累乘；成交量因子按 1/(1+S+R) 累乘
              · 仅 category=1（除权除息）参与价格因子
```

**实测证据（2026-10-07）**

```
raw   2026-09-30 close=1258.62 volume=3833098
qfq   2026-09-30 close=1258.62  adj_price_factor=1.0   adj_volume_factor=1.0     ← 归一到最新，正确
hfq   2026-09-30 close=1675.22  adj_price_factor=1.331 adj_volume_factor=0.7513  ← 后复权，正确

UserWarning: [adjust_prev_close_missing] 复权事件 2009-07-01 缺少前收盘价
             （bar.extra['prev_close']），每股现金红利 1.1560 元被忽略：
             价格因子按 1/(1+S+R) 近似。
dividend_history(sh600519)  size=50 → 28 条；默认(size=20) → 20 条，末条 ex_date 2009-07-01
```

---

## 2. 复权：4 个缺口与补齐设计

| ID | 缺口 | 严重度 | 证据 | 补齐方案 |
|----|------|--------|------|----------|
| **B1** | 事件分页截断：executor 调 `dividend_history` 不传 `size`，落默认 `size=20`，长历史标的早期除权事件丢失 | **高**（静默错：不报错，因子就是错的） | 600519 实有 28 条，默认只到 2009-07-01 | `_adjusted_events()` 显式传 `size`（如 200）并**翻页到尽**；加单测钉住"事件条数 ≥ 全历史条数"（用罐头响应造 30 条样本的 fixture） |
| **B2** | `prev_close` 缺失：TDX 原始 bar 不带 `extra["prev_close"]`，**每股现金红利被完全忽略**，价格因子退化成只还原股本扩张 | **高**（告警已复现） | `adjust_prev_close_missing`，1.1560 元/股被丢 | `_adjusted_raw_bars()` **多取一根**（`count+1`），把前一根 `close` 注入 `bar.extra["prev_close"]`；首根沿用既有 `open` 兜底并写进文档。**改完该告警应消失**——把它做成断言：样本股复权后告警数为 0 |
| **B3** | 配股恒 0：`RPT_SHAREBONUS_DET` 无配股列，`rights_ratio`/`rights_price` 置 0，配股标的除权价算错 | 中（配股近年少，历史标的有） | `capital_changes_from_dividends` 注释明写 | 新增配股事件源：**报表名以抓包/akshare 实测为准，禁止按命名惯例猜**（历史教训：猜的 14 个报表名 13 个返回 9501）。抓不到就保留 0 + 结果侧告警，**不许静默** |
| **B4** | 因子不落盘：每次 `adjusted_bars` 都重新拉分红 + 全量重算；全市场 5000 只要 5000 次请求 | 中（性能/配额） | 链路无缓存（且架构契约禁止隐式缓存） | 落 `data/lake/adjust/<symbol>.parquet`（`date`/`cum`/`price_factor`/`vol_factor`/`event_type`/`source`/`verified`）；命中本地表时不回源（**显式策略**，不进默认路径） |

**B5（次要）**：`0x000F` golden 锁定降级为"多一条独立源做交叉校验"，不再是可用性前置——错位记录已由 `reject_implausible_changes()` 挡住，错位的复权比不复权更危险这一判断仍然成立。

**验收**

* 30 只样本（含高送转、长期停牌、历史配股、ST）与独立源（本地 gbbq / 新浪除权页）逐日比对，qfq/hfq 相对误差 ≤ 1e-4；
* 长历史标的（600519/000001/600601）事件条数与东财报表总数一致（B1 断言）；
* 样本股复权后 `ADJUST_PREV_CLOSE_MISSING` 告警数 = 0（B2 断言）。

---

## 3. 宽表日线：现状与拼装方案

### 3.1 实测可得字段（东财 `RPT_VALUEANALYSIS_DET`，`valuation_history`，日频）

`Client.call("valuation_history", symbol, count=N)`，`count` 范围 **1..500**（超长历史需翻页）。

实测返回列（2026-10-07，`sh600519`）：

`TRADE_DATE` `SECURITY_CODE` `SECURITY_NAME_ABBR` `CLOSE_PRICE` `CHANGE_RATE`
`TOTAL_SHARES` `FREE_SHARES_A` `TOTAL_MARKET_CAP` `NOTLIMITED_MARKETCAP_A`
`PE_TTM` `PE_LAR` `PB_MRQ` `PS_TTM` `PCF_OCF_TTM` `PCF_OCF_LAR` `PEG_CAR` `BOARD_NAME`

补充源：

| 源 | 给什么 | 粒度 |
|----|--------|------|
| `bars`（tdx/腾讯/东财/百度） | `open/high/low/close/volume/amount` | 日频（及分钟） |
| `stock_base_info`（`EastmoneyProfileSource`） | `total_shares` `float_shares` `total_market_cap` `float_market_cap` `pe_dynamic` `pb` `roe` `high_52w` `low_52w` `industry` | **当前快照** |
| `baidu_valuation_history` | 总市值 / PE(TTM) / PE(静) / PB / 市现率 | 日频（近一年等周期） |
| `index_valuation`（`RPT_INDEX_VALUATION`） | 指数 PE/PB/股息率 | 快照 |
| `st_list`（BK0511） | ST 名单（**最新快照，无历史基准日**） | 快照 |

### 3.2 21 字段对齐表（free-stockdb 日线 → atst 获取方式）

| free-stockdb 字段 | atst 获取 | 状态 |
|-------------------|-----------|------|
| `date` `code` | 日线主键 | ✅ |
| `open` `high` `low` `close` `volume` `amount` | `bars` | ✅ |
| `pre_close` | 前一根 `close`（或 `CLOSE_PRICE` 前移一日） | ✅ 可自算 |
| `pct_chg` | `CHANGE_RATE` 或 `(close-pre_close)/pre_close` | ✅ 可自算 |
| `amplitude` | `(high-low)/pre_close` | ✅ 可自算 |
| `pe_ttm` | `PE_TTM` | ✅ 日频 |
| `pb` | `PB_MRQ` | ✅ 日频 |
| `total_mv` | `TOTAL_MARKET_CAP` | ✅ 日频 |
| `float_mv` | `NOTLIMITED_MARKETCAP_A`（无限售 A 股市值） | ✅ 日频 |
| `total_share` | `TOTAL_SHARES` | ✅ 日频 |
| `float_share` | `FREE_SHARES_A` | ✅ 日频 |
| `turnover`（换手率） | **`volume / FREE_SHARES_A`**（volume 单位=股，口径自洽） | ✅ **可自算** |
| `vol_ratio`（量比） | 日频近似：`volume_t / mean(volume_{t-5..t-1})`；真实盘中量比需分钟数据 | 🟡 口径须标注 |
| `name` | `SECURITY_NAME_ABBR` / `profile` | 🟡 **仅当前名**，历史更名无源 |
| `is_st` | `st_list`（BK0511 快照） | ❌ **无历史** |

**结论：21 字段里 13 个直接可得、4 个可自算、1 个口径需标注、2 个（`name` 历史、`is_st` 历史）需要新源。**

### 3.3 补齐设计

| ID | 内容 |
|----|------|
| **C1** | 新增 `derived` 能力 `daily_enriched(symbol, start, end, fields)`：以本地日线为主干，按 `TRADE_DATE` **as-of 左连接**估值序列表（防前视：估值行不得早于其可得时点），产出 20 字段宽表；`_src_*` provenance 列保留来源。**能力双登记**（`WebQuoteSession` 静态方法 + `providers/__init__.py` 显式声明），否则 `Client()` 构造即 `CapabilityAuditError`。 |
| **C2** | 派生字段规则写成可测常量：`pre_close`（前一根 close；首根用 open）、`pct_chg`、`amplitude`、`turnover = volume / float_share`；`vol_ratio` **显式标注为日频近似口径**，不冒充盘中量比。 |
| **C3** | 长历史翻页：`valuation_history` 单页上限 500，`daily_enriched` 内部按 `count` 翻页并在 `fields` 里声明覆盖区间。 |
| **C4** | `is_st` 历史：优先补源（交易所风险警示变更公告 / cninfo 公告解析，需实测）；无源时提供**带标注的启发式**（名称含 ST/*ST 前缀 + 涨跌幅限制 5% 双重判据），并在输出里显式标 `is_st_source="heuristic"`，**绝不静默当真值**。 |
| **C5** | 落盘 `data/lake/enriched/<symbol>.parquet`（沿用 Phase A 的分区与 manifest 校验），使宽表可批量回放。 |

**验收**：随机 50 标的 × 近 3 年，最新交易日宽表估值字段与实时接口一致率 ≥ 99%；历史行无未来数据（as-of 单测）；`turnover` 与东财/新浪当日换手率对拍误差 ≤ 1%。

---

## 4. 数据获取层：还有哪些接口缺失（分级清单）

| # | 缺失 | 级别 | 能否补齐 | 路径 |
|---|------|------|----------|------|
| 1 | 配股事件（rights_ratio / rights_price） | **P0** | 可（需新源，须实测报表名） | 东财配股报表抓包 / akshare 源码 grep |
| 2 | 复权事件分页拉全 | **P0** | 可（纯工程） | B1 |
| 3 | bar `prev_close` 注入 | **P0** | 可（纯工程） | B2 |
| 4 | 复权因子落盘与复用 | P1 | 可 | B4 |
| 5 | 宽表日线（`daily_enriched`） | P1 | 可（拼装 + 4 个派生公式） | C1–C3 |
| 6 | `turnover` 历史换手率（无直接接口） | P1 | **可自算**（volume / float_share） | C2 |
| 7 | `vol_ratio` 历史量比（无源） | P2 | 可近似（日频），真实盘中量比需分钟数据 | C2（口径标注） |
| 8 | `is_st` 历史快照 | P2 | 部分（需外挂源；否则启发式 + 显式标注） | C4 |
| 9 | 证券名称历史（更名/ST 摘帽） | P2 | 难（免费源稀缺） | 交易所公告 / cninfo，需实测 |
| 10 | 指数 / ETF 的估值日频序列 | P2 | 可（`index_valuation` 现为快照，需找日频报表） | 实测 `RPT_` 报表 |
| 11 | 指数成分股**历史**截面（现仅当前） | P2 | 难（免费源稀缺，多为季度快照） | 中证/国证官方文件 |
| 12 | 停牌历史、退市历史 | P2 | 部分（可从日线缺口 + 公告推断） | 本地湖缺口检测 |
| 13 | 分钟 / tick 落盘与批量回放 | **P0**（回测刚需） | 可（体量大，须先有限流与分区） | 见 parity 文档 Phase A2 |
| 14 | 全市场横截面日频查询面 | **P0** | 可（DuckDB/Arrow 谓词下推） | 见 parity 文档 Phase A |

**已有且常被误判为缺失的**（实测确认存在）：
`margin`（融资融券，东财 datacenter，`DATE` 倒序，支持 `days` 取最近 N 日）、
`holder_num`（股东户数变动历史）、`sw_industry_history`（申万行业变迁史）、
`valuation_history`（估值日频）、`dividend_history`（分红送转，含除权除息日）、
`unlocks`/`unlock_stocks`（解禁）、`longhu`（龙虎榜）、`northbound_hold`（北向持股）。

---

## 5. 落地顺序

```
B1 → B2 → B3（可并行）→ B4        # 复权：4 条缺陷，B1/B2 是纯工程且直接影响正确性，先做
        ↓
C1 → C2 → C3 → C5                 # 宽表：依赖 B 之后的本地湖分区
        ↓
C4（is_st 历史，等外挂源实测结论）
```

**不变量**（与仓库既有纪律一致）：禁止隐式跨 Provider fallback；新能力双登记；
新 Provider 会话源必须是合法 `SOURCE_ALIASES` 键；市场时刻一律走 `atst/domain/calendar.py`
（`market_now()` / `to_market_tz()`），改完 `TZ=UTC` 与 `TZ=Asia/Shanghai` 双跑；
报表名禁止靠猜；东财 `push2*` 控频；`ruff==0.15.2` + `mypy` 全绿；
全量 `pytest` 用 `CODEBUDDY_SAFE_DELETE_ENABLED=0 --basetemp="$TEMP/xxx"`。
