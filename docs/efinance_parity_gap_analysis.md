# tstdx ↔ efinance 接口能力对标与补全报告

> 目标：对标开源库 [`Micro-sheep/efinance`](https://github.com/Micro-sheep/efinance) 的
> **stock / fund / futures / bond** 四大模块全部公开函数，补全 tstdx 缺失的数据接口，
> 使 tstdx 在「数据接口能力」层面对齐并覆盖 efinance。
>
> 生成时间：2026-09-09
> 新增门面方法：`UnifiedQuoteAPI` 上新增 **21** 个 efinance 对标方法（详见下文）。

---

## 1. 结论速览

| 模块 | efinance 公开函数数 | 此前 tstdx 已覆盖 | 本次新增 | 剩余未覆盖（说明） |
|------|------------------|----------------|----------|------------------|
| stock | 18 | 14 | **4** | 0（全部覆盖） |
| fund | 14 | 7 | **7** | 2（见 §6 备注） |
| futures | 5 | 0 | **4** | 0（等价覆盖） |
| bond | 9 | 0 | **6** | 1（见 §6 备注） |
| **合计** | **46** | 21 | **21** | 3（1 项超出数据接口范畴） |

新增代码文件：
- `tstdx/web/efinance_fund.py` —— 天天基金移动端源（`FundMobSource`）
- `tstdx/web/efinance_deriv.py` —— 期货 / 债券 push2 源（`EastmoneyFuturesSource` / `EastmoneyBondSource`）
- `tstdx/web/_facade_mixin_efinance.py` —— 三个门面 Mixin
- `tstdx/facade/api.py` —— 新增 21 个 `UnifiedQuoteAPI` 方法
- `tstdx/web/facade.py` —— `WebQuoteSession` 继承三个 Mixin

新增测试：
- `tests/web/test_efinance_fund.py`（7 用例）
- `tests/web/test_efinance_deriv.py`（9 用例）
- `tests/web/test_efinance_facade.py`（11 用例，端到端离线集成）
- 全部离线（FakeHttpClient + 罐头响应），不发起真实 HTTP。

---

## 2. 股票模块（stock，18）

| efinance 函数 | tstdx 对应 | 状态 |
|---------------|-----------|------|
| `get_base_info` / `_single` / `_muliti` | **`stock_base_info(codes)`**（新增，批量） | ✅ 新增 |
| `get_quote_history` | `history(symbol, period, count, adjust)` | ✅ 已有 |
| `get_realtime_quotes` | `quotes(symbols)` | ✅ 已有 |
| `get_latest_quote` | `quotes(symbols)`（取实时涨幅） | ✅ 已有 |
| `get_quote_snapshot` | `snapshot(symbol)` | ✅ 已有 |
| `get_history_bill` | `big_order_flow(symbol)`（大单净流入，近似） | ✅ 已有（近似） |
| `get_today_bill` | `big_order_flow(symbol)`（当日，近似） | ✅ 已有（近似） |
| `get_top10_stock_holder_info` | `shareholders(symbol)`（十大流通股东） | ✅ 已有 |
| `get_latest_holder_number` | `holder_num(symbol)` | ✅ 已有 |
| `get_all_report_dates` | **`stock_report_dates(limit)`**（新增） | ✅ 新增 |
| `get_all_company_performance` | **`stock_all_performance(report_date)`**（新增） | ✅ 新增 |
| `get_daily_billboard` | `longhu(...)`（龙虎榜） | ✅ 已有 |
| `get_members` | `index_constituents(index)` | ✅ 已有 |
| `get_latest_ipo_info` | **`ipo_review(page, size)`**（新增，审核进度）+ `ipo_calendar` | ✅ 新增 |
| `get_deal_detail` | `ticks(symbol)`（逐笔成交） | ✅ 已有 |
| `get_belong_board` | `stock_boards(symbol)`（所属板块） | ✅ 已有 |

**新增（4）**：`stock_base_info`、`stock_report_dates`、`stock_all_performance`、`ipo_review`。
> 注：`stock_base_info` 走 `EastmoneyProfileSource`（PE/PB/行业/总市值/流通市值，金额单位元，PE/PB ×100 还原）。

---

## 3. 基金模块（fund，14）

| efinance 函数 | tstdx 对应 | 状态 |
|---------------|-----------|------|
| `get_quote_history` | `fund_nav_history(code, ...)` | ✅ 已有 |
| `get_quote_history_multi` | `fund_nav_history`（多只循环） | ✅ 已有 |
| `get_realtime_increase_rate` | `fund_estimate(code)` | ✅ 已有 |
| `get_fund_codes` | `fund_list()` | ✅ 已有 |
| `get_base_info` / `_single` | **`fund_base_info(code)`**（新增，单数） | ✅ 新增 |
| `get_fund_manager` | **`fund_manager(code)`**（新增） | ✅ 新增 |
| `get_invest_position` | **`fund_holdings(code, dates)`**（新增） | ✅ 新增 |
| `get_period_change` | **`fund_period_change(code)`**（新增） | ✅ 新增 |
| `get_public_dates` | **`fund_public_dates(code)`**（新增） | ✅ 新增 |
| `get_types_percentage` | **`fund_asset_allocation(code, dates)`**（新增） | ✅ 新增 |
| `get_industry_distribution` | **`fund_industry_distribution(code, dates)`**（新增） | ✅ 新增 |
| `get_base_info_muliti` | （批量单数循环即可；未单独建方法） | ⚠️ 见 §6 |
| `get_pdf_reports` | （下载 PDF 到本地，非数据接口） | ⛔ 不在范畴 |

**新增（7）**：`fund_base_info`、`fund_manager`、`fund_holdings`、`fund_period_change`、
`fund_asset_allocation`、`fund_industry_distribution`、`fund_public_dates`。
> 后端：天天基金移动端 `fundmobapi.eastmoney.com/FundMNewApi/*`；基金经理走
> `fundf10.eastmoney.com/jjjl_{code}.html` 的 HTML best-effort 解析（页面改版时容错返回 `None`）。

---

## 4. 期货模块（futures，5）

> 后端说明：TDX 7727 扩展行情服务（期货/商品期权）在 2026-09 实测主站池整体不可达，
> 本模块改用**东财 push2 / push2his** 作为 Web 降级通路。

| efinance 函数 | tstdx 对应 | 状态 |
|---------------|-----------|------|
| `get_futures_base_info` | **`futures_base_info()`**（新增，全市场） | ✅ 新增 |
| `get_realtime_quotes` | **`futures_realtime(quote_id)`**（新增，单合约） | ✅ 新增 |
| `get_quote_history` | **`futures_kline(quote_id, period, count, adjust)`**（新增） | ✅ 新增 |
| `get_deal_detail` | **`futures_trades(quote_id, max_count)`**（新增） | ✅ 新增 |

**新增（4）**：`futures_base_info`、`futures_realtime`、`futures_kline`、`futures_trades`。
> 期货 secid 沿用东财格式 `市场段.合约`（郑商所 `115`/大商所 `114`/上期所 `113`/中金所 `8`/上期能源 `142`），
> 与 efinance 行情 ID（`115.ZCM`）一致可透传。价格字段统一 ÷100 还原到全局契约（元）。

---

## 5. 债券模块（bond，9）

> 债券与 A 股同处沪/深市场，secid 沿用 `1.代码` / `0.代码`；资金流复用 `EastmoneyFundFlowSource`。

| efinance 函数 | tstdx 对应 | 状态 |
|---------------|-----------|------|
| `get_base_info` / `_single` / `_multi` | **`bond_base_info(codes)`**（新增，批量） | ✅ 新增 |
| `get_realtime_quotes` | **`bond_realtime(codes)`**（新增） | ✅ 新增 |
| `get_quote_history` | **`bond_kline(code, period, count, adjust)`**（新增） | ✅ 新增 |
| `get_history_bill` | **`bond_history_bill(code, count)`**（新增） | ✅ 新增 |
| `get_today_bill` | **`bond_today_bill(code)`**（新增） | ✅ 新增 |
| `get_deal_detail` | **`bond_trades(code, max_count)`**（新增） | ✅ 新增 |
| `get_all_base_info` | （全债券枚举，未单独建方法） | ⚠️ 见 §6 |

**新增（6）**：`bond_realtime`、`bond_base_info`、`bond_kline`、`bond_history_bill`、
`bond_today_bill`、`bond_trades`。

---

## 6. 剩余差异与后续项

| 项 | 说明 | 建议 |
|----|------|------|
| fund `get_base_info_muliti` | 批量基金基础信息。当前 `fund_base_info` 仅单数；批量可由调用方循环，或后续补 `fund_base_info_multi`。 | 低优先，按需补充 |
| bond `get_all_base_info` | 全市场债券基础信息枚举。可通过东财 `clist` 债券板块（`m:90` 类）拉全量，代价较高。 | 低优先，按需补充 |
| fund `get_pdf_reports` | 将基金 PDF 研报下载到本地文件系统。**非数据接口**，属文件 IO 能力，超出行情数据库范畴。 | 不实现（范畴外） |
| stock `get_history_bill` / `get_today_bill` | efinance 为个股「大单分档」分时流；tstdx 以 `big_order_flow`（日级大单净额）近似覆盖，粒度不同。 | 如需逐分钟大单明细，可后续扩展 |
| futures `get_realtime_quotes`（全市场） | 本次 `futures_realtime` 为单合约；全市场实时快照可由 `futures_base_info` 枚举后逐只拉取。 | 等价覆盖，可接受 |

---

## 7. 关键设计约定（与既有 tstdx 一致）

- **归一化契约**：价格字段 ×100（东财 push2 口径）统一 ÷100 还原到「元」；成交量统一「股」；金额「元」。
- **容错**：解析失败 / 接口下线统一抛 `SourceDeprecated`，便于上层降级与「接口已下线」检测（复用 `_EastmoneyJson._get_json` 的主机池 failover + 黑名单）。
- **financial 字段**：null 语义保留（不填 0），避免污染下游统计。
- **复用**：债券资金流复用既有 `EastmoneyFundFlowSource`；股票扩展复用既有 `corporate` / `profile` 源；期货/债券复用 `_EastmoneyJson` 主机池与重试内核。
- **离线可测**：全部新功能配 FakeHttpClient + 罐头 JSON/HTML 离线测试，覆盖字段映射、×100 还原、K 线解析、secid 透传。

---

## 8. 验证结果

```
tests/web/test_efinance_fund.py      7 passed
tests/web/test_efinance_deriv.py     9 passed
tests/web/test_efinance_facade.py   11 passed   （门面 → Mixin → 源 端到端）
------------------------------------------------------------------
全量 tests/web/（含 -m "not network"）                      全部通过，无回归
UnifiedQuoteAPI 21 个新方法 + WebQuoteSession 同签名方法    全部存在且可实例化
```
