# Sina Provider 接口与 Channel 契约

> Provider ID: `sina`  
> API compatibility selector: `source="sina"`  
> Role: `auxiliary_live / auxiliary_info`

## 1. 定位

新浪是独立行情/资讯 Provider。它与 TDX 部分行情能力重叠，同时提供联想、板块、新闻、资金流等特色数据。它不是 TDX 自动备用 Provider。

## 2. Channel

```text
quote
history_kline
suggest
industry_board
board_list
board_member
fund_flow
news
```

Direct API：

```python
md.sina.quotes(symbols, market="cn_a")
md.sina.hk_quotes(symbols)
md.sina.history_kline(symbol, period="day", count=320)
md.sina.suggest("茅台")
md.sina.industry_board(...)
md.sina.board_list(...)
md.sina.board_members(board)
md.sina.fund_flow(symbol)
md.sina.news(symbol)
```

## 3. Unified API

共同语义能力：

```python
md.quotes(symbols, provider="sina")
md.bars(symbol, provider="sina")
```

`bars` 走 `getKLineData` 端点：单次 `count` 实测 **1970 根封顶**（`datalen` 1971 起整体返回
JSON 裸 `null`），超过 `SINA_KLINE_MAX`（`atst/sources/limits.py`）的请求会被钳到上限并告警；
该端点无日期游标、不能分段，需要更长历史请换 `eastmoney` / `tencent`。该端点只提供原始价，
**不支持复权**（`adjust` 非空显式报错）。

`suggest/boards/fund_flow/news` 优先 Direct API。

新浪 `quotes()` 的 `hq_str` 快照不包含 PE/PB/市值字段；`all_market(node="hs_a")`
使用另一个行情中心端点，返回 `per`、`pb`、`mktcap`、`nmc`、`turnoverratio`，其中
`mktcap`/`nmc` 已换算为元。它适合一次获取全市场当日摘要，不是个股历史估值接口。
需要历史 PE/PB/总市值/流通市值/股数时使用 `valuation_history`（Eastmoney）或
百度 `baidu_valuation_history`（总市值与 PE/PB 等，但无流通股数序列）。

### 3.1 全市场行情（`all_market`）

端点：`Market_Center.getHQNodeData`（域名 `vip.stock.finance.sina.com.cn`，路径
`quotes_service/api/json_v2.php`，GBK，必须带 Referer）。一页即一份行情摘要，
不需要先拉代码表再逐批 `quotes()`。

- 参数：`node`（`hs_a` 沪深 A / `hs_b` B 股 / `sh_a` / `sz_a` / `kcb` / `cyb`；
  `bjs` 实测返回空；`hs_a` 实测含 `bj` 前缀的北交所标的）、`page`、`num`（**有效上限
  100**：`num=101/200/500` 均只回 100 行且不报错）、`sort=symbol&asc=1`。2026-11-08
  实测节点总数（`Market_Center.getHQNodeStockCount?node=...`）：`hs_a` 5578、`hs_b`
  77、`sh_a` 2317、`sz_a` 2906、`kcb` 618、`cyb` 1411。
- 分页：`fetch_all` 先探测第 1 页，再并发拉后续页；短页/空页终止。`page_size`
  超 100 在入口钳制并告警 `web_sina_page_size_clamped`；某页 `symbol` 缺失过半即判
  结构下线（`SourceDeprecated`），少量缺字段告警 `web_sina_market_field_missing`；
  分页失败告警 `web_sina_pages_missing`。上游不可达抛 `WebSourceError`，不返回空
  列表冒充全市场。
- 前置计数校验：拉分页前**额外请求一次** `getHQNodeStockCount?node=...` 拿总数
  （只请求一次，失败不重试，不在每个分页里重复）。用途是把三种此前同形的空结果
  区分开：① 计数为 0，或计数与首页同时为空数组（如 `bjs`）→ 节点确实没有标的，
  发 `web_sina_node_empty` 并返回空列表；② 计数 > 0 但首页为空 → 上游异常回空，
  抛 `SourceDeprecated`；③ 正常取完。页数由总数推导（向上取整），实际条数与
  总数不符发 `web_sina_market_count_mismatch`；`max_pages` 不足时 `web_sina_page_limit`
  给出确定结论（写明缺多少条），不再只是"可能截断"。计数接口失败/返回非数字/
  结构异常 → 发 `web_sina_count_unavailable` 并降级为原分页探测（含"末页仍为满页"
  的推断式告警），能力整体不失败。
- 开销与关闭：全市场比纯分页多 **1 次** HTTP 请求（该计数请求）。没有关闭开关；
  若不需要这次校验，请改用 `quotes()` 按自己的代码表批量查询，不要绕开
  `all_market()` 再期待它不做计数。
- 与 `quotes()` 的关系：需要**给定代码列表**的实时五档/量额时用 `quotes()`
  （`hq.sinajs.cn`，无 PE/PB/市值）；需要**全市场/板块快照**（含
  `per/pb/mktcap/nmc/turnoverratio`）时用 `all_market()`。两者端点不同，不要
  交叉补齐字段。
- 其他源：`tencent` 也有 `all_market`（排行枚举 + `qt.gtimg.cn` 批量），统一入口
  `WebQuoteSession.all_market(provider=...)`；规划层默认走 `derived` 路由。

## 4. Referer / Rate

新浪部分接口要求 Referer。该事实属于 Provider/Channel metadata，由 Provider runtime 统一装配。

## 5. Freshness

quote/history/news/fund_flow 使用各自 freshness profile；新闻和板块更新频率不能机械套用秒级行情 freshness。

## 6. Errors

错误面只有 `docs/providers/README.md` §12 那一棵树：能力/`currentness` 口径与新浪 channel 不匹配时是规划期的 `ValidationError`(E1010)（`history_kline`/`news`/`fund_flow` 等非 live channel 上 `currentness='live'` 当场拒绝），上游失败保持 `WebSourceError`(E7xxx) 家族原异常（429 → `WebRateLimited`，反爬 → `AntiSpiderBlocked`，接口下线或返回空 → `SourceDeprecated`，读超时 → `ReadTimeout` E2030），context 带 `provider=sina`/`channel`/`capability`。

## 7. Provider-specific Model

新闻、资金流、板块结构使用 Sina-specific model，不塞入 Quote.extra。

## 8. Conformance

验证 Referer、单位、Market、时间/字符集、限流、session reuse、无跨 Provider attempt。
