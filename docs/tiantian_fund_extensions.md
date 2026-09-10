# 天天基金扩展接口：排行 / 快照 / 经理 / 公司 / 搜索

> 本文件记录 tstdx 在基金域的**第二轮扩展**：补齐 efinance 对标（7 端点）与
> `adapters_fund`（净值 / 估值 / 列表）之外的所有基金数据接口。
> 上一轮对标见 `docs/efinance_parity_gap_analysis.md`。

## 一、背景与结论

调研口径：上游 `Micro-sheep/efinance`、`tiantianlaolao/astock-data-toolkit`、
`kouchao/TiantianFundApi`（完整 39 端点清单）以及天天基金移动端
`fundmobapi.eastmoney.com/FundMNewApi` 抓包惯例。

**结论**：本轮新增 **3 个 Web 源 + 18 个 `UnifiedQuoteAPI` 门面方法**，
覆盖基金「选基 → 尽调 → 经理 → 公司」完整链路，并顺带修复两个真实缺口：

1. **实时估值替代** —— `adapters_fund.fund_estimate` 依赖的
   `fundgz.1234567.com.cn` 接口**已于 2026 年下线**（返回 404）。
   新增 `fund_snapshot` 走官方 `FundMNFInfo`，支持多代码**一次批量**拉取
   （旧接口只能单只请求）。
2. **基金经理去 HTML 化** —— `efinance_fund.fetch_manager` 依赖
   `fundf10.eastmoney.com/jjjl_{code}.html` 正则解析，页面改版即**静默失效**。
   新增 `fund_manager_list` 走移动端 JSON，字段稳定且信息更全。

## 二、新增门面方法一览

| # | 门面方法 | Web 源方法 | 上游端点 | 返回 |
|---|---|---|---|---|
| 1 | `fund_rank()` | `FundMobRankSource.fetch_rank` | `FundMNRank` | `{"total","page","size","rows"}` |
| 2 | `fund_snapshot(codes)` | `fetch_snapshot` | `FundMNFInfo` | `list[dict]` |
| 3 | `fund_nav_history_mob(code)` | `fetch_nav_history` | `FundMNHisNetList` | `list[dict]` |
| 4 | `fund_detail(code)` | `fetch_detail` | `FundMNDetailInformation` | `dict` |
| 5 | `fund_rating(code)` | `fetch_rating` | `FundGradeDetail` | `list[dict]` |
| 6 | `fund_yield_curve(code)` | `fetch_yield_curve` | `FundVPageAcc` | `list[dict]` |
| 7 | `fund_rank_trend(code)` | `fetch_rank_trend` | `FundRankDiagram` | `list[dict]` |
| 8 | `fund_manager_list(code)` | `FundManagerSource.fetch_list` | `FundMNMangerList` | `list[dict]` |
| 9 | `fund_manager_profile(mgrid)` | `fetch_profile` | `FundMSNMangerInfo` | `dict` |
| 10 | `fund_manager_yield(mgrid)` | `fetch_yield` | `FundMSNMangerAcc` | `list[dict]` |
| 11 | `fund_manager_eval(mgrid)` | `fetch_eval` | `FundMSNMangerPerEval` | `dict` |
| 12 | `fund_manager_style(mgrid)` | `fetch_style` | `FundMSNMangerPosMark` | `dict` |
| 13 | `fund_companies()` | `FundCompanySource.fetch_companies` | `FundMApi/FundCompanyBaseList.ashx` | `list[dict]` |
| 14 | `fund_company_archives(cc)` | `fetch_archives` | `CompanyApi2?companyarchives` | `dict` |
| 15 | `fund_company_funds(cc)` | `fetch_funds` | `CompanyApi2?fundlist` | `list[dict]` |
| 16 | `fund_company_scale(cc)` | `fetch_scale_change` | `CompanyApi2?companygmbd` | `list[dict]` |
| 17 | `fund_company_base_info(cc)` | `fetch_base_info` | `CompanyApi2?fundcompanybaseinfo` | `dict` |
| 18 | `fund_search(key)` | `search_funds` | `fundts.eastmoney.com/.../fundinfobynohigh` | `{"total","page","size","rows"}` |

## 三、代码结构

