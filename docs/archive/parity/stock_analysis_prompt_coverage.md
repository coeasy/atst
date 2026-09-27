# 股票深度分析提示词模板 — atst 接口覆盖度审计与扩展方案

> **归档说明（2026-09-23）**：本文是当时的对标/审计快照，**不是现行契约**。文中以现在时出现的 `UnifiedQuoteAPI` 统一门面（包括「新增 N 个门面方法」一类清单与给下游的校验指令）已随 v16 Phase 2 物理删除；今天的对外接口面是 `atst.client.TdxClient` / `Client` 与 capability 目录，口径见 [interfaces.md](../../api/interfaces.md) 与 [ARCHITECTURE.md](../../ARCHITECTURE.md)。本文的点位数、方法名与端点清单按原文留存而不逐条订正 —— 归档负责说明当时为什么这么做，不负责说明现在怎么用。

> 审计对象：`P:\yanbao\股票深度分析提示词模板.docx`（10 大分析维度 + 4 项工具调用建议）
> 审计基线：atst @ `main`（commit `43b8182`，2026-09-10）
> 结论：**38 个子项中 14 项完全覆盖（37%）、10 项部分覆盖（26%）、14 项存在缺口（37%）**。缺口可分为「东财报表直补」「新数据源接入」「本地计算」三类，其中 **P0 类可用现有 `dc_query` 零新依赖补齐 4 项缺口（新增 7 个门面方法）**。

---

## 一、结论摘要

| 覆盖状态 | 数量 | 占比 | 说明 |
|---|---|---|---|
| ✅ 完全覆盖 | 14 | 37% | 直接调用现有门面/会话方法即可取到 |
| ⚠️ 部分覆盖 | 10 | 26% | 数据部分可得，需上层补算或换维度 |
| ❌ 存在缺口 | 14 | 37% | 需新增接口 / 新数据源 / 本地计算 / 人工判断 |

| 维度 | ✅ | ⚠️ | ❌ | 小计 |
|---|---|---|---|---|
| 1 公司概况与护城河 | 2 | 1 | 1 | 4 |
| 2 财务健康度与排雷 | 3 | 1 | 2 | 6 |
| 3 技术面走势分析 | 1 | 0 | 2 | 3 |
| 4 市场情绪与舆情 | 2 | 1 | 1 | 4 |
| 5 竞品对比与行业周期 | 0 | 2 | 1 | 3 |
| 6 估值合理性评估 | 2 | 0 | 1 | 3 |
| 7 核心风险揭示 | 0 | 1 | 1 | 2 |
| 8 资金面与筹码分布 | 3 | 2 | 1 | 6 |
| 9 治理与供应链 | 0 | 0 | 3 | 3 |
| 工具调用建议 | 1 | 2 | 1 | 4 |
| **合计** | **14** | **10** | **14** | **38** |

**缺口最集中的两处**：维度 9（治理与供应链，0/3 全覆盖）与维度 3（技术面，仅 K 线可得、
指标与支撑阻力全缺）。

**三个系统性缺口**（非个别接口问题，而是能力层次缺失）：

1. **atst 无技术指标计算层** — 全库 grep 无 `macd/rsi/boll/kdj/pandas_ta`，是纯数据获取库。提示词第 3 维度的 MACD/RSI/均线与第 6 维度 DCF 全部落在库外。
2. **atst 无报告导出能力** — `atst/output/` 仅是数据输出 writer，没有研报/Word 生成。提示词末尾「调用文档导出工具」无着落。
3. **atst 无定性/宏观数据源** — 行业周期、政策动态、地缘政治、护城河叙事均属定性内容，无任何免费接口。

---

## 二、逐维度覆盖度矩阵

### 维度 1 · 公司概况与护城河

| 子项 | atst 接口 | 状态 |
|---|---|---|
| 主营业务模式及盈利来源 | `web().profile(sym)`、`stock_base_info([sym])` | ✅ |
| 当前总市值及所属板块 | `quotes(sym)`、`snapshot(sym)`（`Quote.total_mv`）；`web().stock_boards(sym)`、`web().industry_boards()` | ✅ |
| 核心护城河（专利/品牌/网络效应） | 无专利接口；`profile` 仅公司简介文本 | ❌ 定性 |
| 行业竞争地位 | `web().board_rank()`、`stock_all_performance(date)` 可跨公司对比排名 | ⚠️ |

### 维度 2 · 财务健康度与排雷

