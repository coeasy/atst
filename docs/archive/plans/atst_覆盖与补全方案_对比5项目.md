# atst 对标 5 个行情项目的覆盖分析与全量覆盖改进方案

> 比对对象：`1nchaos/adata`、`zhangxiangliang/stock-api`、`simonlin1212/a-stock-data`、`ArvinLovegood/go-stock`、`clawyi-com/china-market-data`
> 基准仓库：`P:\github_public\tstdx`（**atst v1.1.0**，2026-10-02 发布，171 个已注册能力）
> 比对日期：2026-10-02
> 关联方案：`docs/atst最终最优化方案.md`（以下简称「原方案」，落款 2026-10-01）

---

## 0. 结论速览

**一句话结论：atst（v1.1.0）已经覆盖了这 5 个项目约 90% 的"行情接口"面，并且在多个维度上反超（TDX 协议级、vipdoc 离线、集思录可转债/场内基金折溢价、四面接入、跨风控域多源）。剩余真实缺口是一个定义清晰的小集合（约 13 类），外加原方案的"治理面"尚未落地。**

> ⚠️ 与原方案的重要偏差：**原方案是在 v1.1.0 之前写的**，它把"北向/两融/龙虎榜/打板/研报/宏观 CPI/期权/筹码/板块资金流"列为待建（L2/L3）。但 v1.1.0 已把这些全部落地（见第 5 节）。所以"是否全部覆盖"这个问题的答案，和原方案假设的起点完全不同。

### 5 个项目真实接口画像（去重后只有 4 个独立参照系）

| 项目 | 类型 | 接口面要点 | 与 atst 关系 |
|---|---|---|---|
| **adata** | A 股 Python 库 | 基础信息（代码/股本/申万/概念/指数成分/交易日历）、行情（日周月/分时/实时/五档/逐笔/概念/指数）、财务（核心指标）、ETF、可转债、舆情（解禁/两融/北向/热榜/龙虎/扫雷） | 子集，atst 在绝大多数面已≥它 |
| **stock-api** | Node 零依赖工具 | `getStock/getStocks/getKlines/searchStocks/inspectStock` + MCP，`tencent→sina→eastmoney` 自动兜底，A/港/美/ETF | atst 在源多样性、协议级、四面接入上**反超** |
| **a-stock-data** | 15 层 / 87 端点 / 34 源 Skill | 行情/研报/信号/资金筹码/新闻/财务/公告/打板/ETF期权/舆情/宏观利率/指数日历/期货大宗/事件驱动/可转债 | 参照系最全；atst 已覆盖其 87 端点中的约 78 个 |
| **china-market-data** | — | **a-stock-data 的衍生 fork**（README 自述"感谢 a-stock-data 的原始工作"），15 层/87 端点同构 | 与 a-stock-data 等价，不增加新接口 |
| **go-stock** | AI 分析桌面应用 | `gotdx` + TDX MAC 协议、港/美/ETF、股指期货持仓(IF/IH/IC/IM)、集合竞价、龙虎榜席位、政策新闻、期货、海外指数/韩股(Naver) | 应用层；其"数据接口"atst 多数已有，少数（股指期货会员持仓排名、海外/韩股）是缺口 |

---

## 1. atst 当前真实能力盘点（来自运行期枚举，171 个）

> 来源：`atst.catalog.capability.MIGRATED_CAPABILITIES` 实际枚举（唯一事实源）。