```
tstdx/web/
├── _mob_fund.py             # 新增：移动端共享工具（设备指纹 / 公共参数 / 归一化）
├── fund_rank.py             # 新增：排行 / 快照 / 净值 / 详情 / 评级 / 走势（7 方法）
├── fund_manager.py          # 新增：基金经理（5 方法）
├── fund_company.py          # 新增：公司 / 搜索（6 方法）
├── _facade_mixin_fund_v2.py # 新增：3 个 Session Mixin
├── facade.py                # 修改：WebQuoteSession 追加 3 个 Mixin
└── efinance_fund.py         # 未改：efinance 对标 7 端点保持原样
tstdx/facade/api.py          # 修改：+18 门面方法
tests/web/test_fund_v2.py    # 新增：50 例离线测试
```

### `_mob_fund` 共享工具（避免三源重复）

三个源共用同一设备指纹、公共参数串与容错口径，抽到独立模块：

| 符号 | 作用 |
|---|---|
| `MOB_BASE` / `DEVICE` / `MOB_COMMON` | 基址 / 设备指纹 / 公共参数串 |
| `mob_headers()` | 移动端 UA + Referer |
| `mob_get_json(request_text, path, base=)` | 统一 GET + JSON 解析 + 失败转 `SourceDeprecated`；`base` 参数支持 `FundMApi` / `fundts` 等非 `FundMNewApi` host |
| `mob_rows(payload, key)` | 兼容 `Datas: [...]` / `Datas: {"fundStocks": [...]}` |
| `mob_rows_any(payload, *keys)` | 多键名兼容（`Datas` vs `data`） |
| `apply_fields(row, fields)` | 按 `输出键 -> (上游键, 类型)` 映射表归一化一行 |

`apply_fields` 的类型标记：`f` = `num_f`（默认 0.0）、`i` = `num_i`（默认 0）、
其他 = `s`（None → 空串）。**缺失字段统一归默认值，语义与
`efinance_fund` 一致**（数值型不给 `None`，避免下游 `.sum()` / `.mean()` 崩）。

## 四、字段契约

| 域 | 关键字段 | 说明 |
|---|---|---|
| 排行 | `code` `name` `company` `fund_type` `establish_date` `day_pct` `unit_nav` `accum_nav` `return_1w` `return_1m` `return_3m` `return_6m` `return_1y` `return_2y` `return_3y` `return_total` `scale` `risk_level` | 收益率均为百分数原值（`50.0` = 50.00%） |
| 快照 | `unit_nav` `accum_nav` `nav_pct` **`est_nav`** **`est_pct`** **`est_time`** `nav_date` `has_redpacket` | 加粗三项即盘中估算，替代已下线的 `fundgz` |
| 净值 | `date` `unit_nav` `pct_change` `accum_nav` `nav_type` `rate` `cum_return` | 比 `fund_nav_history`（`lsjz`）多 `nav_type` / `rate` / `cum_return` |
| 详情 | `risk_level` `risk_scale` `benchmark` `index_code` `index_name` `invest_target` `invest_strategy` `management_exp` `trust_exp` `sales_exp` | 选基尽调核心字段 |
| 评级 | `date` `rating_ht` `rating_zs` `rating_sz3` `rating_ja` | 天天基金 / 招商 / 上证 / 嘉实 |
| 收益走势 | `date` `fund_yield` `index_yield` `peer_yield` `benchmark_quote` | 基金 vs 指数 vs 同类，超额收益分析基础 |
| 排名走势 | `date` `rank` `total` | 每日同类排名与总数 |
| 经理列表 | `mgrid` `name` `fund_code` `days` `start_date` `end_date` `nav_growth` `is_in_office` | `is_in_office` 区分现任 / 离任 |
| 经理档案 | `resume` `invest_method` `invest_idea` `total_days` `net_nav` `fund_count` `award_num` `max_nav_growth` `max_retra_1y` | — |
| 经理评价 | `sharp_1y` `sharp_3y` `max_ret_1y` `max_ret_3y` `win_pct_1y` `win_pct_3y` `stddev_1y` `stddev_3y` `hc_pct_*` `xp_pct_*` `bd_pct_*` | **量化经理打分卡直接可用** |
| 经理风格 | `pos_date` `holdings[]` `style` `sub_style[]` | 重仓股 / 风格标签 / 子风格分布 |
| 公司 | `company_id` `name` `name_abbrev` `pinyin` `fund_count` | `company_id` 是其余公司接口的入参 |
| 公司旗下基金 | 同排行字段 + `fee_1y` `fee_2y` `fee_3y` | 便于同公司横向比较 |
| 搜索 | `code` `name` `fund_type` `highlight` `abb_tname` | `highlight` 为命中片段 |

