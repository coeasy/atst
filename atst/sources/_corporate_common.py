# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""基本面与公司行为适配器（§33 扩展）。

覆盖东财三套后端：``datacenter-web`` 报表族、``np-anotice`` 公告、
``reportapi`` 研报，以及 ``push2`` 的 F10 基础资料。

接口事实（2026-09 真实抓包验证，atst 自有实现）:

**datacenter-web 报表族** ``datacenter-web.eastmoney.com/api/data/v1/get``::

    ?reportName=RPT_XXX&columns=ALL&sortColumns=...&sortTypes=-1
    &pageSize=5&pageNumber=1&filter=(SECURITY_CODE%3D%22600519%22)
    &source=WEB&client=WEB

    // 成功:
    {"version":"...","result":{"pages":N,"data":[{...}],"count":M},
     "success":true,"message":"ok","code":0}
    // 失败（报表名拼错时高频出现）:
    {"version":null,"result":null,"success":false,
     "message":"报表配置不存在,RPT_XXX","code":9501}

    // 已验证可用报表:
    //   RPT_LICO_FN_CPD            业绩报表
    //   RPT_F10_EH_FREEHOLDERS     十大流通股东
    //   RPT_HOLDERNUMLATEST        股东户数
    //   RPT_DATA_BLOCKTRADE        大宗交易
    //   RPT_LIFT_STAGE             限售股解禁
    // 实测不可用（报表配置不存在）: RPT_IPO_INFO / RPT_IPO_APPLY /
    //   RPT_MARGIN_DAILY / RPT_MARGIN_DAILYSUM —— 不要写进代码。

**东财 F10 基础资料** ``push2.../api/qt/stock/get``::

    {"data":{"f43":129956,"f47":32664,"f48":4242440861.0,"f49":15894,
      "f57":"600519","f58":"贵州茅台","f60":129952,"f84":1250081601.0,
      "f85":1250081601.0,"f116":1624556045395.56,"f117":1624556045395.56,
      "f127":"白酒Ⅱ","f128":"贵州板块","f162":1825,"f167":647,
      "f173":16.75,"f174":153998,"f175":115101}}
    // ×100 字段: f43 最新价 / f60 昨收 / f162 市盈率(动) / f167 市净率
    //            / f174 52 周最高 / f175 52 周最低
    // 原生单位: f47 成交量(手) / f48 成交额(元) / f49 外盘(手)
    //           f84 总股本 / f85 流通股 / f116 总市值 / f117 流通市值
    //           f127 所属行业 / f128 所属板块 / f173 ROE(%)

**东财公告** ``np-anotice-stock.eastmoney.com/api/security/ann``::

    {"data":{"list":[{"art_code":"AN2026...","title":"贵州茅台:...",
      "notice_date":"2026-08-15 00:00:00","display_time":"2026-08-14 20:41:29",
      "codes":[{"stock_code":"600519","short_name":"贵州茅台"}],
      "columns":[{"column_name":"其他"}]}]}}

**东财研报** ``reportapi.eastmoney.com/report/list``::

    {"hits":46,"size":5,"data":[{"title":"2026年中报点评：...",
      "stockCode":"600519","orgSName":"西南证券","publishDate":"2026-08-21 ...",
      "predictThisYearEps":"69.83","predictThisYearPe":"18.59",
      "emRatingName":"买入","researcher":"朱会振,舒尚立"}]}

.. note::
   基本面接口变更频率高于行情接口。所有适配器在响应结构不匹配时抛
   :class:`~atst.errors.SourceDeprecated`，便于上层切换到备用源。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "VALID_REPORTS",
    "EMPTY_RESULT_CODE",
    "is_empty_result",
    "report_code_for",
]


