# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""板块行情适配器：行业 / 概念 / 地域板块列表与成分（§33 扩展）。

接口事实（2026-08 真实抓包验证，tstdx 自有实现）:

**新浪行业板块列表** ``vip.stock.finance.sina.com.cn/q/view/newSinaHy.php``
（gbk）::

    var S_Finance_bankuai_sinaindustry = {"new_blhy":
    "new_blhy,玻璃行业,19,13.049,0.093,0.720,452830284,10610274008,sh600552,2.852,18.390,0.510,凯盛科技",...}
    // 值为逗号分隔 13 列：
    // [0]板块代码 [1]名称 [2]公司家数 [3]均价 [4]涨跌额 [5]涨跌幅%
    // [6]总成交量(股) [7]总成交额(元) [8]领涨股 [9]领涨股涨跌幅%
    // [10]领涨股价 [11]领涨股涨跌额 [12]领涨股名称

**新浪板块成分** ``Market_Center.getHQNodeData?node=new_xxx``（分页，
行结构与全市场接口一致，见 :class:`~tstdx.web.sina.adapters.SinaSource`）。

**腾讯板块排行** ``proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/rank``
（行业 ``t=01/averatio``、概念 ``t=02/averatio``）::

    {"code":0,"data":[{"bd_name":"数字媒体","bd_code":"pt01801767",
      "bd_zxj":"1633.43","bd_zd":"109.51","bd_zdf":"7.19",
      "nzg_code":"sz300413","nzg_name":"芒果超媒","nzg_zdf":"20.00",...}]}

**东财板块列表 / 成分** ``push2.eastmoney.com/api/qt/clist/get``::

    列表: fs=m:90+t:2(行业)/t:3(概念)/t:1(地域) → diff=[{f12:BKxxxx,f14:名称,f3:涨跌幅},...]
    成分: fs=b:BK0475 → diff=[{f12:代码,f14:名称,f2:现价,f3:涨跌幅},...]

.. warning::
   腾讯 mktHs 家族的**板块成分**端点未公开（stockList/getBoard 等均返回
   code=11），成分统一走新浪 node 接口。东财 f2/f3 在收盘后可能为 ``"-"``
   字符串，解析时按 0 处理。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.models import Quote
from ..errors import SourceDeprecated
from .base import (
    BaseWebSource,
    _EastmoneyJson,
)
from .base import (
    num_f as _f,
)
from .base import (
    num_i as _i,
)
from .sources import EASTMONEY, SINA, TENCENT

__all__ = [
    "SinaIndustryBoardSource",
    "SinaBoardListSource",
    "SinaBoardMemberSource",
    "TencentBoardRankSource",
    "EastmoneyBoardSource",
]

#: 新浪行业板块列表响应前缀
_SINA_BOARD_PREFIX = "var S_Finance_bankuai_sinaindustry"


