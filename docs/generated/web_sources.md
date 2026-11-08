<!-- 本文件由 scripts/gen_docs.py 生成，请勿手改；改代码后运行 `make docs-gen`。 -->

# Web 源登记表

| 源 | 摘要 | Referer | Cookie | 默认限流 (req/s) | 能力 | 备注 |
|---|---|---|---|---|---|---|
| `baidu` | 百度财经（finance.pae.baidu.com selfselect） | 否 | 否 | 3 | `kline`、`minute`、`tick`、`quote`、`valuation_history` | 仅 A 股（stockType=ab）。坑：K 线 kline.volume 实为成交额(元)、kline.amount 实为成交量(手)，解析层须交换映射（volume=amount×100, amount=volume）；分时 amount 为含'万'字符串，优先用 oriAmount(元)。股市通历史图表可取总市值、PE(TTM/静态)、PB、市现率；值保留源端单位，不含股息率/流通股本历史。非官方接口，随时可能改版/下线；不提供 fund_flow（2026-05 起下线）。 |
| `boc` | 中国银行外汇牌价 | 否 | 否 | 1 | `fx` | 汇率数据，非行情 |
| `cninfo` | 巨潮资讯网法定披露公告（www.cninfo.com.cn） | 否 | 否 | 2 | `announcements` | POST 表单到 /new/hisAnnouncement/query；column 决定板块（szse 深主板+创业板 / sse 沪市 / hke 港交所）。webapi.cninfo.com.cn 那套（p_info3015 等）需 token，未采用。 |
| `corporate` | 基本面与公司行为（东财 datacenter-web 报表族） | 否 | 否 | 2 | `corporate`、`ipo` | F10/公告/研报/股东/大宗/解禁/业绩/IPO 申购日历/所属板块/停复牌/商誉/增发/股本结构/股东户数明细/机构席位/沪深港通成交/港股通持股/两融每日/宏观（货币供应量·贷款·社零·固投·工业增加值·存准·外储）；报表名错误返回 code=9501 |
| `eastmoney` | 东方财富行情（push2.eastmoney.com） | 否 | 否 | 5 | `quote`、`kline`、`rank`、`stock_boards` | 价格字段 f2/f3/f4 均为 ×100 整数；高频易触发 IP 封禁 |
| `fund` | 东财基金（天天基金：历史净值 / 实时估值 / 基金列表） | 否 | 否 | 3 | `fund_nav_history`、`fund_estimate`、`fund_list` | 数据型源（list[dict]/dict），不参与行情降级链；历史净值需 Referer http://fundf10.eastmoney.com/（适配器内置）；基金列表为全量 js 数组（约 2.7 万条，数 MB）。**实时估值已下线**：fundgz jsonp 接口 2026 年实测返回 404 页面，fetch_estimate 显式抛 SourceDeprecated。 |
| `fund_flow` | 资金流（东财 ulist.np 实时 / fflow/kline 历史） | 否 | 否 | 3 | `fund_flow` | 主力=超大单+大单；占比字段 f184/f69/f75/f81/f87 为 ×100 整数 |
| `global` | 外盘期货 / 外汇（腾讯 qt.gtimg.cn hf_ 前缀） | 否 | 否 | 5 | `quote`、`global` | 纽约原油/黄金、伦铜、布伦特原油等；字段为定长 14 列 |
| `hk` | 港股实时行情（qt.gtimg.cn/q=hkXXXXX） | 否 | 否 | 8 | `quote` | 港股通标的；成交量单位为股；价格/成交额以港元(HKD)计，extra.currency 标记原生币种，库不做汇率换算 |
| `hk_sina` | 新浪港股实时行情（hq.sinajs.cn hk 前缀） | 是 | 否 | 8 | `quote` | SinaHkSource 为 SinaSource 子类，复用新浪港股 hq_str 解析；其 source_name 返回 'hk'（归一化走 HK 的 identity），量为股、额为元(HKD)，extra.currency='HKD'。需带 Referer 同 SINA。 |
| `hot_rank` | 股吧个股人气榜（东财 emappdata stockrank，POST JSON） | 否 | 否 | 2 | `hot_rank` | 人气排名榜（rk 当前名次 + rc 较上期变动）；榜单仅含排名与代码，不含行情字段，如需行情请以返回 symbol 回查 quotes；globalId 由本库生成（uuid4），appId 沿用页面公开参数 |
| `index_cons` | 东财指数成分股（datacenter-web RPT_INDEX_TS_COMPONENT） | 否 | 否 | 3 | `index_constituents` | 数据型源（list[dict]），不参与行情降级链；TYPE 为指数族过滤（2026-09 与中证官网 XLS 交叉验证 5 族 jaccard=1.0）；单页上限 500，中证1000/中证2000 等大指数自动分页拉全量；weight 仅部分指数族提供（沪深300/上证50/中证500/科创50 有值）。 |
| `jsl` | 集思录（可转债 / 分级基金 / ETF） | 否 | 是 | 2 | `bond`、`etf` | 部分接口需登录 cookie；限速严格 |
| `kline` | 日 K 线（腾讯 ifzq.gtimg.cn / 东财 push2his） | 否 | 否 | 5 | `kline` | 日 K 线历史数据 |
| `limit_pool` | 涨停 / 跌停 / 炸板池（东财 push2ex） | 否 | 否 | 3 | `limit_pool` | date 为 YYYYMMDD；非交易日返回空池。本源输出为 dict 列表，解析层已按 1/1000 直接换算，不调用 normalize_quote |
| `longhu` | 龙虎榜每日个股榜（东财 datacenter-web RPT_DMSK_TS_STOCKNEW） | 否 | 否 | 2 | `longhu` | 主力净流入=超大单净+大单净(元)；TRADE_DATE 过滤须用完整'YYYY-MM-DD 00:00:00' 字面值，否则 9201 空数据；机构席位/营业部买卖子报表名已失效(9501)，本源仅取每日上榜个股汇总 |
| `margin` | 东财融资融券个股明细（datacenter-web RPTA_WEB_RZRQ_GGMX） | 否 | 否 | 3 | `margin` | 数据型源（list[dict]），不参与行情降级链；仅两融标的（非标的个股空列表）；DATE 倒序单页/翻页均可；金额单位元（RZYE 融资余额/RZMRE 融资买入/RZJME 融资净买/RQYE 融券余额/RZRQYE 两融余额），RZYEZB 为融资余额占流通市值比(%)。 |
| `market_stat` | 大盘统计（腾讯 qt.gtimg.cn s_ 前缀） | 否 | 否 | 5 | `quote`、`stat` | s_sh000001 形式；返回点数、涨跌额、涨跌幅、成交量(手)、成交额(万元) |
| `minute` | 当日分时（腾讯 web.ifzq.gtimg.cn minute/query） | 否 | 否 | 5 | `minute` | 当日 1 分钟分时（价格/均价/成交量）。接口累计量为「手」，解析层 MinuteSource 已 ×100 到股；量口径统一由解析层内联处理，normalizer 为 identity（不二次缩放）。 |
| `minute_kline` | 分钟 K 线（腾讯 ifzq.gtimg.cn mkline） | 否 | 否 | 5 | `kline`、`minute_kline` | 1/5/15/30/60 分钟 K 线，**仅 A 股**（港股/美股 mkline 返回空，直连会抛错）。港股/美股分钟 K 线走东财 push2his（EastmoneyHistoryKlineSource），WebQuoteSession.klines/minute_klines 已按市场自动路由。 |
| `news` | 新浪个股新闻（vCB_AllNewsStock.php） | 否 | 否 | 2 | `news` | 个股新闻列表（标题/链接/时间）；A 股覆盖最全，港股/美股可能为空；新浪新闻 JSON 接口已失效，改采稳定 HTML 资讯页 |
| `northbound` | 沪深港通资金（东财 kamt） | 否 | 否 | 2 | `fund_flow`、`northbound` | 金额单位为万元；status=3 表示已收盘 |
| `rank` | 通用排行（东财 push2 clist） | 否 | 否 | 3 | `rank` | fs 决定市场（m:0+t:6 深A 等），fid 决定排序字段；高频易断连 |
| `sina` | 新浪财经实时行情（hq.sinajs.cn；全市场走 Market_Center.getHQNodeData） | 是 | 否 | 8 | `quote`、`rank`、`all_market` | 必须带 Referer: https://finance.sina.com.cn；无 Referer 返回 403。全市场分页 num 有效上限 100（超 100 只回 100 行，已钳制告警）；node 实测 hs_a 5578/hs_b 77/sh_a 2317/sz_a 2906/kcb 618/cyb 1411，bjs 返回空。全市场拉分页前会额外请求一次 getHQNodeStockCount 拿总数做前置校验（只请求一次、失败不重试、无关闭开关）：计数为 0 或与首页同为空数组 → web_sina_node_empty；计数 > 0 但首页空 → SourceDeprecated；页数由总数推导，条数不符发 web_sina_market_count_mismatch；计数不可用则降级为分页探测并发 web_sina_count_unavailable |
| `sina_fund_flow` | 新浪资金流历史（个股 ssl_qsfx_zjlrqs / 板块 ssl_bkzj_zjlrqs） | 是 | 否 | 5 | `fund_flow`、`history` | 净额(元)/换手率(%)等均为原生单位，normalizer 为 identity；必须带 page/num/sort 三参数，缺省会返回全历史(约 1MB)；板块代码用 SinaIndustryBoardSource 的 new_xxx 体系（旧 hangye_ZLxx 体系已停更，最新数据停留在 2020 年） |
| `stock_changes` | 盘中异动池（东财 push2ex getAllStockChanges） | 否 | 否 | 2 | `stock_changes` | 20 类异动枚举（火箭发射/大笔买入/60日新高…，2026-09 实测验证）；输出为 dict 列表（time/code/name/change_type/metrics），metrics 为异动指标数值列表（含义随类型不同，不强行归一）；非交易时段返回空 allstock 为合法状态 |
| `suggest` | 证券代码联想搜索（新浪 smartbox） | 否 | 否 | 5 | `suggest` | 输入拼音/汉字/代码片段 → 候选证券列表 |
| `tencent` | 腾讯财经实时行情（qt.gtimg.cn） | 否 | 否 | 10 | `quote`、`kline`、`minute_kline`、`minute`、`all_market` | 成交量单位为手、成交额单位为万元，必须归一化 |
| `ths` | 同花顺涨停池 / 板块归属 / 人气榜（10jqka） | 否 | 否 | 2 | `limit_pool`、`theme_attribution`、`concept_members`、`hot_rank` | data.10jqka.com.cn/dataapi/limit_up/{limit_up_pool,block_top}（涨停池/板块归属）；dq.10jqka.com.cn/fuyao/hot_list_data（人气榜）。需 Referer。 |
| `ticks` | 逐笔成交明细（腾讯 stock.gtimg.cn detail） | 否 | 否 | 5 | `tick` | 每页条数以 atst.sources.ticks.TICKS_PER_PAGE 为单一事实源（勿在此复写具体数字）；p 为页码（0 起）。量单位手、额单位元 |
| `trends` | 当日分时成交（东财 push2his trends2） | 否 | 否 | 5 | `tick`、`minute` | 1 分钟粒度，含均价；iscr=0 不复权、ndays=1 当日 |
| `us` | 美股实时行情（qt.gtimg.cn/q=usXXXXX） | 否 | 否 | 8 | `quote` | 美股（NYSE/NASDAQ）；成交量单位为股；价格/成交额以美元(USD)计，extra.currency 标记原生币种，库不做汇率换算 |
| `wallstreet` | 华尔街见闻快讯（api-one.wallstcn.com） | 否 | 否 | 2 | `breaking_news` | GET /apiv1/content/lives?channel=global-channel；仅该频道稳定返回，macro-channel 实测为空，故只挂 breaking_news 一个能力。 |
| `wencai` | i问财自然语言选股（www.iwencai.com load-data） | 否 | 否 | 1 | `wencai` | 自然语言选股（如“连板3板以上”）；hexin-v cookie 由调用方注入（参数 cookie= 或环境变量 ATST_WENCAI_COOKIE），atst 不依赖任何第三方 cookie 中继服务；缺 cookie 时在 fetch 阶段抛 WebSourceError（构造不拦截，便于罐头测试）；返回 title+rows zip 后的 list[dict] |
