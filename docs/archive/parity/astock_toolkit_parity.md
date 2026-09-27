# astock-data-toolkit 对标补全 — 覆盖与缺口分析

> **归档说明（2026-09-23）**：本文是当时的对标/审计快照，**不是现行契约**。文中以现在时出现的 `UnifiedQuoteAPI` 统一门面（包括「新增 N 个门面方法」一类清单与给下游的校验指令）已随 v16 Phase 2 物理删除；今天的对外接口面是 `atst.client.TdxClient` / `Client` 与 capability 目录，口径见 [interfaces.md](../../api/interfaces.md) 与 [ARCHITECTURE.md](../../ARCHITECTURE.md)。本文的点位数、方法名与端点清单按原文留存而不逐条订正 —— 归档负责说明当时为什么这么做，不负责说明现在怎么用。

> 对标对象：<https://github.com/tiantianlaolao/astock-data-toolkit>
> （A 股全市场数据本地化工具箱：行情 / 估值 / 财务 / 分红 / 公告 / 增减持）
> 目标：延续 efinance 对标工作，补齐 atst 此前缺失的「基本面衍生」数据接口。

## 一、工具箱数据域 → atst 状态总览

| 工具箱数据域 | 工具箱后端 | atst 对应接口 | 状态 |
|---|---|---|---|
| 全 A 股名单 (`stock_list`) | akshare | `security_list` / `security_list_all` | ✅ 已覆盖 |
| 指数日线 (`index_daily`) | akshare | `klines(index)` | ✅ 已覆盖 |
| 个股日线 OHLCV（前复权） | 腾讯 | `bars` | ✅ 已覆盖 |
| **估值** (`valuation_daily`: pe_ttm/pb/ps_ttm/pcf_ttm/total_share/total_mv) | baostock + 巨潮 | `stock_valuation`（东财 `RPT_VALUEASSESS_DET`） | 🆕 新增 |
| 财务季报 (`financial_quarterly`: ROE/营收/净利润) | 新浪 | `finance` / `stock_all_performance` | ✅ 已覆盖 |
| **财务摘要（28 指标）** | 新浪 abstract | `financial_abstract`（东财 `RPT_F10_FINANCE_MAIN`） | 🆕 新增 |
| **分红送转** (`dividend_history`) | akshare | `dividend_history`（东财 `RPT_SHAREBONUS_DET`，已验证） | 🆕 新增 |
| **公告** (`info_archive.db`) | 巨潮 | `announcements`（东财 `np-anotice`，对接既有 `EastmoneyNoticeSource`） | 🆕 接入门面 |
| **股东增减持** (`holder_changes`) | 三所官方披露 | `holder_changes`（东财 `RPT_CAPITAL_PARTICIPATION_DET`） | 🆕 新增 |
| 分钟线 5min / 1min | 通达信 pytdx | `minute` / `minute_history` / `minute_klines`（TDX 核心路径） | ✅ 已覆盖 |
| 集合竞价（开盘/收盘） | 东财 clist_delay | `auction` | ✅ 已覆盖 |
| 股本变动 | 巨潮 | `capital_changes` | ✅ 已覆盖 |

## 二、本次新增接口（5 个 `UnifiedQuoteAPI` 方法）

| 方法 | 后端报表 | 返回字段 | 置信度 |
|---|---|---|---|
| `dividend_history(symbol)` | `RPT_SHAREBONUS_DET` | code/name/report_date/bonus_shares_per_10/transfer_shares_per_10/cash_dividend_per_10/ex_dividend_date/record_date/dividend_date/progress | ✅ 报表名已实测可用 |
| `stock_valuation(symbol)` | `RPT_VALUEASSESS_DET` | code/name/report_date/pe_ttm/pb/ps_ttm/pcf_ttm/total_share/total_mv | ⚠️ best-effort |
| `holder_changes(symbol)` | `RPT_CAPITAL_PARTICIPATION_DET` | code/name/person/position/actor/relation/change_shares/avg_price/shares_after/change_ratio/reason/change_date/disclosure_date | ⚠️ best-effort |
| `financial_abstract(symbol)` | `RPT_F10_FINANCE_MAIN` | code/name/report_date/eps/roe/bps/revenue/net_profit/revenue_yoy/net_profit_yoy/gross_margin/debt_ratio | ⚠️ best-effort |
| `announcements(symbols)` | `np-anotice-stock` | art_code/title/notice_date/display_time/categories/codes | ✅ 复用既有源 |

## 三、实现要点（与库内既有约定一致）

- 新源统一继承 `EastmoneyDataCenterSource` / `EastmoneyNoticeSource`，复用主机池
  failover + 黑名单 + 失败计数；解析失败统一抛 `SourceDeprecated`，触发下线检测。
- 数值字段容错：`None`/空/非数 → `None`（保留 null 语义，不填 0）；日期/进度保留字符串。
- 分红比例字段显式标注 `_per_10`（每 10 股口径，与东财报表一致），避免与库内
  每股口径混淆。
- best-effort 报表名若服务端返回 `code=9501`（报表配置不存在），需重新抓包校准——
  与既有 `ipo_review`（`RPT_IPO_AUDIT`）策略一致。

## 四、与工具箱实现的差异（已知取舍）

1. **估值粒度**：工具箱为 baostock+巨潮日频序列（`total_mv = close × total_share`，
   逐日）；atst 东财版为**季度报表快照**（取首项即最新估值）。如需日频序列，
   需另接 baostock 后端（非东财体系），可后续扩展。
2. **增减持源**：工具箱直连**三所官方披露 API**（上交所/深交所/北交所，最权威、
   含北交所）；atst 用东财聚合接口（单入口、免反爬），字段以「股东/董监高增减持」
   为主。如需三所原始明细（含 `actor`/`relation` 等），可后续补交易所 scraper。
3. **财务摘要**：工具箱为**新浪 28 指标**（经 akshare）；atst 用东财**主要指标**
   精简版（字段略少但稳定）。如需完整 28 指标，可后续接新浪 `getFinanceReport2022`
   端点（需 UA + 限速，参考工具箱踩坑实录 #06）。
4. **公告**：工具箱落巨潮全量库（约 630 万条，需翻页终止判定 `hasMore` 恒 true）；
   atst 提供按股票查询的便捷接口，未做全量回填。

## 五、测试

- `tests/web/test_astock_toolkit.py`：6 例全部离线（FakeHttpClient + 罐头 JSON），
  覆盖 4 个源解析 + 门面整链（`AstockToolkitMixin` monkeypatch `_shared_http`）。
- 全量 `tests/web/ -m "not network"` 无回归。
