# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""HTTP Web 行情源注册表（§33）。

与 TDX 二进制协议族的关系
-------------------------
TDX 协议是**主路径**（低延迟、高吞吐、有主站 failover）；
HTTP Web 源是**并列的第二条取数路径**（零门槛、无需连主站，但有反爬与限流风险）。

两者获取的数据类型高度重叠，重叠的部分**不靠路由解决**：内核按 ``(provider,
channel)`` 三元组精确派发，查询指定哪个 Provider 就只走哪个 Provider，TDX 全主站
不可达不会把请求递给 Web 源，反之亦然（ADR-015）。v16 的 ``atst.sources.router``
自动路由/自动降级已随"零降级"裁决物理删除，这里提到它只为解释旧文档里为什么还有它。

最关键的坑：口径不一致
----------------------
===================  =============  =============  =============
源                   价格           成交量          成交额
===================  =============  =============  =============
新浪                 元（浮点）      **股**          元
腾讯                 元（浮点）      **手**          **万元**
东财                 **×100 整数**   **手**          元
===================  =============  =============  =============

atst 全局契约：**volume = 股，amount = 元，price = 元**。
三家源的原始值在 :class:`~atst.domain.models.Quote` 出炉前必须归一化，
否则混用会产生 100 / 10000 倍级的错误数据。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SourceSpec",
    "KNOWN_SOURCES",
    "SOURCES",
    "register_source",
    "get_source",
    "list_sources",
    "DEFAULT_FALLBACK_ORDER",
    "SINA",
    "TENCENT",
    "EASTMONEY",
    "JSL",
    "HK",
    "US",
    "KLINE",
    "BOC",
    "MINUTE_KLINE",
    "MINUTE",
    "SUGGEST",
    "TICKS",
    "TRENDS",
    "GLOBAL",
    "MARKET_STAT",
    "RANK",
    "FUND_FLOW",
    "LIMIT_POOL",
    "NORTHBOUND",
    "CORPORATE",
    "LHB",
    "NEWS",
    "SINA_FUND_FLOW",
    "WENCAI",
    "BAIDU",
    "FUND",
    "MARGIN",
    "INDEX_CONS",
    "CNINFO",
    "THS",
    "WALLSTREET",
]