def report_code_for(symbol: str) -> str | None:
    """东财 datacenter 报表族按 **6 位代码** 过滤，市场前缀在这里必然丢掉。

    ``sh000001``（上证指数）与 ``sz000001``（平安银行）因此撞码。这些报表只覆盖
    **有公司行为的 A 股标的**，而指数没有分红、配股、估值、股东户数、两融——继续查
    下去只会把撞码那只股票的事实安到指数头上。实测（2026-10-13）::

        dividend_history('sh000001') → 平安银行 20 条分红
        margin('sh000001')          → 平安银行 20 条两融
        holder_num('sh000001')      → 平安银行 1 条

    于是 ``adjusted_bars('sh000001', 'hfq')`` 拿平安银行的分红给上证指数复权，
    收盘价从 3797 变成 434924。判据取 :func:`atst.universe.classify_with_index`
    的段表——与 ``0x052D`` 指数位同一份事实源，不另抄一套。

    Returns
    -------
    ``str``
        该标的在报表里的 6 位代码。
    ``None``
        该标的不在这些报表的覆盖范围内（指数）。调用方按**空集**处理——
        这是事实陈述（指数没有公司行为），不是容错。
    """
    from ..universe import classify_with_index

    if classify_with_index(symbol)[1]:
        return None
    from ..domain.symbol import split_symbol

    _, code = split_symbol(symbol)
    return code


