# 开源接口吸收分析（thsdk / levistock → atst）

> 2026-09 审计版。目标：吸收两个上游开源项目的**公开接口面与能力面**，
> 在 atst 自有架构内补齐高价值功能接口。
>
> **洁净室红线**：只吸收接口形态（方法语义 / 参数 / 返回结构），不复制任何
> 实现代码、抓包数据或私有签名算法；所有实现均为 atst 自有代码，
> `check_originality` 门禁保持全绿。

## 一、上游项目概览

| 项目 | 定位 | 接口形态 | 关键依赖 |
|------|------|---------|---------|
| [thsdk](https://github.com/panghu11033/thsdk)（MIT） | 同花顺 C 动态库封装，多市场行情 | `THS` 客户端 + 统一 `Response{success,error,data,extra}` + `.df` | 私有动态库（闭源） |
| [levistock](https://github.com/fleetinglife/levistock)（MIT） | A 股数据聚合（东财/财联社/同花顺/开盘红/i问财） | 模块级函数，返回裸 list/dict | requests + 各家 HTTP 端点（含私有 sign） |

## 二、能力对照矩阵

图例：✅ atst 已有等价能力；🆕 本轮新增；📋 规划（后续批次）；⛔ 不吸收（洁净室/可靠性原因）。

### 2.1 thsdk → atst

| thsdk 接口 | 语义 | atst 状态 | 落点 |
|-----------|------|-----------|------|
| `Response{success,error,data,extra}` + `.df` + `__bool__` | 统一响应形态 | 🆕 `facade.response.ApiResponse` + `UnifiedQuoteAPI.query()` | `atst/facade/response.py` |
| `search_symbols(pattern, needmarket)` | 统一证券搜索 | 🆕 `search_symbols()`（display 展示名 + market 过滤） | WebQuoteSession / UnifiedQuoteAPI |
| `complete_ths_code` | 代码补齐 | ✅ `suggest` 覆盖（返回 symbol 归一形态） | — |
| `klines(interval, adjust)` | K 线 | ✅ `bars` / `klines`（周期别名更全） | — |
| `intraday_data` / `min_snapshot` | 日内 / 历史分时 | ✅ `minute` / `minute_web` / `minute_history` | — |
| `tick_level1` / `tick_super_level1` | 逐笔成交 | ✅ `trades` / `ticks` / `trends` | — |
| `depth` / `order_book_ask/bid` | 五档盘口 | ✅ `Quote.bid/ask`（五档 Level 内嵌） | — |
| `call_auction` | 竞价快照 | ✅ `auction` / `auction_snapshot` | — |
| `corporate_action` | 权息资料 | 🆕 `corporate_action`（capital_changes 语义别名） | UnifiedQuoteAPI / HTTP `/corporate_action/{symbol}` |
| `block` / `block_constituents` / `ths_industry` / `ths_concept` | 板块族 | ✅ `industry_boards` / `board_list` / `board_members` / `em_boards` | — |
| `index_list` | 指数列表 | 🆕 `index_list()`（离线静态目录 + `index()` 行情） | WebQuoteSession / UnifiedQuoteAPI |
| `stock_*_lists`（cn/us/hk/bj/bond/fund/etf…） | 各市场证券列表 | ✅ `security_list` / `all_market` / `rank`（分市场 node） | — |
| `market_data_*`（cn/us/hk/uk/bond/fund/future/forex/index） | 通用市场查询 | ✅ `quotes`（多市场前缀）+ `globals` / `rates`；期货走 GoodsClient | — |
| `wencai_base` / `wencai_nlp` | 问财自然语言 | 🆕 `wencai()`（与 levistock 同能力面） | `atst/web/wencai.py` |
| `news` | 资讯 | ✅ `news`（新浪个股新闻，分页/去重/标签） | — |
| `ipo_today` / `ipo_wait` | IPO 数据 | 📋 规划（东财 datacenter，需 live 端点验证） | — |
| `big_order_flow` | 大单流向 | ✅ `big_order_flow`（fund_flow 单只五档分档明细语义别名） | — |
| `call_auction_anomaly` | 竞价异动 | 📋 规划 | — |
| `option_data` | 期权资料 | ✅ OptionClient / GoodsClient | — |
| `help` | 接口帮助 | ✅ docstring + docs/ | — |

### 2.2 levistock → atst

| levistock 接口 | 语义 | atst 状态 | 落点 |
|---------------|------|-----------|------|
| `stock_strategy_wencai` | 问财自然语言选股 | 🆕 `wencai()`（见上） | `atst/web/wencai.py` |
| `market_emotion_cls/kph` | 市场情绪 | ✅ `market_breadth`（涨跌宽度）+ `limit_up_ladder`（连板高度/炸板率）聚合等价 | `web/market_stats.py` |
| `market_wind_cls` / `market_mainline_cls` | 风偏 / 主线（财联社） | ⛔ 端点带私有 sign 算法，复制即违反洁净室 | — |
| `sector_em` / `sector_stocks_em` | 东财板块 / 成分 | ✅ `em_boards` / `em_board_members` | — |
| `sector_stock_belong_em` | 个股所属板块 | ✅ `stock_boards`（东财 slist spt=3，实测验证） | — |
| `sector_heat` / `sector_rotation` | 板块热度 / 轮动（财联社） | ✅ `hot_boards` / `board_rank` 能力近似 | — |
| `stock_zt_pool_em` / `stock_dt_pool_em` / `stock_yesterday_zt_em` | 涨停/跌停/昨涨停池 | ✅ `limit_pool`（三池合一） | — |
| `stock_changes_em` | 盘中异动 | ✅ `stock_changes`（push2ex getAllStockChanges，dpt=wzchanges，实测验证） | — |
| `stock_hot_rank_ths` | 个股人气榜 | ✅ `hot_rank`（东财股吧 emappdata；ths 旧端点 404 已下线） | — |
| `stock_kline_cls` / `stock_timeline_cls` | K 线 / 分时（财联社） | ✅ `klines` / `minute`（腾讯/东财通道） | — |
| `news_telegraph_cls` | 财联社电报 | ✅ `news`（新浪资讯，能力等价） | — |
| `limit_up_his_kph` 等复盘族 | 打板历史（开盘红） | ⛔ 私有端点 + sign | — |
| `is_trade_day` / `get_trade_days` | 交易日历 | ✅ `TradingCalendar`（2024-2026 + 联网更新，更强） | `domain/calendar.py` |

## 三、本轮新增（🆕 明细）

### 3.1 统一响应形态 `ApiResponse`
- 新模块 `atst/facade/response.py`：`success / error / data / extra / code`
  五要素 + `__bool__` + `to_dict()` + 惰性 `.df`（pandas 可选，零硬依赖）。
- `ok()` / `err()` / `from_result()` / `wrap()` 工厂；`TdxError` 自动转
  `success=False`（保留错误码与 context）。
- 统一入口 `UnifiedQuoteAPI.query(method, *args, **kw)`：任意门面方法的
  「永不抛异常」响应化边界；HTTP 网关同步暴露 `POST /query`。
- **语义要点**：`success=True` 且 `data=[]` 是合法状态（成功但空），
  与请求失败严格区分。

### 3.2 i问财自然语言选股 `wencai`
- 新源 `atst/web/wencai.py`（`WencaiSource`），注册名 `wencai`，
  capability `wencai`，限流 1 req/s。
- **cookie 由调用方持有**（参数或 `ATST_WENCAI_COOKIE`），atst 不依赖
  任何第三方 cookie 中继服务（上游 levistock 依赖
  `api.levizhang.com/getCookie`——单点不可靠，不吸收该设计）。
- 返回 title+rows zip 后的 `list[dict]`，可直接 `.df`；解析为纯函数，罐头可测。

### 3.3 统一证券搜索 `search_symbols`
- `WebQuoteSession.search_symbols(pattern, limit=, market=)`：
  在 `suggest` 之上补齐 `display`（"名称(代码)"）与市场过滤语义，
  作为「搜代码 → 行情 / K 线 / 基本面」统一入口。
- `UnifiedQuoteAPI.search_symbols` 同实现透出。

### 3.4 权息资料别名 `corporate_action` 与指数目录 `index_list`
- `UnifiedQuoteAPI.corporate_action(symbol)`：capital_changes（TDX 0x000F
  股本变迁）的「公司行为」语义别名，便于跨库迁移对接。
- `index_list()`：常用指数离线目录（名称 + 带市场代码），与 `index()` 行情互补。

### 3.5 HTTP 网关新端点
| 端点 | 说明 |
|------|------|
| `GET /search?pattern=&limit=&market=` | 统一证券搜索 |
| `GET /index_list` | 指数目录 |
| `GET /wencai?query=&page=&limit=` | 问财选股（需服务端配置 cookie） |
| `GET /corporate_action/{symbol}` | 权息资料 |
| `POST /query` | 统一响应形态调用（success/error/data/extra） |

## 四、后续批次路线图（📋）

按价值排序：
1. ~~**个股所属板块**~~ ✅ 已交付（`stock_boards`，东财 slist spt=3，实测验证）
2. ~~**盘中异动**~~ ✅ 已交付（`stock_changes`，push2ex **getAllStockChanges**
   + `dpt=wzchanges`——此前 rc=102 系方法名/参数错误，经页面公开 JS 提取
   端点事实后实测验证，16 类异动枚举随源暴露）
3. ~~**IPO 日历**~~ ✅ 已交付（`ipo_calendar`，datacenter `RPTA_APP_IPOAPPLY`，实测验证）
4. ~~**大单流向**~~ ✅ 已交付（`big_order_flow`，fund_flow 单只语义别名）
5. ~~**Golden origin 分级 + L1 真实样本门禁**~~ ✅ 已交付
   （`atst/tools/golden_audit.py` + CI `golden-gate` job + `make audit-golden`；
   当前基线：500 样本 real 30 / synthetic 470，L1 verified 四命令全部具备
   real 样本，硬门禁通过；capture ctx 已补记 market/code 维度）
6. ~~**个股人气榜**~~ ✅ 已交付（`hot_rank`，东财股吧 emappdata stockrank；
   同花顺旧端点实测 404 已下线，不采用）
7. **0x44E/0x052D 真实样本补录**（交易时段外跑 `capture --plan core/kline`，
   消除 market 维度缺口）

## 五、门禁与验证

- 全量 pytest 全绿（含本轮新增 `tests/facade/test_response.py`、
  `tests/web/test_wencai.py`、`tests/web/test_search_symbols.py`）。
- registry 三方一致性门禁扩展：`wencai` capability ↔ facade 方法映射。
- `check_originality` 0 suspicious（未引入任何上游实现/指纹）。