SINA = "sina"
TENCENT = "tencent"
EASTMONEY = "eastmoney"
JSL = "jsl"
HK = "hk"
#: 新浪港股实时行情（``hq.sinajs.cn/list=hkXXXXX``，复用 HK 的口径）
HK_SINA = "hk_sina"
#: 美股实时行情（腾讯 qt.gtimg.cn q=usXXXXX / 200 前缀布局）
US = "us"
KLINE = "kline"
BOC = "boc"
#: 新浪个股新闻（vCB_AllNewsStock.php HTML 解析）
NEWS = "news"
#: 分钟 K 线（腾讯 ifzq mkline 接口）
MINUTE_KLINE = "minute_kline"
#: 当日分时（腾讯 minute/query 接口）
MINUTE = "minute"
#: 证券代码联想搜索（新浪 smartbox 接口）
SUGGEST = "suggest"
#: 逐笔成交明细（腾讯 stock.gtimg.cn detail 接口）
TICKS = "ticks"
#: 当日分时成交（东财 push2his trends2 接口）
TRENDS = "trends"
#: 外盘期货 / 外汇（腾讯 qt.gtimg.cn hf_ 前缀）
GLOBAL = "global"
#: 大盘统计（腾讯 qt.gtimg.cn s_ 前缀）
MARKET_STAT = "market_stat"
#: 通用排行（东财 push2 clist，个股 / ETF / 指数 / 板块资金流）
RANK = "rank"
#: 资金流（东财 ulist.np 实时 + fflow/kline 历史）
FUND_FLOW = "fund_flow"
#: 涨停 / 跌停 / 炸板池（东财 push2ex）
LIMIT_POOL = "limit_pool"
#: 沪深港通资金（东财 kamt）
NORTHBOUND = "northbound"
#: 基本面与公司行为（东财 datacenter-web 报表族）
CORPORATE = "corporate"
#: 龙虎榜每日个股榜（东财 datacenter-web RPT_DMSK_TS_STOCKNEW）
LHB = "longhu"
#: 新浪资金流历史（个股 ``ssl_qsfx_zjlrqs`` / 板块 ``ssl_bkzj_zjlrqs``）
SINA_FUND_FLOW = "sina_fund_flow"
#: i问财自然语言选股（www.iwencai.com load-data；需调用方注入 cookie）
WENCAI = "wencai"
#: 盘中异动池（东财 push2ex getAllStockChanges，20 类异动枚举）
STOCK_CHANGES = "stock_changes"
#: 股吧个股人气榜（东财 emappdata stockrank，POST JSON）
HOT_RANK = "hot_rank"
#: 百度财经（行情 + 股市通估值历史）
BAIDU = "baidu"
#: 东财基金（天天基金：历史净值 / 实时估值 / 基金列表；数据型源，不参与行情降级）
FUND = "fund"
#: 东财融资融券个股明细（datacenter-web RPTA_WEB_RZRQ_GGMX；数据型源，不参与行情降级）
MARGIN = "margin"
#: 东财指数成分股（datacenter-web RPT_INDEX_TS_COMPONENT；数据型源，不参与行情降级）
INDEX_CONS = "index_cons"
#: 巨潮资讯网（cninfo）法定披露公告（www.cninfo.com.cn/new/hisAnnouncement/query，POST 表单）
CNINFO = "cninfo"
#: 同花顺（10jqka）涨停池 / 板块归属 / 人气榜（data.10jqka.com.cn + dq.10jqka.com.cn）
THS = "ths"
#: 华尔街见闻（wallstreetcn）快讯流（api-one.wallstcn.com/apiv1/content/lives）
WALLSTREET = "wallstreet"


@dataclass(frozen=True)
class SourceSpec:
    """一个 HTTP Web 源的元信息 + 归一化系数。"""

    name: str
    summary: str
    #: 价格原始值 → 元的换算系数（乘）
    price_scale: float = 1.0
    #: 成交量原始值 → 股 的换算系数（乘）
    volume_scale: float = 1.0
    #: 成交额原始值 → 元 的换算系数（乘）
    amount_scale: float = 1.0
    #: 是否必须带 Referer（反爬）
    needs_referer: bool = False
    #: 是否需要 cookie（如集思录）
    needs_cookie: bool = False
    #: 默认限流（req/s）
    default_rate: int = 5
    #: 支持的能力
    capabilities: tuple[str, ...] = ("quote",)
    notes: str = ""