| 子项 | atst 接口 | 状态 |
|---|---|---|
| 近 3 年营收与净利润趋势、成长性 | `financial_abstract(sym)`（含 `revenue/net_profit/revenue_yoy/net_profit_yoy`）、`web().performance()`、`stock_all_performance(report_date)` | ✅ |
| **应收账款、长期应收款变化（坏账风险）** | 无。需三大报表明细 `RPT_F10_FINANCE_GBALANCE` | ❌ **P0** |
| 资产负债率、经营性现金流净额 | `financial_abstract`（已含 `debt_ratio`/每股经营现金流） | ✅ |
| **财务造假风险排查** | 无。需审计意见/非标意见/持续经营警示 | ❌ 待验证 |
| ST 戴帽风险 | `profile` 股票简称含 ST 标识；`dc_query` 可查风险警示报表 | ⚠️ |
| 大股东违规减持 / 解禁压力 | `web().unlocks()`、`web().block_trades()`、`holder_changes()`、`web().shareholders(sym)`、`web().free_holders(sym)` | ✅ |

### 维度 3 · 技术面走势分析

| 子项 | atst 接口 | 状态 |
|---|---|---|
| 价格运行趋势（上升/盘整/下降） | `web().klines(sym, period=…)` 多周期 + `quotes` | ✅ |
| **MACD / RSI / 均线系统多空信号** | ❌ atst 无指标计算 | ❌ **本地计算** |
| **强支撑位与重阻力位** | ❌ 无 | ❌ **本地计算** |

### 维度 4 · 市场情绪与舆情

| 子项 | atst 接口 | 状态 |
|---|---|---|
| **主流券商最新评级与目标价** | 无。东财 `RPT_WEB_RESPREDICT` 含评级机构数/EPS 预测/目标价 | ❌ **P0** |
| 近期重大新闻、公告 | `web().news()`、`web().notices(syms)`、`news_financial()`、`web().research_reports()` | ✅ |
| 游资情绪倾向 | `web().longhu()`、`web().hot_rank()`、`web().block_trades()`、`web().limit_up_ladder()` | ✅ |
| **散户情绪倾向** | 无直接指标。`holder_num(sym)` 股东户数变化为间接代理 | ⚠️ |

### 维度 5 · 竞品对比与行业周期

| 子项 | atst 接口 | 状态 |
|---|---|---|
| **行业周期阶段、政策动态** | ❌ 无宏观/政策接口 | ❌ 定性 |
| 竞争对手毛利率、营收增速对比 | `financial_abstract`（含 `gross_margin`/同比）、`stock_all_performance(report_date)` 全市场截面 | ⚠️ |
| 市场份额 | 需「同板块成分 + 营收占比」自行计算（`web().board_members()` + 营收） | ⚠️ |

### 维度 6 · 估值合理性评估

| 子项 | atst 接口 | 状态 |
|---|---|---|
| PE / PB 相对估值 | `stock_valuation(sym)`（`RPT_VALUEASSESS_DET`，含历史分位） | ✅ |
| 高估 / 低估 / 合理区间判断 | `stock_valuation` 历史分位可直接判读 | ✅ |
| **DCF 内在价值** | ❌ 无模型。输入（营收增速/FCF/WACC）可从 `financial_abstract` 取 | ❌ **本地计算** |

### 维度 7 · 核心风险揭示

| 子项 | atst 接口 | 状态 |
|---|---|---|
| 行业竞争加剧 | ⚠️ 可用 `board_rank` 集中度间接推断 | ⚠️ |
| 宏观政策变动、地缘政治 | ❌ 无 | ❌ 定性 |

### 维度 8 · 资金面与筹码分布

| 子项 | atst 接口 | 状态 |
|---|---|---|
| 机构持仓（公募） | `web().fund_holdings()`、`web().shareholders(sym)` | ✅ |
| 社保 / QFII | `web().shareholders(sym)` 十大股东名称匹配 | ⚠️ |
| ETF 份额变动 | `fund_period_change(code)` 需按 ETF 代码逐个查，无批量 | ⚠️ |
| 北向资金流向 | `web().northbound()`、`web().fund_flow(syms)`、`web().sector_flow()` | ✅ |
| 散户持仓集中度变化 | `holder_num(sym)`（`RPT_HOLDERNUMLATEST`）、`free_holders(sym)` | ✅ |
| **筹码分布（收集/发散）** | ❌ 无。东财有筹码分布数据（akshare `stock_cyq_em` 同源） | ❌ **P1** |

### 维度 9 · 补充维度：治理与供应链

| 子项 | atst 接口 | 状态 |
|---|---|---|
| **公司治理结构、管理层能力** | 无。东财 `RPT_EXECUTIVE_HOLD_DETAILS`（董监高持股变动）+ F10 高管表可补 | ❌ **P0** |
| **ESG 表现** | ❌ 无。新浪财经有 11 家机构免费评级 | ❌ **P1** |
| **上下游供应链稳定性、客户集中度** | ❌ 无。东财 F10「主营构成/主要客户供应商」可补 | ❌ **P0** |