## 五、量化场景串联

```python
from tstdx import tstdx   # UnifiedQuoteAPI

api = tstdx

# 1) 选基：近1年收益 Top20 的股票型基金，过滤 4 级风险
page = api.fund_rank(fund_type=25, sort_column="SYL_1N", size=20, risk_level="4")

# 2) 批量快照：一次拿到净值 + 盘中估值（替代已下线的 fundgz）
snaps = api.fund_snapshot([r["code"] for r in page["rows"]])

# 3) 尽调：单只基金的风险等级 / 业绩基准 / 投资策略
detail = api.fund_detail("161725")

# 4) 超额收益：基金 vs 沪深300 vs 同类
curve = api.fund_yield_curve("161725", index_code="000300")

# 5) 经理打分卡：夏普 / 最大回撤 / 胜率
mgrs = api.fund_manager_list("161725")
live = [m for m in mgrs if m["is_in_office"] == "1"]
eval_ = api.fund_manager_eval(live[0]["mgrid"])
score = eval_["sharp_1y"] - abs(eval_["max_ret_1y"]) / 100

# 6) 公司维度：招商基金旗下全部基金 + 规模变动
comps = api.fund_companies()
zs = next(c for c in comps if c["company_id"] == "80084302")
funds = api.fund_company_funds(zs["company_id"], sort_field="SYL_Y")
scale = api.fund_company_scale(zs["company_id"])
```

## 六、best-effort 边界与失败约定

以下端点非 `FundMN*` 前缀，host 与 PascalCase 命名沿用移动端惯例，
属 **best-effort**；若线上返回 `ErrCode != 0` 或 `code != 0`，需重新抓包校准
（同 `EastmoneyIpoAuditSource` 的策略）：

- `FundGradeDetail` / `FundVPageAcc` / `FundRankDiagram` / `FundMNDetailInformation`
- `CompanyApi2` 全部 action
- `fundts.eastmoney.com` 搜索

统一失败约定：

1. **非 JSON / 非 JSON 对象** → 抛 `SourceDeprecated`（含 `path` 与 200 字符样本），
   触发下线检测与自动降级。
2. **业务空数据**（`Datas` 为 None / 空列表）→ **不抛错**，返回空列表或空键 dict，
   让上层自己判断（避免批量场景单只失败阻断整批）。
3. **字段缺失** → 归 `0.0` / `0` / `""`，不做 `None` 语义注入。

## 七、验证

- `tests/web/test_fund_v2.py`：**50 例全离线测试**（罐头 JSON + FakeHttpClient，
  零真实 HTTP），覆盖共享工具 5 例 + 排行源 10 例 + 经理源 7 例 + 公司源 9 例
  + 门面端到端链路 18 例 + 方法完整性 1 例。
- `tests/web/ -m "not network"`：**494 例，EXIT=0，无回归**。
- 门面接线校验：`UnifiedQuoteAPI` 与 `WebQuoteSession` 均具备全部 18 个方法。

## 八、未做项

| 项 | 原因 |
|---|---|
| `fundThemeList`（热门主题排行） | 端点名未在移动端抓包中确认，host 不确定；`fund_rank` 已支持 `topic` 过滤参数，主题 code 可从 `fund_company_base_info` 的 `topics` 间接获得 |
| `FundMNNetNewList` / `fundNetList`（按类型列表） | 与 `fund_rank(fund_type=N)` 能力重叠，后者支持排序与分页，无需另建 |
| `fundMNStopWatch`（基金简介） | 与 `fund_detail` 字段高度重叠（`FundMNDetailInformation` 是超集） |
| `fundVPageDiagram`（净值走势图） | 与 `fund_nav_history` / `fund_nav_history_mob` 数据同源，只是图表抽样 |
| `fundVarietieValuationDetail`（盘中估值曲线） | 需与 `fund_snapshot` 合并设计（估值曲线属高频轮询域，暂不做） |
| 股票域 `stockTrends2` / `stockKline` / `stockDetails` / `stockGet` | 属股票行情域，tstdx 已有独立 push2 实现，不应由基金源重复 |