KNOWN_SOURCES: dict[str, SourceSpec] = {
    SINA: SourceSpec(
        name=SINA,
        summary="新浪财经实时行情（hq.sinajs.cn）",
        price_scale=1.0,
        volume_scale=1.0,  # 新浪 turnover 已是「股」
        amount_scale=1.0,  # 元
        needs_referer=True,  # 无 Referer 直接 403
        default_rate=8,
        capabilities=("quote", "rank", "all_market"),
        notes="必须带 Referer: https://finance.sina.com.cn；无 Referer 返回 403",
    ),
    TENCENT: SourceSpec(
        name=TENCENT,
        summary="腾讯财经实时行情（qt.gtimg.cn）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股
        amount_scale=10_000.0,  # 万元 → 元
        needs_referer=False,
        default_rate=10,
        capabilities=("quote", "kline", "minute_kline", "minute", "all_market"),
        notes="成交量单位为手、成交额单位为万元，必须归一化",
    ),
    EASTMONEY: SourceSpec(
        name=EASTMONEY,
        summary="东方财富行情（push2.eastmoney.com）",
        price_scale=1 / 100.0,  # f2 为 ×100 整数
        volume_scale=100.0,  # 手 → 股
        amount_scale=1.0,  # 元
        needs_referer=False,
        default_rate=5,
        capabilities=("quote", "kline", "rank", "stock_boards"),
        notes="价格字段 f2/f3/f4 均为 ×100 整数；高频易触发 IP 封禁",
    ),
    JSL: SourceSpec(
        name=JSL,
        summary="集思录（可转债 / 分级基金 / ETF）",
        needs_cookie=True,
        default_rate=2,
        capabilities=("bond", "etf"),
        notes="部分接口需登录 cookie；限速严格",
    ),
    HK: SourceSpec(
        name=HK,
        summary="港股实时行情（qt.gtimg.cn/q=hkXXXXX）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=8,
        capabilities=("quote",),
        notes="港股通标的；成交量单位为股；价格/成交额以港元(HKD)计，"
        "extra.currency 标记原生币种，库不做汇率换算",
    ),
    HK_SINA: SourceSpec(
        name=HK_SINA,
        summary="新浪港股实时行情（hq.sinajs.cn hk 前缀）",
        price_scale=1.0,  # 港股价格直接为港元，无需缩放
        volume_scale=1.0,  # 新浪港股成交量已是「股」
        amount_scale=1.0,  # 成交额已是元(HKD)
        needs_referer=True,  # 复用新浪源，需 Referer
        default_rate=8,
        capabilities=("quote",),
        notes="SinaHkSource 为 SinaSource 子类，复用新浪港股 hq_str 解析；"
        "其 source_name 返回 'hk'（归一化走 HK 的 identity），量为股、"
        "额为元(HKD)，extra.currency='HKD'。需带 Referer 同 SINA。",
    ),
    US: SourceSpec(
        name=US,
        summary="美股实时行情（qt.gtimg.cn/q=usXXXXX）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=8,
        capabilities=("quote",),
        notes="美股（NYSE/NASDAQ）；成交量单位为股；价格/成交额以美元(USD)计，"
        "extra.currency 标记原生币种，库不做汇率换算",
    ),
    KLINE: SourceSpec(
        name=KLINE,
        summary="日 K 线（腾讯 ifzq.gtimg.cn / 东财 push2his）",
        price_scale=1.0,
        volume_scale=100.0,
        amount_scale=1.0,
        default_rate=5,
        capabilities=("kline",),
        notes="日 K 线历史数据",
    ),
    BOC: SourceSpec(
        name=BOC,
        summary="中国银行外汇牌价",
        default_rate=1,
        capabilities=("fx",),
        notes="汇率数据，非行情",
    ),
    NEWS: SourceSpec(
        name=NEWS,
        summary="新浪个股新闻（vCB_AllNewsStock.php）",
        default_rate=2,
        capabilities=("news",),
        notes="个股新闻列表（标题/链接/时间）；A 股覆盖最全，港股/美股可能为空；"
        "新浪新闻 JSON 接口已失效，改采稳定 HTML 资讯页",
    ),
    MINUTE_KLINE: SourceSpec(
        name=MINUTE_KLINE,
        summary="分钟 K 线（腾讯 ifzq.gtimg.cn mkline）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股（A 股；港股/美股为股不缩放）
        amount_scale=1.0,
        default_rate=5,
        capabilities=("kline", "minute_kline"),
        notes="1/5/15/30/60 分钟 K 线，**仅 A 股**（港股/美股 mkline 返回空，"
        "直连会抛错）。港股/美股分钟 K 线走东财 push2his（EastmoneyHistoryKlineSource），"
        "WebQuoteSession.klines/minute_klines 已按市场自动路由。",
    ),
    MINUTE: SourceSpec(
        name=MINUTE,
        summary="当日分时（腾讯 web.ifzq.gtimg.cn minute/query）",
        price_scale=1.0,
        volume_scale=100.0,  # 腾讯分时累计量返回「手」，解析层 ×100 到股
        amount_scale=1.0,  # 额已为元
        default_rate=5,
        capabilities=("minute",),
        notes="当日 1 分钟分时（价格/均价/成交量）。接口累计量为「手」，"
        "解析层 MinuteSource 已 ×100 到股；量口径统一由解析层内联处理，"
        "normalizer 为 identity（不二次缩放）。",
    ),
    SUGGEST: SourceSpec(
        name=SUGGEST,
        summary="证券代码联想搜索（新浪 smartbox）",
        default_rate=5,
        capabilities=("suggest",),
        notes="输入拼音/汉字/代码片段 → 候选证券列表",
    ),
    TICKS: SourceSpec(
        name=TICKS,
        summary="逐笔成交明细（腾讯 stock.gtimg.cn detail）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股
        amount_scale=1.0,  # 元
        default_rate=5,
        capabilities=("tick",),
        notes="每页条数以 atst.web.ticks.TICKS_PER_PAGE 为单一事实源（勿在此复写具体数字）；p 为页码（0 起）。量单位手、额单位元",
    ),
    TRENDS: SourceSpec(
        name=TRENDS,
        summary="当日分时成交（东财 push2his trends2）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股
        amount_scale=1.0,
        default_rate=5,
        capabilities=("tick", "minute"),
        notes="1 分钟粒度，含均价；iscr=0 不复权、ndays=1 当日",
    ),
    GLOBAL: SourceSpec(
        name=GLOBAL,
        summary="外盘期货 / 外汇（腾讯 qt.gtimg.cn hf_ 前缀）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=5,
        capabilities=("quote", "global"),
        notes="纽约原油/黄金、伦铜、布伦特原油等；字段为定长 14 列",
    ),
    MARKET_STAT: SourceSpec(
        name=MARKET_STAT,
        summary="大盘统计（腾讯 qt.gtimg.cn s_ 前缀）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股
        amount_scale=10_000.0,  # 万元 → 元
        default_rate=5,
        capabilities=("quote", "stat"),
        notes="s_sh000001 形式；返回点数、涨跌额、涨跌幅、成交量(手)、成交额(万元)",
    ),
    RANK: SourceSpec(
        name=RANK,
        summary="通用排行（东财 push2 clist）",
        price_scale=1.0,
        volume_scale=100.0,  # 手 → 股
        amount_scale=1.0,
        default_rate=3,
        capabilities=("rank",),
        notes="fs 决定市场（m:0+t:6 深A 等），fid 决定排序字段；高频易断连",
    ),
    FUND_FLOW: SourceSpec(
        name=FUND_FLOW,
        summary="资金流（东财 ulist.np 实时 / fflow/kline 历史）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=3,
        capabilities=("fund_flow",),
        notes="主力=超大单+大单；占比字段 f184/f69/f75/f81/f87 为 ×100 整数",
    ),
    LIMIT_POOL: SourceSpec(
        name=LIMIT_POOL,
        summary="涨停 / 跌停 / 炸板池（东财 push2ex）",
        price_scale=1 / 1000.0,  # p 字段为 ×1000 整数（7220 → 7.22 元）
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=3,
        capabilities=("limit_pool",),
        notes="date 为 YYYYMMDD；非交易日返回空池。本源输出为 dict 列表，"
        "解析层已按 1/1000 直接换算，不调用 normalize_quote",
    ),
    NORTHBOUND: SourceSpec(
        name=NORTHBOUND,
        summary="沪深港通资金（东财 kamt）",
        default_rate=2,
        capabilities=("fund_flow", "northbound"),
        notes="金额单位为万元；status=3 表示已收盘",
    ),
    CORPORATE: SourceSpec(
        name=CORPORATE,
        summary="基本面与公司行为（东财 datacenter-web 报表族）",
        default_rate=2,
        capabilities=("corporate", "ipo"),
        notes="F10/公告/研报/股东/大宗/解禁/业绩/IPO 申购日历；报表名错误返回 code=9501",
    ),
    LHB: SourceSpec(
        name=LHB,
        summary="龙虎榜每日个股榜（东财 datacenter-web RPT_DMSK_TS_STOCKNEW）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,
        default_rate=2,
        capabilities=("longhu",),
        notes="主力净流入=超大单净+大单净(元)；TRADE_DATE 过滤须用完整"
        "'YYYY-MM-DD 00:00:00' 字面值，否则 9201 空数据；"
        "机构席位/营业部买卖子报表名已失效(9501)，本源仅取每日上榜个股汇总",
    ),
    SINA_FUND_FLOW: SourceSpec(
        name=SINA_FUND_FLOW,
        summary="新浪资金流历史（个股 ssl_qsfx_zjlrqs / 板块 ssl_bkzj_zjlrqs）",
        price_scale=1.0,
        volume_scale=1.0,
        amount_scale=1.0,  # 金额字段原生即为元，无需缩放
        needs_referer=True,
        default_rate=5,
        capabilities=("fund_flow", "history"),
        notes="净额(元)/换手率(%)等均为原生单位，normalizer 为 identity；"
        "必须带 page/num/sort 三参数，缺省会返回全历史(约 1MB)；"
        "板块代码用 SinaIndustryBoardSource 的 new_xxx 体系"
        "（旧 hangye_ZLxx 体系已停更，最新数据停留在 2020 年）",
    ),
    WENCAI: SourceSpec(
        name=WENCAI,
        summary="i问财自然语言选股（www.iwencai.com load-data）",
        default_rate=1,
        capabilities=("wencai",),
        notes="自然语言选股（如“连板3板以上”）；hexin-v cookie 由调用方注入"
        "（参数 cookie= 或环境变量 ATST_WENCAI_COOKIE），"
        "atst 不依赖任何第三方 cookie 中继服务；缺 cookie 时在 fetch 阶段"
        "抛 WebSourceError（构造不拦截，便于罐头测试）；"
        "返回 title+rows zip 后的 list[dict]",
    ),
    STOCK_CHANGES: SourceSpec(
        name=STOCK_CHANGES,
        summary="盘中异动池（东财 push2ex getAllStockChanges）",
        default_rate=2,
        capabilities=("stock_changes",),
        notes="20 类异动枚举（火箭发射/大笔买入/60日新高…，2026-09 实测验证）；"
        "输出为 dict 列表（time/code/name/change_type/metrics），"
        "metrics 为异动指标数值列表（含义随类型不同，不强行归一）；"
        "非交易时段返回空 allstock 为合法状态",
    ),
    HOT_RANK: SourceSpec(
        name=HOT_RANK,
        summary="股吧个股人气榜（东财 emappdata stockrank，POST JSON）",
        default_rate=2,
        capabilities=("hot_rank",),
        notes="人气排名榜（rk 当前名次 + rc 较上期变动）；榜单仅含排名与"
        "代码，不含行情字段，如需行情请以返回 symbol 回查 quotes；"
        "globalId 由本库生成（uuid4），appId 沿用页面公开参数",
    ),
    BAIDU: SourceSpec(
        name=BAIDU,
        summary="百度财经（finance.pae.baidu.com selfselect）",
        price_scale=1.0,
        volume_scale=1.0,  # 量额口径在解析层内联换算（与常规命名相反），此处 identity
        amount_scale=1.0,
        default_rate=3,
        capabilities=("kline", "minute", "tick", "quote", "valuation_history"),
        notes="仅 A 股（stockType=ab）。坑：K 线 kline.volume 实为成交额(元)、"
        "kline.amount 实为成交量(手)，解析层须交换映射（volume=amount×100, "
        "amount=volume）；分时 amount 为含'万'字符串，优先用 oriAmount(元)。"
        "股市通历史图表可取总市值、PE(TTM/静态)、PB、市现率；值保留源端单位，"
        "不含股息率/流通股本历史。非官方接口，随时可能改版/下线；"
        "不提供 fund_flow（2026-05 起下线）。",
    ),
    FUND: SourceSpec(
        name=FUND,
        summary="东财基金（天天基金：历史净值 / 实时估值 / 基金列表）",
        default_rate=3,
        capabilities=("fund_nav_history", "fund_estimate", "fund_list"),
        notes="数据型源（list[dict]/dict），不参与行情降级链；历史净值需 "
        "Referer http://fundf10.eastmoney.com/（适配器内置）；基金列表为全量 "
        "js 数组（约 2.7 万条，数 MB）。**实时估值已下线**：fundgz jsonp 接口 "
        "2026 年实测返回 404 页面，fetch_estimate 显式抛 SourceDeprecated。",
    ),
    MARGIN: SourceSpec(
        name=MARGIN,
        summary="东财融资融券个股明细（datacenter-web RPTA_WEB_RZRQ_GGMX）",
        default_rate=3,
        capabilities=("margin",),
        notes="数据型源（list[dict]），不参与行情降级链；仅两融标的（非标的"
        "个股空列表）；DATE 倒序单页/翻页均可；金额单位元（RZYE 融资余额/"
        "RZMRE 融资买入/RZJME 融资净买/RQYE 融券余额/RZRQYE 两融余额），"
        "RZYEZB 为融资余额占流通市值比(%)。",
    ),
    INDEX_CONS: SourceSpec(
        name=INDEX_CONS,
        summary="东财指数成分股（datacenter-web RPT_INDEX_TS_COMPONENT）",
        default_rate=3,
        capabilities=("index_constituents",),
        notes="数据型源（list[dict]），不参与行情降级链；TYPE 为指数族过滤"
        "（2026-09 与中证官网 XLS 交叉验证 5 族 jaccard=1.0）；单页上限 "
        "500，中证1000/中证2000 等大指数自动分页拉全量；weight 仅部分指数"
        "族提供（沪深300/上证50/中证500/科创50 有值）。",
    ),
    CNINFO: SourceSpec(
        name=CNINFO,
        summary="巨潮资讯网法定披露公告（www.cninfo.com.cn）",
        default_rate=2,
        capabilities=("announcements",),
        notes="POST 表单到 /new/hisAnnouncement/query；column 决定板块"
        "（szse 深主板+创业板 / sse 沪市 / hke 港交所）。"
        "webapi.cninfo.com.cn 那套（p_info3015 等）需 token，未采用。",
    ),
    THS: SourceSpec(
        name=THS,
        summary="同花顺涨停池 / 板块归属 / 人气榜（10jqka）",
        default_rate=2,
        capabilities=("limit_pool", "theme_attribution", "concept_members", "hot_rank"),
        notes="data.10jqka.com.cn/dataapi/limit_up/{limit_up_pool,block_top}（涨停池/板块归属）；"
        "dq.10jqka.com.cn/fuyao/hot_list_data（人气榜）。需 Referer。",
    ),
    WALLSTREET: SourceSpec(
        name=WALLSTREET,
        summary="华尔街见闻快讯（api-one.wallstcn.com）",
        default_rate=2,
        capabilities=("breaking_news",),
        notes="GET /apiv1/content/lives?channel=global-channel；仅该频道稳定返回，"
        "macro-channel 实测为空，故只挂 breaking_news 一个能力。",
    ),
}

#: 注册表（含延迟构造的工厂）
SOURCES: dict[str, SourceSpec] = dict(KNOWN_SOURCES)

#: 默认降级顺序（可被配置覆盖，§33.6）
DEFAULT_FALLBACK_ORDER: tuple[str, ...] = (TENCENT, SINA, EASTMONEY)


def register_source(spec: SourceSpec) -> None:
    """注册自定义 HTTP 行情源。"""
    SOURCES[spec.name] = spec


def get_source(name: str) -> SourceSpec:
    if name not in SOURCES:
        raise KeyError(f"未知 HTTP 行情源: {name!r}；已知: {sorted(SOURCES)}")
    return SOURCES[name]


def list_sources(*, capability: str | None = None) -> list[str]:
    if capability is None:
        return sorted(SOURCES)
    return sorted(n for n, s in SOURCES.items() if capability in s.capabilities)