### 维度 10 · 结论与投资建议
分析产出，无需数据接口。⚠️ 但「文档导出工具」无着落 — 见「工具调用建议」。

### 工具调用建议

| 提示词需求 | atst 现状 | 状态 |
|---|---|---|
| 实时行情数据 | `quotes` / `quotes_concurrent` / `snapshot` / `ex_quotes` / `goods_quotes`（含期货/商品/汇率） | ✅ |
| 最新财务数据 | `financial_abstract`（主要指标）；三大报表明细 ❌ | ⚠️ |
| 行业对比数据 | `industry_boards` / `board_members` / `stock_all_performance` 部分 | ⚠️ |
| **文档导出工具** | ❌ `atst/output/` 仅数据 writer，无研报生成 | ❌ |

---

## 三、缺口分级与扩展渠道

### P0 — 东财 datacenter 报表直补（零新依赖，`dc_query` 已具备通用查询能力）

atst 已有 `web().dc_query(report, symbol=…, filters=…, all_pages=True)` 通用入口与
`corporate.VALID_REPORTS` 白名单（当前 9 项）。以下报表名经外部实现交叉验证，可直接注册进白名单：

| 报表名 | 内容 | 补的缺口 | 备注 |
|---|---|---|---|
| `RPT_F10_FINANCE_GBALANCE` | 资产负债表（应收账款/长期应收款/存货/商誉） | 维度2 坏账风险 | `source=HSF10, client=PC`，filter `(SECUCODE="600519.SH")` |
| `RPT_F10_FINANCE_GINCOME` | 利润表 | 维度2/5 | `sty=APP_F10_GINCOME` |
| `RPT_F10_FINANCE_GCASHFLOW` | 现金流量表 | 维度2 造血能力 | — |
| `RPT_WEB_RESPREDICT` | **券商评级 + EPS 预测 + 目标价 + 评级机构数** | **维度4 最关键缺口** | 排序键 `RATING_ORG_NUM`，对应 `data.eastmoney.com/report/profitforecast` |
| `RPT_EXECUTIVE_HOLD_DETAILS` | 董监高及相关人员持股变动明细 | 维度9 治理 | `source=WEB, client=WEB` |
| `RPT_SHARE_HOLDER_INCREASE` | 股东增持/减持（`DIRECTION` 过滤） | 维度2/8 | — |
| `RPT_F10_INFO_ORGPROFILE` | 公司概况（主营/成立日期/员工数/董事长/注册地） | 维度1/9 | 美股为 `RPT_USF10_INFO_ORGPROFILE` |

**实施要点**（沿用既有工程约定）：
- 新增 `atst/web/fin_report.py`：一个 `EastmoneyDataCenterSource` 子类 + `fetch_balance_sheet` /
  `fetch_income_sheet` / `fetch_cash_flow` / `fetch_rating_forecast` 四个方法
- 新增 `atst/web/governance.py`：高管持股 + 公司概况 + 股东增减持
- 在 `corporate.VALID_REPORTS` 注册 7 个新报表名（注释标注验证日期，沿用
  `# 分红送配（2026-09-06 实测可用）` 的写法）
- 新增 `_session_fundamental.py`，门面暴露 `balance_sheet` / `income_sheet` /
  `cash_flow_sheet` / `rating_forecast` / `executive_holds` / `org_profile` /
  `shareholder_changes` 共 7 个方法
- **合规**：atst 硬约束「禁止复制开源代码」，报表名与参数从抓包独立实现，不抄 akshare

### P1 — 新增外部数据源

| 渠道 | 内容 | 补的缺口 | 可行性 |
|---|---|---|---|
| **新浪财经 ESG 评级平台**（免费，11 家机构） | MSCI / 路孚特 / 华证 / 秩鼎 / 商道融绿 等评级与分项评分 | 维度9 ESG | 高。免费公开页面，季度更新，覆盖全部 A 股；`stock_esg_rate_sina` 聚合表可直接作为起点 |
| 华证指数官网 `chindices.com` | 华证 ESG 九档评级 + 尾部风险四档 + 历史评级 | 维度9 ESG（权威源） | 高。A 股全覆盖，追溯至 2009 年 |
| 商道融绿 `syntaogf.com` | A+/A/…/D 十档 + 行业调整分 | 维度9 ESG | 中。官网可查，季度更新 |
| **同花顺 iwencai 自然语言查询** | 「护城河」「行业地位」「政策影响」等定性问答 | 维度1/5/7 定性缺口 | **高 — atst 已有 `web().wencai()` Mixin 方法**，零新增即可作兜底 |
| 数库 A 股新闻情绪指数 | 市场/个股情绪量化 | 维度4 散户情绪 | 中 |
| 东财筹码分布 | CYQ 筹码分布（收集/发散判定） | 维度8 筹码 | 高 |

### P2 — 本地计算层（非接口范畴，建议独立模块）

提示词中有 4 类需求本质是**计算**而非**取数**，atst 作为行情数据库不宜承担，建议独立成
`atst/analysis/` 子包或拆为独立包，输入统一来自 `web().klines()` / `financial_abstract()`：

| 计算项 | 输入 | 算法参考 |
|---|---|---|
| MACD / RSI / BOLL / KDJ / MA / 均线系统 | 日线 K 线 | 标准递推公式，无外部依赖 |
| 支撑位 / 阻力位 | 日 K + 成交量 | 枢轴点（Pivot Point）+ 成交量加权均价峰值 |
| 筹码分布 | 日 K + 成交量 | 三角/正态衰减分布算法（或接 P1 东财筹码数据） |
| DCF 内在价值 | 营收/FCF/WACC（源自 `financial_abstract`） | 两阶段 DCF + 终值，输出敏感性表 |

### 无免费渠道缺口（需人工 / LLM 判断）

以下内容任何免费行情接口都不提供，应显式标注为「需分析师判断」，或由 LLM 联网检索后
人工核验：

- 行业周期阶段判定（复苏/繁荣/衰退/萧条）
- 政策动态与监管走向
- 地缘政治冲突影响
- 护城河叙事（技术专利壁垒、品牌、网络效应的强度评估）
- 管理层能力评估
- 报告导出（Word/研报格式）— 属交付工具层，非数据层

---

## 四、建议实施顺序

| 阶段 | 内容 | 新增门面方法 | 预期收益 |
|---|---|---|---|
| **P0-A** | `fin_report.py` 三大报表 + 估值预测 | `balance_sheet` / `income_sheet` / `cash_flow_sheet` / `rating_forecast`（4） | 直接补齐维度 2 坏账风险 + 维度 4 券商评级目标价两个**硬缺口** |
| **P0-B** | `governance.py` 治理与供应链 | `executive_holds` / `org_profile` / `shareholder_changes`（3） | 补齐维度 9 治理缺口 |
| **P1-A** | 新浪 ESG 数据源 | `esg_rating` / `esg_ratings_all`（2） | 补齐维度 9 ESG |
| **P1-B** | 筹码分布 | `chip_distribution`（1） | 补齐维度 8 筹码分布 |
| **P2** | `atst/analysis/` 本地计算层 | `technical_indicators` / `support_resistance` / `dcf_valuation`（3） | 补齐维度 3 技术指标 + 维度 6 DCF |

**总收益预估**：全部落地后 38 子项中约 **26 项可自动取数**（覆盖度 37% → 68%）。
剩余约 12 项属**定性判断**（护城河叙事、行业周期判定、政策与地缘政治、管理层能力）与
**交付工具层**（研报文档导出），不应由行情数据层承担——应由 LLM 联网检索 + 人工核验，
或交给独立的文档生成工具链。

**优先级建议**：P0-A 的 `rating_forecast`（券商评级与目标价）是提示词中价值密度最高的一项，
它同时服务维度 4「市场情绪」与维度 6「估值合理性」，且完全落在现有 `dc_query` 能力内，
建议作为第一批落地。

---

## 五、审计方法与已验证事实

- **接口面枚举**：`atst/facade/api.py`（76 个门面方法）+ `atst/web/_session_*.py`
  六个 Mixin（约 110 个会话方法），合计约 180 个公开数据接口。
- **能力边界确认**：全库正则扫描 `macd|rsi|boll|kdj|pandas_ta|ta\.lib` 零命中 →
  确认无技术指标计算；`atst/output/` 仅 `__init__.py` 且无报告生成 → 确认无文档导出。
- **现有字段确认**：`EastmoneyFinanceMainSource.FIELD_MAP` 实测含
  `revenue / net_profit / revenue_yoy / net_profit_yoy / gross_margin / debt_ratio / eps / roe / bps`，
  维度 2 成长性与偿债能力已覆盖，缺口仅在**三大报表明细**。
- **报表名验证**：三大报表与评级预测报表名均经独立第三方实现交叉验证；东财报表名偶发变动，
  落地时按既有 best-effort 约定（服务端返回「报表配置不存在 code=9501」时重新抓包校准）。
- **合规提示**：atst 硬约束禁止复制开源代码，P0/P1 所有报表名、参数与解析逻辑需自行抓包
  实现，参考实现仅用于确认端点与字段存在性。