| 能力域 | 已注册能力（关键名） | 状态 |
|---|---|---|
| 实时行情 | `quotes_concurrent`、`global_quotes`、`globals`、`hk_quotes`、`us_quotes`、`ex_quotes`(北交所)、`goods_quotes`(商品)、`fx_rates`/`rates`(外汇)、`futures_realtime`、`bond_realtime`、`fund_snapshot`、`mac_quotes`(TDX MAC/集合竞价) | ✅ 多市场齐全 |
| 历史 K 线 | `klines`、`minute`、`minute_klines`、`baidu_kline`、`ex_bars`、`goods_bars`、`bond_kline`、`futures_kline`、`adjusted_bars`(复权)、`history`、`all_market`、`sync_daily` | ✅ 日/周/月/分钟/复权/全市场盘后 |
| 逐笔/盘口 | `ticks`、`baidu_ticks`、`bond_trades`、`futures_trades`、`trades`、`mac_quotes` | ✅ |
| 基本面 | `balance_sheet`/`income_sheet`/`cash_flow`/`fin_report`/`financial_abstract`、`rating_consensus`/`rating_forecast`、`research_reports`/`reports`、`earnings_preview`/`forecast`、`research_visits` | ✅ 三表+评级+研报+预告+调研 |
| 分红/股本/股东 | `dividend_history`、`corporate_action`、`capital_changes`、`holder_num`/`holder_changes`/`shareholders`/`top_holders`/`free_holders` | ✅ |
| 基金（庞大） | `fund_*` 约 30 个（净值/估值/列表/排行/经理/公司/持仓/行业分布/资产配置/评级/搜索/周期/公告日…） | ✅ 远超对标 |
| 可转债（独特） | `convertible_bond`、`convertible_bonds`（集思录：溢价率/收益率/强赎/剩余年限） | ✅ 对标四家全无 |
| 期权 | `options_list`、`options_snapshot`、`options_trends`（T 型/希腊字母/IV） | ✅ |
| 期货/商品 | `futures_base_info`/`futures_kline`/`futures_realtime`/`futures_trades`、`goods_bars`/`goods_quotes` | ✅ |
| 龙虎榜/打板 | `longhu`、`limit_pool`、`limit_up_ladder` | ✅ |
| 资金流 | `fund_flow`/`fund_flow_history`/`big_order_flow`/`sina_fund_flow`/`sina_board_fund_flow`/`sector_flow` | ✅ 含板块/大单/历史 |
| 北向/两融 | `northbound`/`northbound_hold`、`margin` | ✅ |
| 大宗/解禁 | `block_trades`、`unlock_stocks`/`unlocks` | ✅ |
| 筹码 | `chip_distribution`/`chip_distributions` | ✅ |
| 热度/题材 | `hot_rank`/`hot_boards`/`theme_attribution`/`market_breadth`/`market_stat`/`rank` | ✅ |
| 新闻/公告 | `news`/`news_financial`/`breaking_news`/`announcements`/`notices`/`hk_announcements`/`dc_reports` | ✅（缺新闻联播） |
| 指数/行业/概念 | `index`/`index_list`/`index_constituents`/`industry_index`/`concept_index`、`industry_board(s)`/`concept_members`/`board_*`/`stock_boards`/`em_boards` | ✅（申万未显式） |
| 宏观（部分） | `macro_cpi`/`macro_gdp`/`macro_ppi` | ⚠️ 仅 3 项 |
| 新股/ESG/筛选 | `ipo_calendar`/`ipo_review`、`esg_*`、`screening`/`wencai` | ✅ |
| 公司概况 | `profile`/`org_profile(s)`/`stock_base_info`/`f10`/`f10_catalog` | ✅ |
| **仍注册但原方案判"死"** | `auction`(0x056A)、`volume_price`(0x051A)、`block_quotes`(0x07E5)、`minute_history`(0x0FB4) | ❌ 治理缺口 G1 |

---

## 2. 覆盖对照矩阵（接口类别 × 5 项目 × atst）