# --------------------------------------------------------------------------- #
# 新浪：行业板块列表
# --------------------------------------------------------------------------- #
class SinaIndustryBoardSource(BaseWebSource):
    """新浪行业板块列表（49 个行业，含领涨股）。"""

    BASE = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE

    def fetch_boards(self) -> list[dict[str, Any]]:
        """拉取全部行业板块。

        Returns
        -------
        ``[{"code": "new_blhy", "name": "玻璃行业", "count": 19,
        "avg_price": 13.05, "change": 0.09, "pct_change": 0.72,
        "volume": 452830284, "amount": 10610274008,
        "leader": "sh600552", "leader_name": "凯盛科技",
        "leader_pct": 2.852}, ...]``
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        return self.parse_boards(
            self._request_text(self.BASE, encoding="gbk", err_msg="新浪板块列表请求失败")
        )

    def parse_boards(self, text: str) -> list[dict[str, Any]]:
        """解析 ``var S_Finance_bankuai_sinaindustry = {...}``（gbk 文本）。"""
        if _SINA_BOARD_PREFIX not in text:
            raise SourceDeprecated(
                "新浪板块列表响应格式变更（未找到声明前缀）",
                context={"source": SINA, "sample": text[:120]},
            )
        body = text.split("=", 1)[1].strip().rstrip(";")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "新浪板块列表非 JSON",
                context={"source": SINA, "sample": body[:120]},
                cause=exc,
            ) from exc
        out: list[dict[str, Any]] = []
        for raw in data.values():
            cols = str(raw).split(",")
            if len(cols) < 13:
                continue
            out.append(
                {
                    "code": cols[0],
                    "name": cols[1],
                    "count": _i(cols[2]),
                    "avg_price": _f(cols[3]),
                    "change": _f(cols[4]),
                    "pct_change": _f(cols[5]),
                    "volume": _i(cols[6]),  # 股（新浪口径）
                    "amount": _f(cols[7]),  # 元
                    "leader": cols[8],
                    "leader_pct": _f(cols[9]),
                    "leader_price": _f(cols[10]),
                    "leader_change": _f(cols[11]),
                    "leader_name": cols[12],
                }
            )
        if not out:
            raise SourceDeprecated("新浪板块列表为空", context={"source": SINA})
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []  # 板块列表非 Quote 语义


# --------------------------------------------------------------------------- #
# 新浪：板块列表（概念 / 地域 / 新版行业，newFLJK 入口）
# --------------------------------------------------------------------------- #
class SinaBoardListSource(BaseWebSource):
    """新浪板块列表（``q/view/newFLJK.php``，概念 / 地域 / 新版行业）。

    与 :class:`SinaIndustryBoardSource`（``newSinaHy.php``，49 行业旧口径）
    互补：本源覆盖 **概念（约 175 个）/ 地域（31 个）/ 新版行业（84 个，
    含北交所领涨股）** 三族，行结构同为逗号分隔 13 列（含领涨股）。
    """

    BASE = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php"

    #: tstdx 板块类型 → 新浪 param（概念 class / 地域 area / 行业 industry）
    BOARD_PARAMS = {"concept": "class", "region": "area", "industry": "industry"}

    #: 响应变量前缀（``var S_Finance_bankuai_{param} = {...}``）
    VAR_PREFIX = "S_Finance_bankuai_"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        param = self.BOARD_PARAMS.get(kwargs.get("board", "concept"), self.BOARD_PARAMS["concept"])
        return f"{self.BASE}?param={param}"

    def fetch_boards(self, board: str = "concept") -> list[dict[str, Any]]:
        """拉取板块列表（实时统计 + 领涨股）。

        Parameters
        ----------
        board:
            ``concept`` 概念（默认）/ ``region`` 地域 / ``industry``
            新版行业（84 个，较旧口径多覆盖北交所标的）。

        Returns
        -------
        ``[{"code": "gn_hwqc", "name": "华为汽车", "count": 97,
        "avg_price": 25.04, "change": -0.26, "pct_change": -1.02,
        "volume": 1946565402, "amount": 34277211962,
        "leader": "sh605068", "leader_pct": 9.984,
        "leader_price": 20.38, "leader_change": 1.85,
        "leader_name": "明新旭腾"}, ...]``
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self.build_url([], board=board)
        return self.parse_boards(
            self._request_text(url, encoding="gbk", err_msg="新浪板块列表请求失败")
        )

    def parse_boards(self, text: str) -> list[dict[str, Any]]:
        """解析 ``var S_Finance_bankuai_{param} = {...}``（gbk 文本）。"""
        if self.VAR_PREFIX not in text:
            raise SourceDeprecated(
                "新浪板块列表响应格式变更（未找到声明前缀）",
                context={"source": SINA, "sample": text[:120]},
            )
        body = text.split("=", 1)[1].strip().rstrip(";")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "新浪板块列表非 JSON",
                context={"source": SINA, "sample": body[:120]},
                cause=exc,
            ) from exc
        out: list[dict[str, Any]] = []
        for raw in data.values():
            cols = str(raw).split(",")
            if len(cols) < 13:
                continue
            out.append(
                {
                    "code": cols[0],
                    "name": cols[1],
                    "count": _i(cols[2]),
                    "avg_price": _f(cols[3]),
                    "change": _f(cols[4]),
                    "pct_change": _f(cols[5]),
                    "volume": _i(cols[6]),  # 股（新浪口径）
                    "amount": _f(cols[7]),  # 元
                    "leader": cols[8],
                    "leader_pct": _f(cols[9]),
                    "leader_price": _f(cols[10]),
                    "leader_change": _f(cols[11]),
                    "leader_name": cols[12],
                }
            )
        if not out:
            raise SourceDeprecated("新浪板块列表为空", context={"source": SINA})
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []  # 板块列表非 Quote 语义