#: 实测可用的 datacenter-web 报表名（写死前须重新抓包验证）
VALID_REPORTS: dict[str, str] = {
    "performance": "RPT_LICO_FN_CPD",
    "forecast": "RPT_PUBLIC_OP_NEWPREDICT",
    "free_holders": "RPT_F10_EH_FREEHOLDERS",
    "holder_num": "RPT_HOLDERNUMLATEST",
    "block_trade": "RPT_DATA_BLOCKTRADE",
    "unlock": "RPT_LIFT_STAGE",
    "ipo": "RPTA_APP_IPOAPPLY",
    # 分红送配（2026-09-06 实测可用；字段较稀疏，金额单位以东财报表页为准）
    "dividend": "RPT_SHAREBONUS_DET",
    # 配股（2026-10-07 实测可用）。报表名取自 akshare ``stock_pg_em`` 源码
    # （data.eastmoney.com/xg/pg/），**不是按命名惯例猜的**——猜的
    # RPT_ALLOTMENT_DET / RPT_SHARE_ALLOTMENT / RPT_RIGHTS_ISSUE_DET /
    # RPT_SECURITY_ALLOTMENT 四个全部返回 9501（报表配置不存在）。
    #
    # 关键：``RPT_SHAREBONUS_DET`` 只覆盖分红送转，**一条配股都没有**（工行/中行/
    # 招行/浦发/兴业 2010 年都做过配股，实测返回 0 条含"配"的方案）。配股必须走
    # 这张独立报表，否则 ``CapitalChange.rights_*`` 恒为 0，配股标的除权价算错。
    # 真机列名（sh601398，2010 年配股，与公开方案 10配0.45股@2.99元 对拍一致）：
    # ``PLACING_RATIO``=0.45（每 10 股配股数）/ ``ISSUE_PRICE``=2.99（配股价）/
    # ``EX_DIVIDEND_DATE``（除权日）/ ``EQUITY_RECORD_DATE``（股权登记日）。
    "rights_issue": "RPT_IPO_ALLOTMENT",
    # ---- 治理 / 评级预测（2026-09-09 扩展，best-effort 映射）----
    # 董监高持股明细（内部人减持 / 套现排查）
    "executive_hold": "RPT_EXECUTIVE_HOLD_DETAILS",
    # 股东增减持（大股东 / 机构持股比例变动）
    "shareholder_change": "RPT_SHARE_HOLDER_INCREASE",
    # 公司概况（法定代表人 / 董事长 / 主营 / 员工数）
    "org_profile": "RPT_F10_BASIC_ORGINFO",
    # 券商评级与目标价（一致预期 EPS / PE / 目标价 / 覆盖机构数）
    "rating_forecast": "RPT_WEB_RESPREDICT",
    # ---- 行业 / 概念指数（2026-09-11 扩展）----
    # 行业指数（板块/概念指标，含涨跌幅/排名）
    "industry_index": "RPT_INDUSTRY_INDEX",
    # 概念指数成分（股票代码→概念映射）
    "concept_index": "RPT_CONCEPT_INDEX",
    # ---- 所属板块（2026-10-14 实测可用）----
    # F10 核心题材「所属板块」。全量 31681 行 = 全市场个股 × 所属板块
    # （行业/概念/地域/指数族），`BOARD_RANK`=1 即该股的主营板块。
    #
    # **这是当前快照，不是历史截面**：报表没有任何时间维度字段——实测
    # `sortColumns=UPDATE_DATE` / `TRADE_DATE` 均回 `code=9501 排序列不存在`，
    # `SELECTED_BOARD_REASON`（入选原因）里那些「2024年12月27日回复称…」
    # 是互动易/年报的**证据披露日**，不是加入板块日，不能拿来反推历史。
    # 想要历史成分只能每日落盘快照后本地反推。
    #
    # `NEW_BOARD_CODE` 是 BK 代码（如 `BK0896`），可直接回查
    # `em_board_members` 拉该板块当前成分，形成
    # 「个股 → 所属板块 → 板块成分」的网络遍历。
    #
    # 按 6 位代码过滤：`sh000001`（上证指数）会撞上 `sz000001`（平安银行），
    # 因此入口必须走 :func:`report_code_for` 的指数守卫。
    "board_attribution": "RPT_F10_CORETHEME_BOARDTYPE",
    # ---- 2026-10-14 批量补齐（全部真机实测：报表名取自 akshare 源码 grep，
    # 可用性与过滤键逐张 datacenter 实拉验证，不是按命名惯例猜的）----
    #
    # 停复牌。**必须给 filter**（空 filter 服务端回 9501「filter字段不能为空」），
    # 且维度是「交易日」不是个股：``(MARKET="全部")(DATETIME='2026-10-13')``
    # 实测 17 条、``(DATETIME='2024-04-26')`` 实测 927 条。所以这个能力的入参
    # 是 date 而不是 symbol——想查某只股票请拉全量后在本地筛。
    "suspension": "RPT_CUSTOM_SUSPEND_DATA_INTERFACE",
    # 商誉：个股明细。稀疏报表（多数公司没有商誉），600519 实测回
    # ``code=9201 返回数据为空``，因此入口必须 ``allow_empty=True``。
    "goodwill": "RPT_GOODWILL_STOCKDETAILS",
    # 商誉：市场统计（按板块/报告期汇总，含减值状态）
    "goodwill_market": "RPT_GOODWILL_MARKETSTATISTICS",
    # 增发/SEO（配股之外的另一条股本扩张路径）。注意它没有可用的日期排序列：
    # 实测 NOTICE_DATE / ISSUE_START_DATE / UPDATE_DATE 均回
    # ``9501 排序列不存在``，因此**不指定排序**，用服务端默认顺序。
    "seo": "RPT_SEO_DETAIL",
    # 股本结构（有限售/无限售/总股本及占比，含变动原因）
    "equity_structure": "RPT_F10_EH_EQUITY",
    # 股东户数**明细**（按报告期）。区别于 holder_num=RPT_HOLDERNUMLATEST
    # 只给最新一期——要趋势必须走这张。
    "holder_num_detail": "RPT_HOLDERNUM_DET",
    # 机构专用席位交易明细（龙虎榜里的"机构专用"席位）
    "org_seat_trades": "RPT_ORGANIZATION_TRADE_DETAILS",
    # 沪深港通历史成交。**南北双向都在这一张表里**：MUTUAL_TYPE 实测语义为
    # 001 沪股通(北向 .SH) / 002 沪市港股通(南向 .HK) / 003 深股通(北向 .SZ) /
    # 004 深市港股通(南向 .HK) / 005 北向合计 / 006 南向合计。
    "connect_deals": "RPT_MUTUAL_DEAL_HISTORY",
    # 港股通（**南向**）持股排行榜。实测全量 500 行样本里 SECUCODE 后缀 100%
    # 是 .HK、MUTUAL_TYPE 只有 002/004（都是南向通道）——这张表**没有 A 股**，
    # A 股代码查它恒为空集。北向（外资持 A 股）走 northbound_hold。
    "southbound_ranks": "RPT_MUTUAL_STOCK_HOLDRANKS",
    # 融资融券每日交易（市场级，含融资余额/融券余额/两融余额）
    "margin_daily": "RPTA_WEB_MARGIN_DAILYTRADE",
    # 大宗交易统计（区别于 block_trade=RPT_DATA_BLOCKTRADE 的明细）
    "block_trade_stats": "RPT_BLOCKTRADE_STA",
    # ---- 宏观补齐（2026-10-14，全部按 REPORT_DATE 排序）----
    # 货币供应量 M0/M1/M2（含同比/环比）
    "macro_money_supply": "RPT_ECONOMY_CURRENCY_SUPPLY",
    # 人民币贷款（含同比/环比/累计）
    "macro_loan": "RPT_ECONOMY_RMB_LOAN",
    # 社会消费品零售总额
    "macro_retail": "RPT_ECONOMY_TOTAL_RETAIL",
    # 固定资产投资
    "macro_asset_invest": "RPT_ECONOMY_ASSET_INVEST",
    # 工业增加值
    "macro_industrial": "RPT_ECONOMY_INDUS_GROW",
    # 存款准备金率（大型/中小金融机构，变动前后）
    "macro_reserve_ratio": "RPT_ECONOMY_DEPOSIT_RESERVE",
    # 外汇储备 / 黄金储备
    "macro_forex_reserve": "RPT_ECONOMY_GOLD_CURRENCY",
    # ---- 宏观经济（2026-09-11 扩展）----
    # CPI（全国/城镇/农村，含同比/环比/累计）
    "macro_cpi": "RPT_ECONOMY_CPI",
    # PPI（出厂价同比/环比/累计）
    "macro_ppi": "RPT_ECONOMY_PPI",
    # GDP（GDP总量/三产占比/同比增速）
    "macro_gdp": "RPT_ECONOMY_GDP",
    # ---- 可转债（2026-09-11 扩展）----
    "convertible_bonds": "RPT_BOND_CB_LIST",
    # ---- 北向持股（2026-09-11 P3 扩展）----
    # 沪股通/深股通持仓明细（按个股/日期，含持股数/市值/持股比例）
    "northbound_hold": "RPT_MUTUAL_HOLD",
    # ---- 十大股东（2026-09-11 P3 扩展）----
    # 全部十大股东（含非流通股，区别于 free_holders 仅流通股东）
    "top_holders": "RPT_F10_EH_HOLDERS",
    # ---- 解禁股票（2026-09-11 P3 扩展）----
    # 按个股解禁明细（区别于 unlock=RPT_LIFT_STAGE 按批次解禁）
    "unlock_stocks": "RPT_LIFT_STOCK",
    # ---- 业绩预告旧版（2026-09-11 P3 扩展）----
    # 含 FORECASTCONTENT 文本描述 + CHANGEREASONDSCRPT 原因
    # （区别于 forecast=RPT_PUBLIC_OP_NEWPREDICT 新版结构化字段）
    "earnings_preview": "RPT_PUBLIC_OP_PREDICT",
}