| 接口类别 | adata | stock-api | a-stock-data | go-stock | **atst 现状** |
|---|---|---|---|---|---|
| A 股实时/批量 | ✅ | ✅ | ✅ | ✅(TDX) | ✅ `quotes_concurrent` |
| 港股/美股实时 | ❌ | ✅ | ✅ | ✅ | ✅ `hk_quotes`/`us_quotes`（**需真机核可用性**） |
| 商品/外汇 | ❌ | ❌ | ✅ | ❌ | ✅ `goods_quotes`/`fx_rates` |
| 期货/股指期货 | 债 | ❌ | ✅ | ✅(IF/IH/IC/IM) | ✅ `futures_*`；**缺会员持仓排名** |
| 期权 T 型/希腊字母/IV | ❌ | ❌ | ✅ | ❌ | ✅ `options_*` |
| 日/周/月/分钟 K 线 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 前/后复权 | ❌ | ❌ | ✅ | ❌ | ✅ `adjusted_bars` |
| 全市场盘后日线 | ❌ | ❌ | ✅ | ❌ | ✅ `all_market`/`history`/`sync_daily`(vipdoc) |
| 逐笔成交 | ✅ | ❌ | ✅ | ❌ | ✅ `ticks`/`baidu_ticks` |
| 五档盘口 | ✅ | ❌ | ✅ | ✅(MAC) | ✅ `mac_quotes` |
| 股票列表/代码 | ✅ | ✅(search) | ✅ | ✅ | ✅ `security_list_all`/`search_symbols`/`suggest` |
| 申万行业 | ✅ | ❌ | ✅ | ❌ | ⚠️ 未显式暴露 |
| 概念/板块成分 | ✅ | ❌ | ✅ | ✅ | ✅ `concept_members`/`board_*` |
| 交易日历（可查询） | ✅ | ❌ | ✅ | ❌ | ❌ 仅内部 `domain/calendar.py`，无能力 |
| 财务三表 | ⚠️核心指标 | ❌ | ✅ | ✅ | ✅ |
| 估值历史（日频 PE/PB 序列） | ❌ | ❌ | ✅(回溯2016) | ❌ | ❌ 仅当前 `stock_valuation` |
| 评级/一致预期 | ❌ | ❌ | ✅ | ❌ | ✅ `rating_*` |
| 研报列表/PDF | ❌ | ❌ | ✅ | ✅ | ✅ `research_reports`/`reports` |
| 业绩预告/机构调研 | ❌ | ❌ | ✅ | ❌ | ✅ `earnings_preview`/`research_visits` |
| 分红送转 | ✅ | ❌ | ✅ | ❌ | ✅ `dividend_history` |
| 融资融券 | ✅ | ❌ | ✅ | ❌ | ✅ `margin` |
| 大宗交易 | ❌ | ❌ | ✅ | ❌ | ✅ `block_trades` |
| 股东户数/变化 | ❌ | ❌ | ✅ | ❌ | ✅ `holder_num`/`holder_changes` |
| 限售解禁 | ✅ | ❌ | ✅ | ❌ | ✅ `unlock_stocks`/`unlocks` |
| 筹码分布 CYQ | ❌ | ❌ | ✅ | ❌ | ✅ `chip_distribution` |
| ETF/基金 | ✅ | ✅ | ✅ | ✅ | ✅（基金极全） |
| **ETF 份额** | ❌ | ❌ | ✅ | ❌ | ❌ 无显式能力 |
| 可转债 | ✅ | ❌ | ✅ | ❌ | ✅（集思录，最强） |
| 龙虎榜 | ✅ | ❌ | ✅ | ✅(席位) | ✅ `longhu` |
| 涨停/炸板/跌停池 | ❌ | ❌ | ✅ | ❌ | ✅ `limit_pool`/`limit_up_ladder` |
| 板块/个股资金流 | ✅ | ❌ | ✅ | ✅ | ✅ `sector_flow`/`fund_flow` |
| 北向资金 | ✅ | ❌ | ✅ | ❌ | ✅ `northbound` |
| 热榜/人气/题材归因 | ✅ | ❌ | ✅ | ✅ | ✅ `hot_rank`/`theme_attribution` |
| 新闻/快讯 | ❌ | ❌ | ✅(财联社/7×24/联播) | ✅(浏览器抓) | ✅（**缺新闻联播**） |
| 公告/互动易问答 | ❌ | ❌ | ✅(巨潮/互动易) | ❌ | ⚠️ 公告✅；**互动易问答❌** |
| 宏观利率（社融/PMI/LPR/中债/回购定盘/宏观日历） | ❌ | ❌ | ✅ | ❌ | ⚠️ 仅 CPI/GDP/PPI |
| 指数成分/权重/PE | ✅ | ❌ | ✅ | ❌ | ✅（PE/股息率⚠️） |
| 事件驱动（增减持/回购/质押/新股） | ❌ | ❌ | ✅ | ❌ | ⚠️ 增减持/新股✅；**股权质押❌**；回购⚠️ |
| ST/*ST 名单 | ❌ | ❌ | ✅ | ❌ | ❌ |
| 扫雷/风险扫描 | ✅(通达信扫雷) | ❌ | ❌ | ❌ | ❌ |
| 海外指数/韩股 | ❌ | ❌ | ❌ | ✅(Naver) | ❌（仅港/美） |
| AI 分析/选股/回测/报警 | ❌ | ❌ | ❌ | ✅ | ❌ 原方案 L6 明确**不做**（护城河在基础设施） |

---

## 3. 真实缺口清单（这 5 个项目有、atst 当前没有）

按"对标价值 × 接入成本"排序：

| # | 缺口 | 对标来源 | 目标数据源（与现有风控域互补） | 接入成本 |
|---|---|---|---|---|
| **G-01** | **交易日历（可查询接口）** | adata/a-stock | 深交所 `ShowReport`（已有 N2 规划）+ 内部 `domain/calendar.py` 暴露为能力 | 低 |
| **G-02** | **期货/期权会员持仓排名** | a-stock/go-stock | 五家期货交易所官方（郑商所/大商所/上期所/中金所/广期所，已有 N5 规划） | 高 |
| **G-03** | **宏观利率补全**：社融、PMI、LPR、中债收益率曲线、回购定盘利率、全球宏观日历 | a-stock | 人民银行/统计局/中债/中国货币网（已有 N6/N7） | 低 |
| **G-04** | **新闻联播文字稿** | a-stock | 央视网（已有 N9 候选） | 低 |
| **G-05** | **ST / *ST 名单**（含现价） | a-stock | 东财（备用 baostock，已有候选） | 低 |
| **G-06** | **股权质押** | a-stock | 中国结算/东财 `datacenter`（事件驱动层补全） | 低 |
| **G-07** | **互动易 / 上证 e 互动问答** | a-stock | 巨潮互动易 + 上证 e 互动（已有 N10） | 低 |
| **G-08** | **估值历史日频序列**（PE/PB/PS 分位，回溯多年） | a-stock | 东财估值历史接口（现有 `stock_valuation` 扩为序列） | 中 |
| **G-09** | **申万行业 + 行业变迁史** | adata/a-stock | 申万（已有 N 规划）/ 现有行业接口补齐申万口径 | 中 |
| **G-10** | **ETF 份额（万份，日频）** | a-stock | 上交所归档 + 深交所快照（现有 `fund_*` 扩展） | 中 |
| **G-11** | **扫雷 / 风险扫描** | adata | 通达信扫雷（原方案未列，独立轻量源） | 低 |
| **G-12** | **海外指数 / 韩股**（Naver fchart 类） | go-stock | 如需则新增海外源（优先级低，超出 A 股核心） | 中 |
| **G-13** | **指数估值 PE/股息率**（中证两种股本口径） | a-stock | 中证指数官网（现有 `index_*` 扩展） | 低 |

> 此外还有两处**半成品**需确认是否算覆盖：
> - **A50 期指 / 上海金现货**：`goods_quotes`/`globals`/`ex_quotes` 可能已隐式覆盖，需真机核字段。
> - **股票回购进度**：`corporate_action` 可能含，需核实字段完整性。

---

## 4. 原方案与现状的关键偏差（v1.1.0 已替你做了大半）

原方案把以下列为"待建"，但 **v1.1.0 已落地**（说明方案已被实现吸收，无需重复立项）：

| 原方案条目 | 原判定 | v1.1.0 实际 |
|---|---|---|
| L3-N1 巨潮、N4 同花顺、三源 wallstreet | 新增源 | ✅ `cninfo/` `ths/` `wallstreet/` 目录已存在 |
| L2-D6 东财估值进快照 | 待建 | ✅ `stock_valuation` 已存在 |
| 北向资金 N8/N4 | 新增 | ✅ `northbound`/`northbound_hold` |
| 两融 N2 | 新增 | ✅ `margin` |
| 龙虎榜 N2/N4 | 新增 | ✅ `longhu` |
| 打板 D18 | 0→1 | ✅ `limit_pool`/`limit_up_ladder` |
| 研报 D20 | 新增 | ✅ `research_reports` |
| 宏观 D?/N6 | 新增 | ⚠️ 仅 `macro_cpi/gdp/ppi`（G-03 补全） |
| 期权 ETF T 型 | 未列 | ✅ `options_*` |
| 筹码 D? | 未列 | ✅ `chip_distribution` |
| 资金流板块 D15/D2 | 待建 | ✅ `sector_flow`/`sina_board_fund_flow` |
| 港股/美股/商品/外汇 D1/D9 | "已死需救活" | ✅ 已注册 `hk_quotes`/`us_quotes`/`goods_quotes`/`fx_rates`（**但需真机验证是否真的活**，见 G-14） |

**因此，原方案仍有价值的不是"建端点"，而是它的"治理面"（L0/L1/L4）与"少数真缺口"（第 3 节）。**

---

## 5. 治理面待办（原方案 L0/L1/L4，v1.1.0 仍未做）

| 编号 | 待办 | 现状证据 | 风险 |
|---|---|---|---|
| **G-14** | **港股/美股/商品/外汇实际可用性真机验证** | 能力已注册，但原方案 P2 判其"已死"，M1 前置未核 | 可能"宣称有、实际挂"——比没有更危险 |
| **G-15** | **4 个死名字处置**（L0-S2） | `auction`/`volume_price`/`block_quotes`/`minute_history` 仍在 171 清单 | 调用即失败，误导"172 能力" |
| **G-16** | **capabilities 带状态**（L1-T1/T2） | 171 个无 `available/offline/degraded` | 死名字仍四面出口可见 |
| **G-17** | **字段 spec 单一事实源 + golden 断言**（L4-A1/A2） | 9 类陷阱（腾讯 mkline 第 8 字段、单位、编码、五档位置…）未钉死 | 隐性 bug 高发 |
| **G-18** | **能力路由表 + 风控域标签 + 跨域 fallback**（L4-A3/A4） | 每能力跨 `risk_domain` 源数未知 | 东财同域被封全灭无自动降级 |
| **G-19** | **跨源对拍告警**（L4-A7） | 未做 | 复权/实时价口径漂移无监控 |
| **G-20** | **单位/编码归一化收口**（L4-A5） | `web/normalize.py` 部分存在，但未全量声明 | 量(手/股)、额(元/万)、市值单位混 |

---

## 6. 全量覆盖改进方案（分阶段，映射到 atst 现有架构）

> atst 的新端点机制：**在 `WebQuoteSession` 的某个 Mixin 上加 `def xxx(...)` 方法 → `catalog/capability.py` 的 `_discover_web_bindings()` 自动发现为能力**（零注册代码）。新增数据源 = 在 `atst/web/<source>/adapters.py` 写适配器 + 在对应 Mixin 暴露方法。所以"补全接口"的工程成本极低，瓶颈在"源可用性验证"。

### 阶段 M-A（第 1–2 周）：先治"假覆盖"——把真活的确认掉，把死的清掉
- **G-14**：对 `hk_quotes`/`us_quotes`/`goods_quotes`/`fx_rates`/`ex_quotes` 跑真机握手，结果入 `runtime.health`；不活的走原方案 D1(腾讯 hk/us/hf_/fx_) / D9(新浪 bj/hk/gb_) 前缀复活。
- **G-15/G-16**：4 个死名字四选一处置（修参/换命令/转 Web/下线），并让 capabilities 带 `status`，offline 名字从 CLI/HTTP/WS/MCP 四面移除（门禁 `test_capability_status_surface`）。
- 出口标准：171→真实可用数，零裸死名。

### 阶段 M-B（第 2–4 周）：补齐"低成本高价值"真缺口（G-01/03/04/05/06/07/11/13）
- G-01 交易日历：把 `domain/calendar.py.get_calendar` 暴露为 `trade_calendar` 能力（深交所官方源）。
- G-03 宏观补全：人民银行社融/PMI、LPR、中债曲线、回购定盘、全球宏观日历 → 一个 `macro_*` 系列能力。
- G-05 ST 名单、G-06 股权质押、G-07 互动易、G-04 新闻联播、G-11 扫雷、G-13 指数估值：各自一个轻量适配器 + Mixin 方法。
- 出口标准：第 2 节矩阵中"❌"项清零（除 G-12 海外）。

### 阶段 M-C（第 4–7 周）：补齐"中成本"缺口（G-08/09/10）
- G-08 估值历史序列：复用东财估值历史接口，把 `stock_valuation` 扩为 `valuation_history`。
- G-09 申万行业 + 变迁史：补申万口径，消除前视偏差。
- G-10 ETF 份额：上交所归档 + 深交所快照，单位万份。
- 出口标准：a-stock-data 87 端点对齐率 ≥ 98%。

### 阶段 M-D（第 7–10 周）：期货持仓排名 + 治理硬化（G-02/17/18/19/20）
- G-02 会员持仓排名：五家期货交易所官方（大商所 JS 反爬需绕过，参考 a-stock-data 的"新浪期货日 K + 实时期货"兜底）。
- G-17~G-20：字段 spec YAML（A1）、golden 断言（A2）、风控域路由（A3/A4）、跨源对拍（A7）、单位编码收口（A5）。
- 出口标准：`atst doctor` 全端点健康度 + 9 类陷阱各 ≥1 断言 + 每能力 ≥2 跨域源。

### 阶段 M-E（可选，第 10+ 周）：G-12 海外指数/韩股
- 仅当确认有需求才做（超出 A 股核心，且原方案 L6 纪律强调"基础设施不追应用层"）。

---

## 7. atst 已经反超、不应重复造轮子的点

| 维度 | atst 独有/领先 | 对标 |
|---|---|---|
| TDX 协议级 + vipdoc 离线 | 5 协议族 / 85 命令 / 61 解析器 / 本地盘后包增量落盘 | 5 家全无 |
| 集思录可转债/场内基金折溢价 | 溢价率/到期收益率/强赎/剩余年限 | 仅 a-stock/adata 有可转债，无折溢价深度 |
| 四面接入 | CLI 31 / HTTP 43 / WS 13–19 / MCP 9–13 | stock-api 仅 Node/CLI/MCP |
| 跨风控域多源 | TDX/腾讯/东财/百度/新浪/集思录/中行/巨潮/同花顺/wallstreet/cninfo | stock-api 仅 tencent/sina/eastmoney |
| 工程化 | 主站池/bestip/熔断/限流/Prometheus/40+ 错误类/门禁 | — |

---

## 8. 验收清单（全量覆盖后）

- [ ] 第 2 节对照矩阵中所有 "❌" 清零（G-01~G-11、G-13）
- [ ] `auction`/`volume_price`/`block_quotes`/`minute_history` 100% 归位或下线（G-15）
- [ ] `hk/us/goods/fx/ex` 真机验证全绿或已走 D1/D9 前缀复活（G-14）
- [ ] capabilities 全部带 `status`，offline 不进四面出口（G-16）
- [ ] 宏观补全社融/PMI/LPR/中债/回购定盘/宏观日历（G-03）
- [ ] 估值历史序列、申万行业、ETF 份额、互动易、股权质押、ST 名单、新闻联播、扫雷 全部可用（G-04~G-11）
- [ ] 期货/期权会员持仓排名可用（G-02）
- [ ] 字段 spec + golden 断言覆盖 9 类陷阱；每能力 ≥2 跨域源；跨源对拍告警上线（G-17~G-20）
- [ ] README 能力矩阵由 capabilities 状态自动生成（禁止手抄）

---

## 9. 一句话给决策者的建议

**不要重做原方案里的"建端点"部分——v1.1.0 已经做了。真正要投入的三件事是：**
1. **确权**：把已注册却可能"假活"的港/美/商品/外汇能力真机验证一遍（G-14），把 4 个死名字清掉（G-15/16）；
2. **补缺**：按第 3 节 13 个缺口，优先做低成本高价值的（交易日历/宏观补全/ST/质押/互动易/新闻联播/扫雷），它们大多一个轻量适配器即可；
3. **硬化**：把原方案 L4 的"字段 spec + golden + 风控域路由 + 跨源对拍"落地，这是 v1.1.0 大量新增端点后最该补的"防隐性 bug"护栏。