# --------------------------------------------------------------------------- #
# 新浪：板块成分
# --------------------------------------------------------------------------- #
class SinaBoardMemberSource(BaseWebSource):
    """新浪板块成分（``Market_Center.getHQNodeData``，node=new_xxx 分页）。"""

    ALL_MARKET_URL = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData"
    )

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.ALL_MARKET_URL

    def fetch_members(
        self,
        node: str,
        *,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> list[Quote]:
        """分页拉取板块成分行情（复用全市场行解析器）。

        Parameters
        ----------
        node:
            板块代码，来自 :meth:`SinaIndustryBoardSource.fetch_boards`
            的 ``code`` 字段（如 ``new_blhy``）。
        page_size:
            每页条数（上限 100）。
        max_pages:
            页数上限；``None`` 拉到底。
        """
        from .sina.adapters import SinaSource

        delegator = SinaSource(
            client=self.client,
            headers=self.headers,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        return delegator.fetch_all(node=node, page_size=page_size, max_pages=max_pages)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []


# --------------------------------------------------------------------------- #
# 腾讯：板块排行（行业 / 概念）
# --------------------------------------------------------------------------- #
class TencentBoardRankSource(BaseWebSource):
    """腾讯板块排行（含领涨股；行业 / 概念 / 地域）。"""

    BASE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/rank"

    #: 板块类型 → t 参数（averatio=均价排序，腾讯固定格式）
    BOARD_TYPES = {"industry": "01/averatio", "concept": "02/averatio", "region": "03/averatio"}

    @property
    def source_name(self) -> str:
        return TENCENT

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        board = self.BOARD_TYPES.get(kwargs.get("board", "industry"), self.BOARD_TYPES["industry"])
        limit = int(kwargs.get("limit", 20))
        page = int(kwargs.get("page", 1))
        return f"{self.BASE}?l={limit}&p={page}&t={board}&o=0"

    def fetch_boards(
        self, board: str = "industry", *, limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块排行（按均价涨跌）。

        Parameters
        ----------
        board:
            ``industry`` 行业 / ``concept`` 概念 / ``region`` 地域。
        limit:
            每页条数。
        page:
            页码（从 1 开始）。

        Returns
        -------
        ``[{"code": "pt01801767", "name": "数字媒体", "price": 1633.43,
        "change": 109.51, "pct_change": 7.19, "leader": "sz300413",
        "leader_name": "芒果超媒", "leader_pct": 20.0,
        "pct_change_5d": 8.92, "pct_change_20d": 2.58}, ...]``
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self.build_url([], board=board, limit=limit, page=page)
        return self.parse_boards(
            self._request_text(url, encoding="utf-8", err_msg="腾讯板块排行请求失败")
        )

    def parse_boards(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "腾讯板块排行非 JSON",
                context={"source": TENCENT, "sample": text[:120]},
                cause=exc,
            ) from exc
        if payload.get("code") != 0:
            raise SourceDeprecated(
                f"腾讯板块排行错误码 {payload.get('code')}",
                context={"source": TENCENT},
            )
        rows = payload.get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append(
                {
                    "code": r.get("bd_code", ""),
                    "name": r.get("bd_name", ""),
                    "price": _f(r.get("bd_zxj")),
                    "change": _f(r.get("bd_zd")),
                    "pct_change": _f(r.get("bd_zdf")),
                    "leader": r.get("nzg_code", ""),
                    "leader_name": r.get("nzg_name", ""),
                    "leader_price": _f(r.get("nzg_zxj")),
                    "leader_change": _f(r.get("nzg_zd")),
                    "leader_pct": _f(r.get("nzg_zdf")),
                    "pct_change_5d": _f(r.get("bd_zdf5")),
                    "pct_change_20d": _f(r.get("bd_zdf20")),
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []


# --------------------------------------------------------------------------- #
# 东财：板块列表（行业 / 概念 / 地域）
# --------------------------------------------------------------------------- #
class EastmoneyBoardSource(_EastmoneyJson):
    """东财板块列表与成分（push2 clist 接口）。

    板块代码形如 ``BK0475``；列表按 ``fs=m:90+t:{2|3|1}``，
    成分按 ``fs=b:BKxxxx``。
    多主机容灾继承 :class:`~tstdx.web.base._EastmoneyJson`（push2 →
    92.push2 → push2delay 延时镜像），失败计数接入下线检测。
    """

    FIELDS = "f12,f14,f2,f3"
    #: 板块类型 → fs 参数
    BOARD_TYPES = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}

    @property
    def source_name(self) -> str:
        return EASTMONEY

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        board = self.BOARD_TYPES.get(kwargs.get("board", "industry"), self.BOARD_TYPES["industry"])
        page = int(kwargs.get("page", 1))
        limit = int(kwargs.get("limit", 100))
        return (
            f"{self.BASE}?pn={page}&pz={limit}&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs={board}&fields={self.FIELDS}"
        )

    def fetch_boards(
        self, board: str = "industry", *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块列表（按涨跌幅降序）。"""
        self.rate_limiter.acquire(self.source_name)
        fs = self.BOARD_TYPES.get(board, self.BOARD_TYPES["industry"])
        payload = self._get_json(
            f"/api/qt/clist/get?pn={page}&pz={limit}&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs={fs}&fields={self.FIELDS}"
        )
        return [
            {
                "code": r.get("f12", ""),
                "name": r.get("f14", ""),
                "pct_change": _f(r.get("f3")),
            }
            for r in self._diff(payload)
        ]

    def parse_boards(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "东财板块列表非 JSON",
                context={"source": EASTMONEY, "sample": text[:120]},
                cause=exc,
            ) from exc
        rows = (payload.get("data") or {}).get("diff") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append(
                {
                    "code": r.get("f12", ""),
                    "name": r.get("f14", ""),
                    "pct_change": _f(r.get("f3")),
                }
            )
        return out

    def fetch_members(
        self, board_code: str, *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块成分（``fs=b:BKxxxx``）。

        Returns
        -------
        ``[{"code": "600519", "name": "贵州茅台", "price": 1299.52,
        "pct_change": 0.47}, ...]``（价格为 0 表示盘后 ``"-"``）
        """
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(
            f"/api/qt/clist/get?pn={page}&pz={limit}&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs=b:{board_code}&fields={self.FIELDS}"
        )
        return [
            {
                "code": r.get("f12", ""),
                "name": r.get("f14", ""),
                "price": _f(r.get("f2")),
                "pct_change": _f(r.get("f3")),
            }
            for r in self._diff(payload)
        ]

    def fetch_stock_boards(self, symbol: str) -> list[dict[str, Any]]:
        """个股所属板块（push2 ``slist`` ``spt=3`` 接口，行业/概念/地域全量）。

        与 :meth:`fetch_members`（板块 → 成分）方向相反：个股 → 所属板块，
        覆盖该标的归属的全部 BK 板块（实测一只家电龙头约 38 个：行业 +
        概念 + 地域），结果按板块涨跌幅降序。

        Returns
        -------
        ``[{"code": "BK1102", "name": "空气能热泵", "pct_change": 0.84,
        "market": 90}, ...]``
        """
        from .base import to_eastmoney_secid

        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(
            f"/api/qt/slist/get?spt=3&fltt=2&invt=2&fields=f12,f13,f14,f3,f152"
            f"&pn=1&pz=200&po=1&np=1&fid=f3&secid={to_eastmoney_secid(symbol)}"
        )
        out: list[dict[str, Any]] = []
        for r in self._diff(payload):
            out.append(
                {
                    "code": r.get("f12", ""),
                    "name": r.get("f14", ""),
                    "pct_change": _f(r.get("f3")),
                    "market": _i(r.get("f13"), 90),
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []
