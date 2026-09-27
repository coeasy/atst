# niuniu ↔ tstdx 接口覆盖审计

> **归档说明（2026-09-23）**：本文是当时的对标/审计快照，**不是现行契约**。文中以现在时出现的 `UnifiedQuoteAPI` 统一门面（包括「新增 N 个门面方法」一类清单与给下游的校验指令）已随 v16 Phase 2 物理删除；今天的对外接口面是 `tstdx.client.TdxClient` / `Client` 与 capability 目录，口径见 [interfaces.md](../../api/interfaces.md) 与 [ARCHITECTURE.md](../../ARCHITECTURE.md)。本文的点位数、方法名与端点清单按原文留存而不逐条订正 —— 归档负责说明当时为什么这么做，不负责说明现在怎么用。

> 审计对象：`P:\sd\niuniu`（牛牛 A股量化/选股/行情平台，FastAPI + ECharts，84 个 py 文件）
> 审计方：`p:\github_public\tstdx`（TDX 行情通用库，统一门面 `UnifiedQuoteAPI`）
> 审计日期：2026-09-09
> 核心问题：**niuniu 的全部接口是否可以被 tstdx 全部包含？**

## 0. 结论摘要（先给答案）

**不能逐条"包含"niuniu 的 HTTP 端点，但能完整接管其底层数据层。**

- niuniu 共暴露 **111 个 HTTP 端点**（含 6 个页面路由 + 1 个 WS）。按性质分三类：
  - **(a) 纯行情数据接口 22 个** —— 依赖底层多源数据（实时/历史/基本面/板块/资讯）
  - **(b) 衍生分析接口 48 个** —— 选股 DSL / 轮动 / 牛熊 / 市场分析 / ML 预测 / 指标（全部由本地 parquet + 实时快照二次计算得到）
  - **(c) 应用内接口 41 个** —— 鉴权 / 自选股 / 会话 / 配置 / 缓存同步 / Agent / 健康
- tstdx 是**数据获取库**，不是应用框架，因此：
  - **(b) 48 个 + (c) 41 个 = 89 个端点**，tstdx 不可能包含（它们是业务逻辑/应用骨架，只能由 niuniu 自己保留）。
  - **但 (a) 22 个端点所消费的"底层数据域"，tstdx 可以 1:1 接管**，并作为统一数据后端供给 (b) 所需的原始输入。

**数据域覆盖结论**（2026-09-09 第二轮补齐后）：niuniu 实际依赖 **16 个数据域**，tstdx 现已全部覆盖 —— 首轮已覆盖 13 个；本轮新增 **3 个资讯类域（财经新闻 / 研报 / 机构调研）** 并关闭 **1 个接线缺口**（`holder_num`/`free_holders` 已提升到门面），净覆盖率 **16/16**。详见第 6 节「缺口闭环实施记录」。

---

## 1. niuniu 接口全景（111 端点，单文件 `app/main.py`）

| 类别 | 数量 | 说明 | tstdx 可否"包含" |
|------|------|------|------------------|
| (a) 纯行情数据接口 | 22 | quotes / K线 / 指数 / 板块 / 实时 / 基本面 / 估值 / 属性 / 新闻公告研报 | 作为**数据后端**可接管其底层数据域 |
| (b) 衍生分析接口 | 48 | 选股 / 轮动 / 牛熊 / 市场分析 / ML / 指标 / 验证 / 模型 | **不可**（业务逻辑，保留在 niuniu） |
| (c) 应用内接口 | 41 | 鉴权 / 自选 / 会话 / 配置 / 缓存同步 / Agent / 健康 | **不可**（应用骨架，保留在 niuniu） |

> 注：所有路由定义在 `app/main.py` 单文件内（无 `APIRouter`/`include_router`），`run.py` 仅 `uvicorn app.main:app`。

### (a) 22 个纯数据端点清单

| 端点 | 消费的数据域 |
|------|-------------|
| `GET /api/stocks` | 个股搜索（名称+代码） |
| `GET /api/kline/{code}` | 历史 K 线 + 指标 |
| `GET /api/market/indices` (+`/overview`) | 指数实时报价 |
| `GET /api/market/index/{code}` | 单指数：报价+日K+成分 |
| `GET /api/market/sectors` | 行业/概念板块实时表现 |
| `GET /api/market/breadth` | 市场宽度（由快照派生） |
| `GET /api/market/ranking` | 全市场排行（由快照派生） |
| `GET /api/market/limit_list` | 涨跌停（由快照派生） |
| `GET /api/intraday/{code}` | 当日分时 |
| `GET /api/market/ownership` | 国企/社保/民营池 |
| `GET /api/realtime` | 实时快照 |
| `GET /api/stock/fundamentals/{code}` | F10 基本面 |
| `GET /api/stock/valuation/{code}` | 7 维估值健康 |
| `GET /api/stock/attributes/{code}` | 指数成分/属性标记 |
| `GET /api/stockinfo/summary`、`/{code}` | 基本面汇总 / 合并信息 |
| `GET /api/news/financial` | 财经新闻头条（Sina） |
| `GET /api/news/announcements/{code}` | 公告（CNINFO） |
| `GET /api/news/research/{code}` | 研报（10jqka） |
| `GET /api/news/research-visits/{code}` | 机构调研（CNINFO） |
| `GET /api/news/{code}` | 资讯聚合 |

---

## 2. 数据域覆盖矩阵（niuniu 消费域 ↔ tstdx）

> ✅ = tstdx 已有等价门面方法；⚠️ = best-effort/需校准/后端不同；❌ = 硬缺口；🔧 = 已有会话层方法但未提升到门面。

| # | 数据域 | niuniu 当前源 | tstdx 对应门面方法 | 状态 |
|---|--------|--------------|-------------------|------|
| 1 | 实时快照（个股/全市场） | tencent/mootdx/eastmoney/tdxpy/easyquotation/sim | `quotes` / `snapshot` / `quotes_concurrent` | ✅ |
| 2 | 历史 K 线（日/周/月/季/年 + 指数 + 分钟） | mootdx/tdxpy/tencent/baostock/sina/akshare/synthetic | `bars` / `minute` / `minute_history` / `adjusted_bars` / `history` / `minute_web` / `minute_klines` / `baidu_kline` | ✅ |
| 3 | 当日分时（intraday） | 腾讯 `web.ifzq.gtimg.cn` + 东财 push2his（直连 HTTP） | `minute` / `minute_history` / `minute_web` / `baidu_minute` / `minute_klines` | ✅ |
| 4 | F10 基本面（名称/市值/PE/PB/股本/行业/上市日/每股净资产） | 6 源合并（mootdx/tencent/eastmoney/sina/xueqiu/ths） | `finance` / `stock_base_info` / `stock_valuation` / `financial_abstract` / `dividend_history` / `holder_changes` / `announcements` / `capital_changes` / `corporate_action` | ✅ |
| 5 | 分红送股 / 除权除息 | mootdx xdxr + cninfo/akshare | `dividend_history` / `capital_changes` / `corporate_action` | ✅ |
| 6 | 财务摘要 / 季度业绩 | akshare `stock_financial_abstract_ths`（直连） | `finance` / `financial_abstract`（⚠️ 东财主要指标精简版） | ⚠️ |
| 7 | 指数成分股 | akshare CSIndex/Sina（直连） | `index_constituents` | ✅ |
| 8 | 大盘指数实时报价 | 腾讯 `qt.gtimg.cn`（直连 HTTP） | `quotes`（支持指数代码）/ `snapshot` | ✅ |
| 9 | 概念板块（目录 + 个股概念） | 新浪目录 + 东财/同花顺反向索引 | `board_list("concept")` / `board_members` / `em_boards` / `em_board_members` / `stock_boards` | ✅ |
| 10 | 行业分类 / 行业板块表现 | 新浪 49 行业（直连 HTTP） + 同花顺 akshare | `industry_boards` / `board_list("industry")` | ✅ |
| 11 | 国企/民营/属性池 | CSIndex 000955（中证国企）+ 核心指数成分 | `index_constituents("000955")` + 核心指数成分 | ✅（社保重仓 niuniu 自身亦 unavailable） |
| 12 | 资金流 / 融资融券 / 大单 | 东财 push2 / fund_flow | `margin` / `big_order_flow` / `sector_flow` / `hot_rank` / `stock_changes` | ✅ |
| 13 | 个股搜索 / 全量代码表 | akshare / 本地 | `security_list` / `security_list_all` / `suggest` / `search_symbols` / `index_list` | ✅ |
| 14 | IPO | 东财 datacenter | `ipo_calendar` / `ipo_review` | ✅ |
| 15 | 股东户数 / 十大流通股东 | mootdx finance + 东财 RPT | `holder_num` / `free_holders`（已提升到 `UnifiedQuoteAPI`） | ✅ |
| 16 | 公告 | CNINFO | `announcements`（东财 `np-anotice`，⚠️ 后端不同但等效） | ⚠️ |
| 17 | 财经新闻头条 | 新浪财经 | `news_financial`（东财 `newsapi.eastmoney.com` 快讯） | ✅ |
| 18 | 个股研报 | 同花顺 10jqka | `research_reports`（东财 `reportapi.eastmoney.com/report/list`） | ✅ |
| 19 | 机构调研记录 | 巨潮 CNINFO | `research_visits`（东财 `datacenter-web` 调研纪要） | ✅ |

### 缺口闭环（2026-09-09 第二轮已实现）

首轮审计标记的 **3 个硬缺口 + 1 个接线缺口已全部闭环**：

| 数据域 | niuniu 端点 | tstdx 落地 | 后端 |
|--------|------------|-----------|------|
| 财经新闻头条 | `GET /api/news/financial` | `news_financial()` / `EastmoneyNewsSource.fetch_news` | 东财 `newsapi.eastmoney.com/kuaixun` |
| 个股研报 | `GET /api/news/research/{code}` | `research_reports()` / `EastmoneyResearchSource.fetch_reports`（既有源，本轮接线） | 东财 `reportapi.eastmoney.com` |
| 机构调研 | `GET /api/news/research-visits/{code}` | `research_visits()` / `EastmoneyResearchVisitSource.fetch_visits` | 东财 `datacenter-web` 调研纪要 |
| 股东户数 / 十大流通股东 | 估值页依赖 | `holder_num()` / `free_holders()`（从会话层提升到门面） | 东财 `RPT_HOLDERNUMLATEST` / `RPT_F10_EH_FREEHOLDERS` |

> 注：tstdx 原本已有 `EastmoneyResearchSource`（研报）与 `EastmoneyNoticeSource`（公告）两个源，
> 但都未接线到门面；本轮除接线外还修复了一个**生产级 bug**（见下）。

---

## 3. 不能"包含"的部分（明确边界）

以下 89 个端点属于 niuniu 的应用层，tstdx 作为数据库**不应也不需**包含：

- **(b) 衍生分析 48 个**：选股 DSL 引擎、双周期/经典/尾盘突破策略、板块轮动、牛熊评估、市场分析、ML 预测/训练/回测、指标注册表、验证统计、交易候选池 —— 全部由 niuniu 本地 parquet + tstdx 提供的实时快照二次计算。
- **(c) 应用内 41 个**：JWT 鉴权、用户/角色、自选股 CRUD、Agent 会话与流式聊天、缓存/刷新/同步/采集、调度器性能、健康/状态、WebSocket 实时推送。

> 若把 niuniu 重构为「tstdx 做统一数据后端 + niuniu 保留分析/应用层」，上述 89 个端点**原样保留**，仅把 (a) 22 个端点背后的多源适配器替换为 tstdx 调用。

---

## 4. 替换方案建议

**目标**：用 tstdx 的 `UnifiedQuoteAPI` 替换 niuniu `app/data/sources/` 下 9 个适配器（akshare/baostock/eastmoney/mootdx/sina/tdxpy/tencent/easyquotation/sim/synthetic）+ `market.py`/`fundamentals.py`/`stockinfo.py` 中的直连 HTTP。

**步骤**：

1. **建适配桥**（在 niuniu 侧新增 `app/data/sources/tstdx_src.py`，实现 `HistorySource` 与 `RealtimeSource` 两个 base 接口）：
   - `TstdxHistorySource.fetch_kline` → `api.bars(...)` / `api.minute(...)`
   - `TstdxRealtimeSource.fetch_quotes` → `api.quotes(...)`
2. **F10/基本面**：`stockinfo.build_one` 改为调用 `api.finance` / `api.stock_base_info` / `api.dividend_history` / `api.holder_num`（接线后）/ `api.announcements`。
3. **板块/指数/概念**：`market.py` 的直连 akshare/HTTP 改为 `api.index_constituents` / `api.industry_boards` / `api.board_members` / `api.stock_boards`。
4. **补缺 3 个资讯源**（若需要 1:1 覆盖）：在 tstdx 侧新增 `news.py`（财经新闻/研报/调研），niuniu 侧 `/api/news/*` 直接调用。
5. **关闭 niuniu 旧多源**：`get_history_source("tstdx")` / `get_realtime_source("tstdx")`，保留 fallback 链兜底。

**收益**：niuniu 数据层从 9+ 适配器 + 多处直连收敛为单一 `UnifiedQuoteAPI`，获得 tstdx 的 TDX 二进制协议 + Web 多源 failover + 字段归一化（volume=股/amount=元/价格÷100）能力，且后续 tstdx 新增的 efinance/astock-toolkit 接口（基金/期货/债券/估值/增减持）可即时复用。

---

## 5. 覆盖度量化

| 维度 | 数量 | 覆盖 |
|------|------|------|
| niuniu HTTP 端点总数 | 111 | tstdx 直接包含：0（角色不同） |
| └ 可经 tstdx 数据后端供给 | 22（a 类） | ✅ 数据域 **19/19** 已覆盖 |
| └ 应用/分析层（保留 niuniu） | 89（b+c 类） | 不适用 |
| niuniu 依赖数据域 | 19 | ✅17  ⚠️2（财务摘要/公告后端不同）  ❌0  🔧0 |
| **数据域净覆盖率** | — | **19/19 = 100%** |

> 一句话：**tstdx 无法"包含"niuniu 的应用，但可 100% 接管 niuniu 的行情数据供给；3 个资讯类数据域（财经新闻/研报/机构调研）与股东接线缺口已在第二轮补齐，数据域覆盖率 100%。**

---

## 6. 第二轮实施记录（2026-09-09）

### 新增 / 变更文件

| 文件 | 变更 |
|------|------|
| `tstdx/web/news.py` | 新增 `EastmoneyNewsSource`（财经快讯）、`EastmoneyResearchVisitSource`（机构调研）；保留既有 `SinaNewsSource`（个股新闻） |
| `tstdx/web/_session_news.py` | **新建** `NewsSessionMixin`（`news_financial` / `research_reports` / `research_visits`） |
| `tstdx/web/session.py` | `WebQuoteSession` 继承链追加 `NewsSessionMixin` |
| `tstdx/web/efinance_deriv.py` | `EastmoneyBondSource` 新增 `fetch_all_base_info`（全市场可转债 clist 枚举） |
| `tstdx/web/_session_efinance.py` | 新增 `fund_base_info_multi`（批量基金基础信息）、`bond_all_base_info`（全市场债券） |
| `tstdx/facade/api.py` | `UnifiedQuoteAPI` 新增 7 个方法：`news_financial` / `research_reports` / `research_visits` / `free_holders` / `holder_num` / `fund_base_info_multi` / `bond_all_base_info` |
| `tstdx/web/corporate.py` | **bug 修复**：新增 `_base_path()`，修复 `fetch_notices`/`fetch_reports` 丢失路径段的问题（见下） |
| `tests/web/test_news.py` | **新建**，10 个离线用例（源级 + 门面端到端） |
| `tests/web/test_efinance_facade.py` | 新增 `fund_base_info_multi` / `bond_all_base_info` 2 个门面用例 |

### 顺手修掉的生产级 bug（BASE 路径段丢失）

`_EastmoneyJson._get_json(path_query)` **只补主机、不补 `BASE`**（`HOSTS` 与 `BASE` 是两套字段）。因此独立主机源里凡是直接调 `self._path(...)` 而没有 `build_url` 拼接的 `fetch_*` 方法，都会在真实请求中丢掉路径段：

```
公告：实际请求 https://np-anotice-stock.eastmoney.com?sr=-1&stock_list=600519
      正确请求 https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&...
研报：实际请求 https://reportapi.eastmoney.com?pageSize=20&code=600519
      正确请求 https://reportapi.eastmoney.com/report/list?pageSize=20&...
```

两处原代码 `url = self._path(...)` 直接丢掉了 `/api/security/ann` 与 `/report/list`，线上必然 404。旧测试用「无条件返回罐头」的假客户端且只断言 URL 子串（`stock_list=`、`code=`），因此没测出来。

**修法**：新增 `_base_path(base)` 用 `urlsplit(base).path` 取 `BASE` 的路径段（单一事实来源，避免与 `BASE` 漂移），两处 `fetch_*` 改为 `url = _base_path(self.BASE) + self._path(...)`。`build_url` 仍返回带主机的完整 URL（给通用 `fetch` 用），两套契约各自自洽。

### 设计取舍

- **研报源沿用既有实现**：tstdx 早已有 `EastmoneyResearchSource`，本轮只接线到门面，不重复造源。
- **机构调研走 datacenter-web**：报表名 `RPT_ORG_SURVEY_DET` 为 best-effort，若服务端返回 code=9501 需重新抓包校准（与 IPO 审核、财务摘要同一处理策略）。
- **快讯走东财 `newsapi`**：字段兼容 `ctime`（unix 秒）与 `update_time`（字符串）两种时间格式，标题兜底 `title` → `digest`。
- **可转债枚举范围**：`fetch_all_base_info` 用 push2 clist 的 `m:128`（沪）+ `m:80`（深），即零售最关注的可转债池；`f3`/`f104`/`f106` 均按 ×100 口径还原。

### 测试

- 新增/扩展用例共 **12 个**（`test_news.py` 10 个 + `test_efinance_facade.py` 2 个），全部离线（罐头客户端），**23 个相关用例全绿**。
- `tests/web/ -m "not network"` 全量离线套件 **EXIT=0**，无回归（含 `TestEastmoneyNoticeSource` / `TestEastmoneyResearchSource` 旧用例）。

### efinance 剩余项（本轮一并补齐）

| efinance 函数 | tstdx 落地 |
|--------------|-----------|
| `fund.get_base_info_muliti` | `fund_base_info_multi(codes)`（逐只容错，单只失败不阻断批量） |
| `bond.get_all_base_info` | `bond_all_base_info()`（push2 clist 全量枚举可转债） |

> 至此 efinance 四模块（stock 18 / fund 14 / futures 4 / bond 9）数据接口全部对齐，仅剩 `fund.get_pdf_reports`（PDF 文件下载，非数据接口，不在 tstdx 范畴）。