#: datacenter-web 用这个 code 表示「该 filter 下没有记录」（``success=false`` +
#: ``message=返回数据为空``）。配股一类**天然稀疏**的报表会正常命中它：绝大多数标的
#: 从未配股，9201 是正常回答而不是故障——见
#: :meth:`EastmoneyDataCenterSource._parse_rows_meta` 的 ``allow_empty``。
EMPTY_RESULT_CODE = 9201


def is_empty_result(payload: Any) -> bool:
    """这次 datacenter-web 应答是不是「该 filter 下没有记录」（``code=9201``）。

    单点判据，供所有需要 ``allow_empty`` 的解析器共用：三处各写一遍"9201 算不算空"
    迟早会分叉（一处按 int 比、一处按 str 比），而分叉出来的那半边是静默的——
    "没配过股"会变成一次故障，或者反过来，一次真故障被当成空集吞掉。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return False
    if not isinstance(payload, Mapping):
        return False
    code = payload.get("code")
    if isinstance(code, str) and code.strip().isdigit():
        code = int(code.strip())
    return not payload.get("success") and code == EMPTY_RESULT_CODE


_HOSTS = (
    "https://datacenter-web.eastmoney.com",
    "https://datacenter.eastmoney.com",
)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _base_path(base: str) -> str:
    """取 :attr:`BASE` 的路径部分。

    :meth:`_EastmoneyJson._get_json` 只补**主机**，不补 ``BASE``；直接传完整
    ``BASE`` 会拼出「主机+主机」的非法 URL。独立主机源（公告 / 研报）的
    ``fetch_*`` 需自行拼上路径段。
    """
    return urlsplit(base).path or "/"


# --------------------------------------------------------------------------- #
# datacenter-web 报表族基类
# --------------------------------------------------------------------------- #
