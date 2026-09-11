# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- P14 数据源补全（ESG 评级 / 筹码分布）：新增 `tstdx/web/esg.py`（新浪 ESG 评级，
  覆盖 13 家机构聚合、季度历史、MSCI 全市场 5200+ 只、华证全市场 6300+ 只，
  含 E/S/G 三维度分项评分），新增 `tstdx/web/chip.py`（东财筹码分布，基于
  push2 资金流接口计算 accumulation_ratio 筹码集中度与 concentration_trend
  吸筹/派发趋势），新增 `tstdx/web/_facade_mixin_p1.py` 与
  `UnifiedQuoteAPI` 暴露 `esg_rating` / `esg_history` / `esg_ratings_all` /
  `chip_distribution` / `chip_distributions` 共 5 个方法；新增
  `tests/web/test_p1_sources.py`（30 例全离线测试）。

- P13 数据源补全：新增 `tstdx/web/fin_report.py`（三大财务报表：资产负债表 / 利润表 /
  现金流量表，东财 datacenter-web `RPT_F10_FINANCE_GBALANCE/GINCOME/GCASHFLOW`，
  SECUCODE 过滤），新增 `tstdx/web/governance.py`（治理四报表：董监高持股
  `RPT_EXECUTIVE_HOLD_DETAILS` / 股东增减持 `RPT_SHARE_HOLDER_INCREASE` /
  公司概况 `RPT_F10_BASIC_ORGINFO` / 券商评级 `RPT_WEB_RESPREDICT`），门面暴露
  `balance_sheet` / `income_sheet` / `cash_flow` / `fin_report` /
  `executive_holds` / `shareholder_changes` / `org_profile` / `org_profiles` /
  `rating_forecast` / `rating_consensus` 共 10 个方法；新增
  `tests/web/test_fundamental_sources.py`（37 例全离线测试）。

- Web 源对标 `Micro-sheep/efinance` 全量补齐：新增 `tstdx/web/efinance_fund.py`
  （天天基金移动端 7 类基金扩展数据）、`tstdx/web/efinance_deriv.py`
  （东财 push2 期货/债券实时、快照、K 线、逐笔），并在 `UnifiedQuoteAPI` 暴露
  `stock_base_info` / `stock_all_performance` / `stock_report_dates` / `ipo_review` /
  `fund_base_info` / `fund_manager` / `fund_holdings` / `fund_period_change` /
  `fund_asset_allocation` / `fund_industry_distribution` / `fund_public_dates` /
  `futures_base_info` / `futures_realtime` / `futures_kline` / `futures_trades` /
  `bond_realtime` / `bond_base_info` / `bond_kline` / `bond_history_bill` /
  `bond_today_bill` / `bond_trades` 共 21 个方法。
- 对标 `tiantianlaolao/astock-data-toolkit` 新增基本面衍生域：`tstdx/web/astock_toolkit.py`
  （东财 `RPT_SHAREBONUS_DET` / `RPT_VALUEASSESS_DET` / `RPT_CAPITAL_PARTICIPATION_DET` /
  `RPT_F10_FINANCE_MAIN`），暴露 `dividend_history` / `stock_valuation` /
  `holder_changes` / `financial_abstract` / `announcements`。
- 新增 `tstdx/web/news.py` 机构调研纪要源（东财数据中心 `RPT_ORG_SURVEY`），
  暴露 `research_visits`；补齐 niuniu 审计发现的资讯类硬缺口。
- 补齐 efinance 批量能力：`fund_base_info_multi`（`fund_base_info` 批量别名）与
  `bond_all_base_info`（`bond_base_info` 全市场别名）；并把 `_facade_mixin_info.py`
  已存在但未暴露的 `free_holders` / `holder_num` 提升到 `UnifiedQuoteAPI`。
- 天天基金深度扩展（对标移动端全端点，补齐排行/快照/经理/公司/搜索 5 大子域）：
  新增共享工具 `tstdx/web/_mob_fund.py`（设备指纹 + 公共参数 + 多 host 容错 +
  `apply_fields` 字段归一化），新增三个源：`tstdx/web/fund_rank.py`
  （排行/实时快照替代已下线的 `fundgz`/净值/详情/评级/走势 7 方法）、
  `tstdx/web/fund_manager.py`（基金经理 JSON 版，含夏普/回撤/胜率/波动率打分卡，
  替代脆弱的 `fundf10` HTML 解析）、`tstdx/web/fund_company.py`
  （公司档案/旗下基金/规模变动/画像 + `fundts` 搜索），门面暴露
  `fund_rank` / `fund_snapshot` / `fund_nav_history_mob` / `fund_detail` /
  `fund_rating` / `fund_yield_curve` / `fund_rank_trend` / `fund_manager_list` /
  `fund_manager_profile` / `fund_manager_yield` / `fund_manager_eval` /
  `fund_manager_style` / `fund_companies` / `fund_company_archives` /
  `fund_company_funds` / `fund_company_scale` / `fund_company_base_info` /
  `fund_search` 共 18 个方法；新增 `docs/tiantian_fund_extensions.md`。
- 新增测试：`tests/web/test_efinance_fund.py`（7）、`tests/web/test_efinance_deriv.py`（9）、
  `tests/web/test_efinance_facade.py`（13）、`tests/web/test_astock_toolkit.py`（6）、
  `tests/web/test_news.py`（10）、`tests/web/test_fund_v2.py`（50），共 95 例全离线测试。
- 新增文档：`docs/efinance_parity_gap_analysis.md`、`docs/astock_toolkit_parity.md`、
  `docs/niuniu_coverage_audit.md`。

### Fixed

- 修复 `EastmoneyNoticeSource` / `EastmoneyResearchSource` 的 BASE 路径段丢失问题：
  `_get_json` 只拼接主机，`fetch_notices` / `fetch_reports` 此前把裸相对路径直接传给
  基类，导致真实请求打到 `https://reportapi.eastmoney.com?pageSize=...` 而 404。
  现改为传入 BASE 的路径段部分，主机只拼接一次。

## [1.0.0] - 2026-09-09

这是 tstdx 的首个正式稳定版，发布包同时提供 wheel 与源码包，支持 Python 3.10–3.13。

### Changed

- 明确 `ConnectionPool` / `AsyncConnectionPool` 的主站生命周期契约：后台测速只更新
  排名，真实请求健康只更新 live health；generation/lease 保护在飞请求，旧代完成结果
  不再污染新代主站状态。
- 统一同步/异步 half-open circuit 的单探测门禁、连接复用、空闲回收、心跳与
  `update_hosts` 热更新语义。

### Fixed

- 修复 `ConnectionPool` 主站生命周期竞态：后台测速、真实请求健康、half-open
  circuit、generation/lease、心跳、空闲回收与 `update_hosts` 连接复用现在互不
  覆盖；活动请求完成后，旧 generation 的结果不会污染新主站状态。
- 同步/异步连接池统一单探测 half-open 门禁，并区分测速 RTT 与 real-request
  health RTT；新增交错时序和并发回归测试。
- 修复 F10 客户端 0x06B9 文件分块响应解析：F10 facade 现在复用规范的文件下载解析器，
  空响应明确抛出 `DataError`。

### Release verification

- 全量 `pytest -q` 通过。
- `ruff check tstdx tests` 与 `python -m compileall -q tstdx tests` 通过。
- 发布脚本完成 wheel/sdist 构建、临时虚拟环境安装与 import 冒烟。
- 产物名称：`tstdx-1.0.0-py3-none-any.whl`、`tstdx-1.0.0.tar.gz`。

### Deprecation Timeline（P13-F：把 v1.4.0 Deprecated 章节的窗口期落到版本号）

- **v1.5.0（下一个 minor）**：`tstdx.native` 仍可用，行为不变；导入即发
  `UserWarning`（P14-D 强告警升级：默认可见，不再走 `DeprecationWarning`
  默认过滤规则）+ `logging.warning` 双通道；此版本为迁移**最后窗口**——
  所有下游需在 v1.5.0 之前切换到 `tstdx.codec` / `tstdx.io` 对应函数。
- **v1.6.0**：`tstdx.native` **正式删除**——模块文件移除，导入即
  `ImportError`（不再走纯 Python 回退）。同期移除 v1.4.0 Deprecated 章节
  中列出的兼容垫片；`pyproject.toml` 的 `[project.urls]` 若引用
  native 相关文档链接同步清理。见 P15-A 批次。

### Changed

- **P14 批次部分落地（2026-09-07）**：
  * **P14-D**：`tstdx/native.py` 弃用告警从 `DeprecationWarning` 升级为
    `UserWarning`（默认显示）+ `logging.warning` 双通道，v1.5.0 强告警。
    迁移指引文案显式指向 `decode_tdx_float / read_day_file /
    parse_kline_payload` 三个 Python 等价函数。
  * **P14-E**：`AsyncUnifiedQuoteAPI` docstring 显式声明「SourceUnavailable
    语义继承」——异步门面经 `asyncio.to_thread` 桥接同步门面实例，
    P13-A 的 CommandOffline/AllHostsUnreachable → SourceUnavailable 转换
    **自动继承**（异步层不重复实现以避免双写漂移）；
    `tests/facade/test_w11_w12_w13.py::TestP14EAsyncFacadeSourceUnavailable`
    3 个测试锁定契约（arun 透传 / aquery 折叠 / docstring 声明）。
  * **P14-F**：验证 `AsyncQuoteStream` 已在 M1 阶段完成 engine 组件接线
    （`ReconnectPolicy` + `BackpressureQueue` + 共享 `_resolve_payload`
    处理 GapUnfilledError + `Subscription._merger` DeltaMerger），
    `tests/streaming` 41 passed 全绿；文档状态更新为已闭环。
  * **P14-D2 覆盖率门禁对齐**：`pyproject.toml` `tool.coverage.report.
    fail_under` 从 75 提升到 77，与 CI `--cov-fail-under` 对齐，消除
    「门禁口径漂移」。同时新增 `[project.optional-dependencies].dev`
    extra（pytest / pytest-cov / pytest-asyncio / ruff / mypy / hatchling），
    开发者可 `pip install -e ".[dev]"` 获得与 CI 一致的本地体验。
  * **P14-A2 CLI hosts audit 子命令**：`tstdx hosts audit` 接入
    `scripts/audit_hosts.py`，用户无需调用脚本即可做 5 族巡检；
    参数与脚本一致（`--family / --timeout / --workers / --report /
    --ranking-file / --strict / --quiet / --no-save-ranking / --hosts-file`）。
  * **P14-A3 外部候选注入**：`audit_hosts.py` 新增
    `load_external_hosts(path)` 函数，支持三种格式：
    1. 纯文本（每行 `host:port [family] [name]`）；
    2. JSON list（`[{"host","port","family","name"}]`）；
    3. JSON dict（`{"quotation": [...], "ex_quotation": [...]}`）。
    family 支持官方常量与别名（quotation/standard/std/7709/extended/
    ex/7727/mac_quotation/mac/goods/f10）；错误格式抛 `ValueError`。
    社区贡献主站入口 P15-C 前置就绪。
  * `docs/POTENTIAL_ISSUES_AND_PLAN.md`：P14 批次状态表重构（🔧/⏳/✅
    三态），新增 P15 批次规划（Native 删除执行 / 性能基线 / 社区主站
    入口 / 覆盖率 77→80）。

- **P15 批次开始（2026-09-07）**：
  * **P15-D1 hosts_audit 单测补齐**：新增 `tests/test_hosts_audit.py`
    22 项测试，覆盖三条主链路——
    1. `TestCliHostsAudit`：CLI `hosts audit` 子命令注册与全量参数
       解析（`--family / --timeout / --host / --workers / --report /
       --ranking-file / --strict / --quiet / --no-save-ranking /
       --hosts-file`）；
    2. `TestLoadExternalHosts`：`load_external_hosts()` 三格式（纯文本 /
       JSON list / JSON dict）+ 家族别名（quotation/standard/std/7709/
       extended/ex/7727/mac_quotation/mac/goods/f10）归一 + 去重 +
       错误路径；
    3. `TestAuditFamilyWithExternalHosts`：`audit_family(additional_hosts=...)`
       去重注入 + baseline 回归（无 additional_hosts 时行为不变）。
    动态导入 `scripts/audit_hosts.py`（`sys.path.insert` + `importlib`），
    不依赖 CLI 主入口。

- **工程与文档（2026-09-07）**：
  * README.md 更新：版本 1.2.0 → 1.4.0，补 P13/P14/P15 特性说明
    （主站池治理 / 异步门面 SourceUnavailable 契约 / dev extra /
    CLI hosts audit 示例 / 巡检流程 / 文档导航新增
    POTENTIAL_ISSUES_AND_PLAN.md）。
  * `pyproject.toml` `[project.urls]` 修正到实际仓库
    （`coeasy/tstdx` → 原为 `tstdx/tstdx` 占位）——PyPI 元数据一致。
  * `.gitignore` 补充 `.workbuddy/` / `.test_tmp/` / `_cov.txt` /
    `base_orig_tmp.py`，杜绝本地会话产物与临时文件入库。
  * 项目首个 commit `f73ef61` 已推送至 `github.com:coeasy/tstdx` main
    分支（2061 files, 133,175 insertions）。

- **P12 数据源扩展与实测修复（真实环境验证）**：
  * **板块资金流排行**：新增 `sector_flow(board, sort, limit)`（session/facade
    双入口 + CLI `tstdx sector-flow [--board industry|concept|region]
    [--sort main_net]`）；实测行业/概念/地域真实取数通过（传媒主力净流入
    61.7 亿、SPD概念净占比 18.56%）。
  * **实测修复排行字段陷阱**：`EastmoneyRankSource.fetch_rows` 按资金流排序
    （main_net/main_ratio）时自动附带 f62/f184/f66/f72/f78/f84 字段——
    此前服务端排序正确但响应缺字段、行值静默为 None。
  * **TDX 扩展市场目录上提 facade**：`UnifiedQuoteAPI.ex_market_list() /
    ex_instruments(market)`（7727 品种目录）。**已知问题**：内置 7727
    扩展行情主站候选池 4 台全部超时（2026-09-06 实测），目录接口报
    `AllHostsUnreachable`；错误路径与提示正常，待更新主站池后可用。
  * **通用 datacenter 报表直查接口** `dc_query(report, symbol=..., filters=...)`
    + `dc_reports()` 白名单暴露（`WebQuoteSession`/`UnifiedQuoteAPI` 双入口）——
    白名单内任意报表直查（dividend 分红送配 `RPT_SHAREBONUS_DET` 已实测入列，
    2026-09-06 实测 performance 37 字段/holder_num/dividend 真实取数通过）；
    字段原样透传不做单位翻译（各报表语义差异大，避免误导），个股过滤键按报表
    自动映射（`_DC_SYMBOL_FILTER_KEYS`），无个股过滤键的报表（ipo）忽略 symbol。
  * **新增东财融资融券个股明细源** `web/adapters_margin.py`
    （datacenter `RPTA_WEB_RZRQ_GGMX`，2026-09-06 实测真实取数通过）——
    `EastmoneyMarginSource.fetch_margin`（DATE 倒序、days 截断、金额单位元、
    3/5/10 日差分字段入 `extra`）+ 源注册 `margin` + identity normalizer +
    `WebQuoteSession.margin` / `UnifiedQuoteAPI.margin` / CLI
    `tstdx margin <symbol> [--days N]` + 注册表一致性门禁扩项。
  * **实测发现并修复**：`fundgz.1234567.com.cn` 实时估值接口已下线
    （返回 404 HTML 页，官方 App 接口需设备签名鉴权）——
    `FundSource.fetch_estimate` 检测死接口后显式抛
    `SourceDeprecated`（保留 jsonp 解析层以备接口复活），
    `FUND` spec notes 同步；`fund_nav_history`/`fund_list` 实测仍可用。
  * **实测发现并修复**：`WebQuoteSession` 缺 context manager 协议
    （有 `close()` 无 `__enter__/__exit__`）——`_SessionBase` 补齐，
    `with web_session("sina") as s:` 可用。
  * **百度源扩展调研结论**（写入 `adapters_baidu.py` docstring）：指数行情
    不可行——`isIndex=true` 实测无效（000001 恒返回深市个股，前缀/1A0001/
    999999 均空结果，同码歧义无解）；smartbox 联想端点空响应；资金流
    `vapi/v1` 403。百度源现有能力（A 股 K线/分时/逐笔/五档）维持。
  * 新增 `tstdx/__main__.py`：`python -m tstdx` 与控制台脚本等价。
  * 新增测试：`tests/web/test_margin.py`（罐头解析/days 截断/空标的/注册表）
    + fund 死接口用例 + session CM 用例；真实取数复验通过。

- **收尾项（审计清单闭环）**：错误树梳理完成——44 类 E1-E9 九域体系经
  复核**无语义重叠对**（易混对按域区分并写入对照表）；新增
  [docs/errors.md](errors.md)（错误树速查 / RetryAdvice 字段契约与消费方 /
  扩展规则 / 上层边界约定），`errors.py` docstring 与 README 文档导航同步指向。

- **收尾项（审计清单闭环）**：错误树梳理完成——44 类 E1-E9 九域体系经
  复核**无语义重叠对**（易混对按域区分并写入对照表）；新增
  [docs/errors.md](errors.md)（错误树速查 / RetryAdvice 字段契约与消费方 /
  扩展规则 / 上层边界约定），`errors.py` docstring 与 README 文档导航同步指向。

- **v11 重构（P11 批次，全部落地）**：
  * **P11-0 lint 门禁清零**：ruff check 从 93 项清至 0（修复 P10-3 拆分遗留的
    `HttpResponse` F821 未定义名；10 处导入排序自动修复；3 个再导出门面模块
    （`cli/__init__`、`client/__init__`、`web/base`）以 per-file-ignores 声明
    F401 豁免；8 处真死导入逐一核对后删除；12 文件格式化对齐）。
  * **P11-1** `integration/mcp_server.py`（1062 行）拆为 `integration/mcp/`
    子包（`_common`/`_tools_impl`/`_tools_spec`/`_server`，依赖单向）+ 135 行
    兼容门面；23 工具 schema 逐字不变，logger 名保持，stdio 往返实测正常。
  * **P11-3** `protocol/parsers/std7709.py`（1216 行）拆为
    `_std7709_common`（常量/请求体构造）/`_std7709_quote`（0x044E/0x053E/0x0530）/
    `_std7709_bars`（0x052D/0x000F/0x0010）+ 107 行注册与再导出门面；
    6 个 L1 解析器注册路径与全部导入不变，golden/对抗门禁守护。
  * **P11-2 决议：transport 不拆**。`pool.py`（Slot/PoolStats + 单一
    ConnectionPool 类）与 `async_.py`（异步镜像域）各自职责单一、类内强耦合，
    无 web/base.py 式的多职责混杂，按 v10 方案「不强拆」条款保留原样。
  * 千行大文件降至 2 个（transport/async_ 831、transport/pool 800，均为
    单一职责域，保留）。

- **v10 重构（REFACTOR_PLAN_v10，全部落地）**：
  * **P10-1** 按 [ADR-007-010](adr/ADR-007-010.md) 删除 `CredentialStore`
    （三级凭据存储，526 行，全库零调用方）：模块/导出/测试/docs/白名单同步清理；
    `tstdx.security` 包保留并注明重新设计条件。
  * **P10-2** facade 路由壳拆分：W11/W12 路由选择与熔断基础设施收口至
    `facade/routing.py`（`RouteSelector`），`api.py` 1297→1129 行；
    实例状态名（`_route_fail_counts`/`_route_cooldown_until`）与全部公开面不变。
  * **P10-3** `web/base.py`（1229 行）四分：`_base_core`（生命周期+模板方法）/
    `_base_retry`（重试退避+失败双桶）/`_base_http`（HTTP 客户端+限流桶）/
    `_base_em`（`_EastmoneyJson` 主机池）；`web/base.py` 保留为组合 re-export
    门面，导入路径全兼容（`tests/seams` W5/W10 契约测试同步指向新模块）。
  * **P10-4** 文档清理：`sink/` docstring 改引 `tstdx.output`；README 特性表
    补 streaming 定位（ADR-011）。
  * **P10-5** route_parity 偶发失败定性：v9 期间代理并发改写 `web/__init__.py`
    时测试运行于半写状态所致（环境性，非测试缺陷）；复跑与终验均未复现，
    测试 fixture 已有隔离，不修改。
  * 千行大文件从 5 个降为 3 个（std7709 解析器/transport/mcp 明确列为 v11 候选）。

- **v9 重构（REFACTOR_PLAN_v9，决策已确认，全部落地）**：
  * **Q1 路由链合并**：`UnifiedQuoteAPI` 的 quotes/bars 各路由取数委托
    `DataSourceRouter` 单源调用（`order` 覆盖 + `default_empty_ok`/`adjust`/
    `start` 增量参数，向后兼容），消除双路由实现；W11 熔断壳/W12/W13 语义
    逐字保留；对拍基线 `tests/facade/test_route_parity.py`（11 例）+
    [ADR-012](adr/ADR-012-路由链合并口径对拍.md)。
  * **Q2 client 共享骨架**：33 组 sync/async 镜像方法一次性迁入
    `client/_mixin.py`（同步生成器 trampoline 模板，任意挂起点逐字等价）；
    sync.py 1035→643 行、async_.py 681→434 行，方法体去重约 -1100 行；
    奇偶门禁/monkeypatch 语义不变。
  * **Q4-1** web Source 层收尾：`web/_paginate.py` 共享分页拉取器
    （KlineSource/MinuteKlineSource 复用）；公告/研报源归入 `_EastmoneyJson`
    （主机池 failover 复用）。
  * **Q4-2** `tstdx/web/__init__` 惰性导入（PEP 562，55 符号 + `_ADAPTERS`
    惰性注册表）：首载子模块 24→3（-87.5%）。
  * **Q4-4** `tstdx/sinks` → `tstdx/output`（消除与 vipdoc 写回包 `tstdx.sink`
    的混淆）；旧名 shim 兼容一版（DeprecationWarning，v10 删除），16 处引用迁移。
  * **Q3/Q4-3** 异步门面紧凑设计文档化并冻结契约测试；[ADR-011](adr/ADR-011-streaming-native-处置决议.md)
    明确 streaming engine/push 与 native 定位（engine 实为 QuoteStream 内核，
    白名单注释修正）。
  * 全程行为与公开 API 兼容；全量 pytest / 对抗矩阵 / golden 门禁 / 严格
    可达性门禁通过。

### Added

- **v8 内部重构（REFACTOR_PLAN_v8，行为与公开 API 零变化）**：
  * 删除已废弃的 `tstdx.i18n` 兼容 shim（v7 起更名 `tstdx.charset` 并告警一版）；
    相关测试/文档/`_reach_allow.txt` 同步迁移至 `tstdx.charset`。
  * `facade/api.py`：新增 `_with` 资源作用域帮助方法，收口 21 处
    「构造客户端/Source → 调用 → close」薄委托模板（-85 行重复）。
  * `tstdx/cli.py`（1210 行）拆分为 `tstdx/cli/` 包：`_common`（表格输出/公共
    参数）+ `cmds_market` / `cmds_web` / `cmds_hosts`（按域命令）+ `parser`
    （argparse 装配）；`tstdx.cli:main` 控制台入口与 `from tstdx.cli import
    main/build_parser/_cmd_*` 导入路径全部不变。
  * `tstdx/client.py`（1715 行）拆分为 `tstdx/client/` 包：`sync.py`（TdxClient
    + 4 同步子类）/ `async_.py`（Async 镜像）/ `factory.py`（get_client）；
    37 个模块级符号逐一 re-export 对齐，`dispatch` 经包属性延迟解析保持
    monkeypatch 语义等价。
  * `tstdx/web/facade.py`（1336 行）拆为 `facade.py`（135 行）+ 3 个域 Mixin
    （`_facade_mixin_market/info/baidu`）；61 方法 AST 级 diff 为空，导入面不变。
  * `tstdx/trade/*`（交易协议模拟器）登记可达性白名单并在 README 标注定位
    （独立可选，不连真实券商）；`audit_reachability.py --strict` 恢复通过。
  * 新增 [docs/ARCHITECTURE_AUDIT_v8.md](ARCHITECTURE_AUDIT_v8.md)：功能完整性/
    链路贯通/不合理项审计结论与 v9 路线。
  * README 代码地图补 `sink/`（vipdoc `.day` 写回）与 `charset/` 条目，澄清
    `sink`/`sinks` 为职责不同的两个包。

### Added

- **P0-1 基金净值（G1，东财接口）**：新增 `tstdx/web/adapters_fund.py`
  `FundSource`（数据型源）——`fetch_nav_history`（api.fund.eastmoney.com/f10/lsjz，
  空净值→None 容错）、`fetch_estimate`（fundgz jsonp 解析）、`fetch_fund_list`
  （fundcode_search.js 全量 JS 数组解析，约 1.2 万条）；源注册 `fund`
  capabilities `fund_nav_history / fund_estimate / fund_list`（不参与行情降级）；
  `WebQuoteSession.fund_*` 便捷方法 + `UnifiedQuoteAPI.fund_nav_history /
  fund_estimate / fund_list` 门面 + CLI ``tstdx fund nav|estimate|list`` 子命令
  （`--page-size/--page-index` 翻页、`--json`）；补齐三方一致性门禁（`_ADAPTERS`
  注册 + `FundNormalizer` identity 显式登记 + `CAPABILITY_FACADE` 映射）；
  新增 `tests/web/test_fund.py` 9 例 + `tests/facade/test_fund_api.py` 5 例 +
  `tests/unit/test_cli_semantics.py` `TestP01FundSubcommand` 6 例（离线 mock）。

- **P0-2 指数成分股（G2，东财数据中心）**：新增 `tstdx/web/adapters_index.py`
  `EastmoneyIndexConstituentsSource`（继承 `EastmoneyDataCenterSource`）——
  `fetch_constituents` 经 datacenter `RPT_INDEX_TS_COMPONENT` 报表拉取，`TYPE`
  指数族过滤（沪深300/上证50/中证500/科创50/中证A50/中证A500/中证1000/深证50/
  深证100/北证50/上证180/中证A100/中证2000 共 13 族，2026-09 与中证官网 XLS
  交叉验证 5 族 jaccard=1.0）、单页 500、中证1000/中证2000 等大指数自动分页拉全量，
  `weight` 仅部分指数族提供；源注册 `index_cons` capability `index_constituents`
  （数据型源，不参与行情降级）；`WebQuoteSession.index_constituents` 便捷方法 +
  `UnifiedQuoteAPI.index_constituents` 门面 + CLI ``tstdx index constituents <code>``
  子命令（`--json`）；补齐三方一致性门禁（`_ADAPTERS` 注册 +
  `IndexConstituentsNormalizer` identity 显式登记 + `CAPABILITY_FACADE` 映射）；
  新增 `tests/web/test_index.py` 14 例 + `tests/facade/test_index_api.py` 2 例 +
  `tests/unit/test_cli_semantics.py` `TestP02IndexSubcommand` 3 例（离线 mock）。

- **B0 百度财经源收口**：`UnifiedQuoteAPI` 新增 `baidu_kline` / `baidu_minute`
  / `baidu_ticks` / `baidu_quote` 门面（转发 `WebQuoteSession` 便捷方法，
  try/finally 保证 close）；CLI 新增 ``tstdx baidu <symbol> [--kind kline|
  minute|ticks|quote]`` 子命令（`--period/--count/--end-time` K 线分页游标、
  `--limit` 逐笔、`--json`）；新增 `tests/facade/test_baidu_api.py` 6 例 +
  `tests/unit/test_cli_semantics.py` `TestB0BaiduSubcommand` 6 例（离线 mock）。

- **P2-1 TDX 交易协议探测**：新增可选模块 `tstdx/trade/`——`constants.py`
  （命令号/查询类别/价格类型/委托状态常量，生态公开约定，inferred 标注）+
  `errors.py`（`TradeError`/`TradeNotLoggedIn`/`TradeRejected`/
  `TradingUnavailable` 红线异常）+ `security.py`（口令 XOR 可逆混淆占位）+
  `frames.py`（8 字节帧头自洽编解码 + 登录/心跳/查询/委托/撤单 body）；
  `simulator.py` 纯内存模拟券商（资金/持仓/委托生命周期/冻结解冻）+
  `SimTransport` 协议回路；`client.py` `TradeClient`（登录/查询/委托/撤单/
  心跳 + `buy`/`sell` 助手）与 `SocketTransport`（**红线**：连接即抛
  `TradingUnavailable`，绝不连真实券商通道）；新增 `PROTOCOL_SPEC/TRADE/`
  6 份 draft spec（`0x0001_LOGIN` / `0x0002_HEARTBEAT` / `0x0003_LOGOUT` /
  `0x0100_QUERY` / `0x1000_SEND_ORDER` / `0x1001_CANCEL_ORDER`）；
  新增 `tests/trade/` 56 例（帧/口令/模拟器/客户端/红线）。

- **P1-2 运行时 bestip 测速热更新**：`ConnectionPool.update_hosts`
  （按新序重建主站池、复用既有连接、丢弃被移除主站，不重启不中断）+
  `AsyncConnectionPool.update_hosts` 异步镜像；`TdxClient.bestip()`
  （并行探测候选主站 RTT → 排序 → 热更新主站池）+ `AsyncTdxClient.bestip()`
  与 `open(bestip=True)` 触发；新增 `tests/client/test_bestip.py`。

- **P1-3 本地日线增量落盘**：`tstdx/sink/local_day.py` `LocalDaySink`——
  断点续传式把网络日 K 线增量写回 vipdoc `.day`（读末日期 → 向后回退
  多窗口拉取 → 已存在日期跳过，幂等；prev_close 链与 reader 语义一致；
  记录编码是 `DayBarReader` 解码的精确逆变换，写入后可直接回读）；
  `UnifiedQuoteAPI.sync_daily(symbols, root)` 批量门面；模块级
  `sync_daily` 便捷入口；新增 `tests/sink/test_local_day.py`。

- **N1 F10 栏目目录客户端方法**：`F10Client.catalog(symbol)`（0x0001，同步）+
  `AsyncF10Client.catalog`（异步镜像）——解析 F10 栏目目录输出
  `[{title, filename}]`；补齐 docstring 漂移（P1）；新增
  `tests/client/test_f10_client.py`（fake pool 注入离线用例）与
  `PROTOCOL_SPEC/F10/0x0001_F10_CATALOG.yaml`（spec\_audit 94.6%→94.7%）。

- **N1 F10 catalog 服务面接线**：`UnifiedQuoteAPI.f10_catalog`（tdx 路由校验）、
  HTTP `/f10/{symbol}/catalog`（fundamental 组 7→8，端点 42→43）、MCP
  `get_f10_catalog` 工具（12→13）、CLI `tstdx f10 <symbol> [--file <name>]`
  （缺省列栏目目录）；各层均带离线用例（facade 路由校验 / HTTP fake client /
  MCP stub / CLI 语义 / WS）。

- **P9 get\_client 显式拒绝**：`get_client` 对未知 `kind` 显式抛
  `ValueError`（不再静默回退 `TdxClient`），对齐 W12 显式拒绝风格。

- **N2 WS 方法面扩展（9→19）**：`JsonRpcHandler` 新增 `capital_changes`、
  `adjusted_bars`、`block_quotes`、`all_market`、`board_list`、`board_members`、
  `security_list`、`minute_klines`、`f10_download`、`f10_catalog` 共 10 个
  JSON-RPC 方法（跨源方法走 `UnifiedQuoteAPI`，其余复用客户端），同一错误映射；
  `tests/integration/test_ws_server_f4.py` 用 FakeClient/FakeFacade 覆盖。

- **M5 urllib 连接复用**：`web/base.py` 新增 `StdlibPooledClient`——基于
  `http.client.HTTPConnection` 的零依赖 keep-alive 连接池（空闲超时 + 全池
  懒回收 + 线程安全），未装 httpx 时自动提升复用；新增
  `tests/web/test_pooled_client.py`。

- **M7 响应体解码零拷贝**：`HttpResponse.json()` 直接 `json.loads(bytes)`
  跳过中间 str，大响应（全市场 / K 线）内存与耗时双降。

- **U5 easyquotation 腾讯全市场**：`TencentSource.fetch_all()` 全程单源
  （`getBoardRankList` 枚举代码 + `qt.gtimg.cn` 批量行情，单批失败不拖垮
  全市场）；`WebQuoteSession.all_market()` 与 `UnifiedQuoteAPI.all_market()`
  均支持 `"tencent"` 源（`node="hs_a"/"cyb"`）。

- **F1 除权除息数据链路**：`tstdx/domain/finance.py`（gpcw 语义字段序 +
  0x0010 语义映射 + `map_finance_values` + `to_capital_changes` 共享转换器）；
  `FinanceReader.read_indicators()` gpcw 语义化解析；`finance_info()` 0x0010
  带字段名输出；`UnifiedQuoteAPI.adjusted_bars()` 复权生产入口（原始 K 线 +
  除权事件 → `AdjustEngine` 前/后/定点复权）。

- **Q4 内存基准 + Q2 基准入 CI**：`benches/bench_memory.py` tracemalloc 三类
  场景峰值基线（全市场 5400 只 / 1000×320 K 线 / vipdoc 8 万条扫描），
  `--synthetic` 离线可跑 + `--live` 真实源 + `--json` 归档；CI 新增
  `benchmark-smoke` job；新增 `tests/unit/test_bench_memory.py`。

- **F3 WS 订阅协议文档化 + 断线补洞**：`ws_server.py` 文档化订阅/推送协议
  （`quote_update` 周期推送 + `quote_snapshot` 断线补洞）；`JsonRpcHandler.
  on_message()` 上报新增订阅、`push_snapshot()` 订阅/重连即推全量快照；
  `serve_ws` 传输层在新增订阅时立即推送快照，客户端无需等下一轮周期推送
  即可对齐断线窗口内漏推区间。

- **F2 板块数据在线化**：`MacClient.block_list`（0x120F 板块列表
  `<H count>+<8s name><H id>`）/`block_members`（0x1210 成分股 `<6s code>`）
  同步+异步方法；板块行情解析器 0x2000（`pct_change`/`price`）。

- **F4 7727 扩展市场覆盖**：`ExMarketClient.ex_market_count`（0x0100）/
  `ex_market_list`（0x0101）/`ex_instrument_count`（0x0102）/
  `ex_instrument_list`（0x0103）与 `GoodsClient.goods_count`（0x0200）/
  `goods_list`（0x0201）同步+异步方法。

- **7727/GOODS/MAC 协议 golden structure 文档**：`PROTOCOL_SPEC/` 新增
  7727（15）/GOODS（11）/MAC（3）共 29 份 `status: inferred` spec YAML；
  `tests/protocol/test_7727_goods_mac_coverage.py` 用合成载荷锁定
  EXTENDED 0x0100–0x010E、GOODS 0x0200–0x020A、MAC 板块三族解析器布局
  （P1c 0x0104 per-record absolute vs 0x0202/0x052D running base 显式区分，
  GBK ≤8B 槽位防字段漂移，28B 记录守卫）；`spec_audit` 覆盖率 75%→94.6%。

## \[1.4.0] - 2026-09-05

M1/M1b 架构决断落地（用户拍板）+ 低级批次 14 项具名全清 + L5 冒烟扩展。

### Added

- **M1 接线落地（用户决断）**：`QuoteStream` / `AsyncQuoteStream` 内核改由
  `streaming.engine` 组件构成——DeltaMerger（增量合并，替换自维护 `_diff`）、
  BackpressureQueue（背压，`max_queue` 参数自弃用状态转真实接线：溢出丢
  最旧 + `BackpressureOverflow` 派发；`0` 关闭直发）、ReconnectPolicy
  （异步版退避对齐同步版：指数 + 抖动 + 上限）。

- **E6 错误分类兑现真实抛点**：不可解析订阅符号 → `SubscriptionError`；
  标的连续 3 轮未见于响应 → `GapUnfilledError`（可观测信号，不自动补数）；
  背压溢出 → `BackpressureOverflow`。errors.py 处置注释同步更新。

### Deprecated

- **M1b 废弃决断（用户拍板）**：`tstdx.native` 自 v1.4.0 弃用（§29
  DeprecationPolicy，2 个 minor 窗口后 v1.6.0 删除）——Rust 扩展源码已移除、
  模块恒为纯 Python 回退透传无加速实质；导入即发 `DeprecationWarning`，
  窗口期行为不变。迁移：直接使用 `tstdx.codec` / `tstdx.io` 对应函数。

### Fixed（低级 14 项具名摘要）

- `commands.py` 族账本注释漂移（36→39）+ F10 族端口显式声明；
  `_looks_like_uint16_date` 裁决保留标注。

- `framing.py` zip==unzip 声明未压缩但长度矛盾时的 zlib 包装兜底。

- `mac.py` MacNews 正文截断 warn\_ctx 留痕。

- `web/normalize.py` KlineNormalizer 对齐解析层内联缩放为 identity
  （防 A 股量 ×10000 双重缩放）；测试同步更新。

- `security/credentials.py` list\_keys 遮蔽警示 + get\_source 诊断路径。

- `domain/symbol.py` 白名单移除 000010/000016（深市同名个股冲突，
  裸写归深市个股，指数需显式前缀）。

- `feedback/reporter.py` 移除股票代码脱敏步骤（公开市场标识非 PII，
  6 步流水线重编号）。

- `sinks/__init__.py` to\_csv 原子写（mkstemp + os.replace）。

- `integration/ws_server.py` 模板层唯一 TdxClient（防每连接独立连接池）；
  `http_server.py` 逗号列表 100 上限（quotes/snapshot/auction\_snapshot）。

- `tests/unit/test_golden.py` 回放 ids 含 case 层（market/标的维度可见）。

## \[1.3.0] - 2026-09-04

第三方深度复审修复批次（6 项高危 + 33 项中级全清 + mypy 清零 + socket 冒烟），
详见 `docs/POTENTIAL_ISSUES_AND_PLAN.md` §2.4 逐项状态表。

### Fixed（高危 6 项）

- **web/base.py + sources/**__init__.py（W#1/W#2）：`bars()` 本地路由 4 分支
  错位（date 列同时映射 open/close，行情口径全错）；`sources` 降级链
  东财→web 切换时 `adjust` 被静默丢弃（复权请求偷换为不复权）。

- **streaming/engine.py（T#1）**：`BackpressureQueue.put` 在锁内调用 on\_drop
  回调——订阅者异常/重入死锁。

- **transport/async\_.py（T#2）**：`iter_frames` 与请求并发时帧交叉（读循环
  未持连接锁，多路复用下响应帧串流）；同步池同型修复；尾部残流显式告警。

- **integration/http\_server.py（S#1）**：8 个端点签名与 `TdxClient` 方法断裂
  （`/file_download`/`/download` 双端点直崩，goods/ex/mac 系 market 参数
  未拼符号），fakes 镜像旧签名掩盖——契约测试用 `inspect.signature` 锁定。

- **protocol/generic.py + registry.py（P#1/P#8）**：Sniffer 归档零调用点
  （三处文档承诺落空）接线；秒级时间戳同秒覆盖 + DRAFT 并发竞写修复
  （毫秒 + 序号探测 + 模块锁 + `open("x")` 原子创建）。

### Fixed（中级 27 项摘要）

- **transport**：`request_multi` 部分数据 `payload=b""` 清空矛盾（保留前缀
  有效数据）；async 限流 3 缺口收口 `_acquire_rate()`；`PushChannel.read`
  断线升级 warning + `last_error` 属性，数据损坏不再伪装「无帧」。

- **streaming**：`QuoteChannel.tick` 裸码/带前缀双向索引；`poll_delay` 兑现
  ReconnectPolicy 指数退避（旧恒 0）；`QuoteStream.stop` 关 client + 防
  start 孤儿双跑。

- **client**：`quotes_snapshot` L3 透传行不再强转 Quote（同步/异步均回退
  逐只 0x0530）；`min_confidence` 兑现显式拒绝语义。

- **web**：sina 源显式拒复权 + 门面复权请求自动路由东财（旧静默给原始价）；
  东财单候选空结果换 host 复核；市值单位跨源统一为元（腾讯亿/新浪万元换算）；
  `SuggestSource` URL 编码；限流桶速率显式更新（不再首建者胜）；12 处裸
  get/post 补 `_check_deprecated` 前置；fundflow 未知周期显式报错。

- **codec/protocol**：`build_request` pkg\_len uint16 溢出抛 `FramingError`；
  `decode_response_body` 统一转 `DecompressError`（golden 回放不再裸抛
  zlib.error）；`parse_f10_text` 中间 NUL 不再截断正文；
  0x0010/0x053E tier 账实对齐（不再缺省自报 L1 满置信）；0x06B9 死分支
  清除；4 处 price\_scale 死语句清除；`ctx["_meta"]` 回传通道修复。

- **observability**：`Registry.snapshot` Histogram 按序列输出（statsd 消费端
  同步适配）；statsd 首推累计口径经裁决文档化保留。

- **integration**：4MB 护栏补 chunked 旁路（无 Content-Length + chunked → 413）；
  TaskStore 取消后完成不回写；`GET /tasks` 列表只回摘要。

- **config**：`merge_config` 改深合并（与 `with_overrides` 同语义，不再吞键）。

- **profile**：hint 先验 +0.1 加权不得救活数据证据不足的候选（原始 conf
  < 阈值 60% 时加权不生效）。

- **facade**：`ex_quotes` 多标的逐只循环（旧把 Sequence 透传单只必崩）；
  `EastmoneyHistoryKlineSource.BASE` 补齐（旧 build\_url AttributeError）。

### Changed

- **mypy 基线清零（L1b）**：95 错误 → 0（100 源文件）。根因修复：`bars`/
  `quotes`/`quotes_concurrent`/`get_client` @overload Literal 收窄、`Self`
  返回类型、Registry 泛型 register；动态元编程 5 处定向 ignore。

- **CI 递减门禁（L4）**：type-check job 加 `--warn-unused-ignores`，顺带清掉
  12 处失效 ignore。

- **ws\_server** `quotes_snapshot` 语义随 client 同步。

### Added

- **L5 socket 级冒烟**（`tests/integration/test_l5_smoke.py`）：HTTP 真实
  loopback（uvicorn 真端口）、WS 双客户端并发订阅推送、MCP stdio 行协议往返。

- 高危修复回归：`tests/protocol/test_sniffer_loop.py`（7）、
  `tests/streaming/test_engine_backpressure.py`、`tests/sources/`
  adjust 一致性、`tests/unit/test_http_server.py` 契约锁定、
  `tests/facade/test_bars_local_route.py`。

## \[1.2.0] - 2026-09-03

工业审计（`docs/INDUSTRIAL_OPTIMIZATION_PLAN.md` v2）全量修复批次 F0-F5 + G/H 长尾。

### Fixed

- **facade/async\_api.py（F0-1，实测级）**：`AsyncUnifiedQuoteAPI` 的 10 个桥接方法
  把同步门面的**实例方法当静态函数**经 `asyncio.to_thread` 调度，真实调用必
  `TypeError`（测试打桩 `staticmethod` 掩盖了缺陷）。重构为实例持有同步门面
  （`__init__(*, sync_api=None, **sync_kwargs)`，惰性创建），全部桥接走绑定方法；
  新增 `close()` 与 `with`/`async with` 上下文管理；`arun` 拒绝下划线方法名。
  回归纪律入测试：只允许打桩传输边界（假 `TdxClient`），禁止打桩被测方法本身
  （`tests/unit/test_fix_f0.py`，13 用例）。

- **web/base.py（F0-2，实测级）**：`HttpxClient` 补齐 `post()`（签名对齐
  `UrllibClient.post`，httpx 异常统一包装 `WebSourceError`）——按推荐
  `tstdx[web]` 安装（httpx 可用）时人气榜/CLI/MCP 全链 `NotImplementedError`
  必崩的断头路接通。

- **config/loader.py（F0-3，实测级）**：① `TSTDX_RATE_LIMIT_*` 段名按
  `Config._SUBCONFIGS` 最长前缀切分（唯一带下划线的段名此前永远无法表达）；
  ② bool 词表收紧为 `true/yes/on` 与 `false/no/off`；③ `config_from_env`
  未知段 `RuntimeWarning` + 跳过（此前任意未知 `TSTDX_*` 直接崩启动）。

- **security/credentials.py（§2-26）**：损坏凭据文件不再被 set() 静默以空字典
  覆盖重建——先改名 `credentials.enc.corrupt-<UTC>` 隔离原件备抢救，隔离失败
  放弃写；`_load_file` docstring 与实现契约对齐（损坏抛 `ConfigError`，
  降级决策权在各调用点）。回归 `tests/security/`（10 用例）。

### Changed（含契约变更）

- **config env 词义**：`TSTDX_*` 值为 `"1"/"0"` 时解析为 **int**（此前被 bool
  抢跑，`TSTDX_CORE_MAX_RETRIES=1` 报「必须是数值，收到 bool」）。确属缺陷修正；
  需要 bool 的用 `true/false`。

- **CredentialStore.set 写策略**：keyring 可用且写入成功时**不再**重复落 XOR
  混淆文件（弱文件恒持有全部密钥等价物与「优先 keyring」叙事冲突）。
  keyring 失败/缺失仍回退文件。读路径三级回退语义不变。

- **AsyncUnifiedQuoteAPI 构造**：新增 `sync_api` 注入参数与 `close()`/上下文
  管理；类级 `_SYNC` 共享实例移除（实例隔离）。

### Removed

- `tstdx/_async_bridge.py`（390 行）：`run()` 对常驻 loop 再 `run_until_complete`
  必抛 `RuntimeError` 且全库零消费——已坏孤儿，删除（§4 决议）。

- `tstdx/protocol/requests.py`：与 `client.py` 内联构包双源漂移（80 只上限等），
  全库含测试零 import——删除（§4 决议）。

- `tstdx/protocol/parsers/_generated.py`：注册链断裂幻影（`parsers/__init__`
  不导入、`tier="TIER_L1"` 字符串 bug）——删除；codegen 输出路径另行调整。

### Added

- **F5 审计门禁固化**：
  `tests/adversarial/test_full_matrix.py`——对抗 payload 全矩阵永久回归
  （9 畸形字节 × 全账本，strict-L1 原生异常逃逸=0 / 降级路径逃逸=0 /
  单条解析 <1s），含 canary 自证「门禁有牙齿」；
  `scripts/audit_reachability.py` + `scripts/_reach_allow.txt`——AST 模块可达性
  扫描（`_LAZY` 边 + 父包传播 + `python -m` 入口种子），孤儿须登记处置理由；
  `Makefile` 新增 `gates`（六步统一门禁序列：lint→format→全量→对抗→golden
  三旗标→可达性）与 `audit-reachability`/`audit-adversarial` 目标。

- **F1 流式与客户端（S 域）**：
  `streaming/__init__.py` C1 订阅符号经 `domain.symbol` 归一裸码查表/作 diff 键
  （`sh600519` 静默零数据终结；异步镜像同修）；C4 `subscribe` 对称加锁、
  流线程顶层 `except Exception` 兜底走 `on_error`+退避（线程无声死亡终结）、
  异步 `on_error` 套 suppress；**新发现并修复**：异步流对 `asyncio.Event.wait`
  传超时参数必 `TypeError`（协程一进空转分支即死）三处改 `asyncio.sleep`。
  `client.py` C3 `last_errors` `__init__` 显式声明+锁保护，`quotes_concurrent`
  错误收集调用局部化（并发错误确定性蒸发终结；`quotes()` 新增内部 `_collect`
  参数，公开签名不变——外部 monkeypatch `client.quotes` 的 fake 需吸收该 kwarg）；
  P1a `bars(index=True)` 经 dispatch ctx 直达解析器（指数 K 线 4 字节尾端到端
  消费，`facade/market.index_bars` 实装；布局仍 ⚠️ 待真机 golden 定标）；
  `quotes_snapshot` ≤80 只分片+失败回退逐只+日志计数（>255 struct.error 绕回退
  终结）；`export_security_list` 截断告警；`quotes()` 单只坏标的不中断整批；
  `_quote_body` 反转语义注释纠偏+market clamp。`push.py` 订阅体符号归一
  （`600519.SH`→`519.SH` 坏字节终结）、bj→0 对齐协议链、真实 Connection
  鸭子适配。`streaming/engine.py` `diff_only` 死分支修正为真 diff 语义并
  标注 experimental；`QuoteStream.max_queue` 弃用告警；三模块日志接线。
  新增回归 32 条（`tests/unit/test_client_f1.py` 等），S 域 90 项独立复验全绿。

- **F3 门面一致性（A 域）**：`facade/api.py` W11 `_try_routes` 聚合
  `route_errors` 进最终异常 context + 逐路由 warning + 实例级轻量熔断
  （连续失败 ≥3 次冷却 30s，auto 序跳过、显式路由仍试、`reset_circuit()`
  公开；空结果≠失败/ImportError 重抛/只捕 Exception 三红线保持）；
  W12 route 参数治理：minute/trades 接 tdx+web 双通路，15 个纯 tdx 能力
  方法显式传非 tdx 路由 → `ValueError`（全仓 grep 无破坏面），误导文案改
  「路由 X 在方法 Y 无对应实现，可用: …」；**W13 契约变更（口径根因修复）**：
  `bars()` 新增 `adjust` 参数透传 web 且默认口径由「web 强制 qfq」改为
  「原始价」，`start` 仅 tdx/local（显式 web → ValueError，auto → 剔除 +
  warning），价格口径不再随网络状态漂移。`response.py` df 列并集显式化 +
  from\_result 边界注释；三门面（binary/bridge/market）定位文档化 + 冒烟
  测试 19 条 + `binary.py` 死代码删除。新增回归 53 条，`tests/facade` 83 全绿。
  （复核例外：审计引导的 `market._option_symbol` 截尾声明为幻影引用，未虚构实现。）

- **F1 传输（T 域）**：`transport/base.py` C2 连接级租约（`TcpConnection.__init__`
  建 `RLock`，覆盖 connect/ping/request/异步镜像全生命周期；`Slot.busy` 死字段
  删除，杜绝假实现第三态）；`async_.py` C5 补 `asyncio.Lock` 请求串行化
  （**跨版本真缺陷**：3.10/3.11+ `wait_for` 超时不取消协程 → 双协程同 socket
  必串线；3.14 `Connection` 本身非线程安全，故跨版本都成立）+ C6 补 8 字节
  响应头 magic 校验（异步路径旧实现完全不校验）。`pool.py` T1 建连期异常分类
  （网络类异常 → `PoolConnectError` 供退避、编程错误不再被吞）+ T4 兑现注释
  承诺（`return_partial_on_failure`：保留已完成请求 + warning）+ tried\_hosts
  快照锁内拷贝 + 心跳 magic fail-fast + 埋点 `record_request`/`record_parse`/
  `record_reconnect`。`ratelimit.py` §2-9 三修（reset 锁 / `failures` 原子读 /
  时段档变更清令牌桶）+ 死 `TokenBucket` import 清理。`speedtest.py` 测量口径
  修复（旧实现测 recv 循环自身 CPU 非 RTT；`round*`→`round`；`ping_host` 默认
  端口 0 恒假 → 必失败）。`sniff.py` §2-13/24/25 三连（zip\_size==0 死循环 /
  解压失败 break / 16 个未知命令名 `msg_id:` 前缀）。新增回归 40+ 条，含
  16×650=10400 并发请求 0 串线压测（`-m slow` 门控）。

- **F2 协议（P 域）**：`protocol/registry.py` P1b/T2 dispatch **边界异常收口**
  （L1 解析器逃逸的原生异常统一包装 `ParseError` 保留 cause/context，fatal
  IntegrityViolation 仍直通；**实测修正：审计所引 7 类逃逸锚点已在早前批次
  消失，真实收口面为 9 payload×61 命令=610 组合实测 0 逃逸 + 永久测试固化**）

  - T3/P1b count 钳制（`codec count_guard` + `guarded_count`，声明数按剩余
    容量收敛 + 告警）+ L1 降级告警可观测（`DEGRADE_NOTICE`）。`std7709.py`
    CapitalChanges/FinanceInfo 尾记录 `len<size` break（旧实现裸 IndexError
    整批崩，测试前 1000×\[0xFF] payload 实测整批全 0 Bar）、P1d `build_realtime_quote_body`
    600000 doctest 笔误改正（golden 逐字节对拍锁定反转语义，⚠️ 家族复用面
    待真机）、P1a 指数 4 字节尾消费核实无误。`std7709_extra/goods/mac` 内层
    `except break` 改 fatal 感知 re-raise。`prober.py` §2 rate<=0 即抛 + probe\_range
    max\_count + 盘中改记 notes（可配 strict 抛）。`framing.py` iter\_frames 补
    max\_frame\_bytes 守卫 + ResponseFrame spec\_magic。`parsers/__init__.py`
    `__all__` 12→70 补全。P2 死代码三项：**`get_datetime_from_lc`** **删除从未使用
    的第二形参** **`minutes_from_midnight`（公共 API 契约变更，单参调用方零影响，
    双参调用转** **`TypeError`）**、`mac._count_records` 死模板删除、
    `MacHeartbeatParser` 恒真定性为设计（空载荷=可解析即活，恒真式清为显式
    `True` + docstring 依据注记）。新增回归累计 53 条。

- **F3 Web 源（W 域）**：`web/base.py` W3/W4 重试治理（`max_retries` 钳 0-5 +
  退避封顶 8s + 400/404 不重试 403/429/5xx 重试 + 传输/解析**双失败桶** +
  网络层异常纳入重试面）+ W5 令牌桶**进程级**（`shared_bucket` 注册表，修
  门面每调用新建源致令牌满血复活）+ C7 限流 `acquire` 超时抛 `WebRateLimited`
  （旧无超时死等）+ W10 `encoding` 类属性（gbk 默认，东财系 utf-8，修正新浪/
  腾讯 gbk 与东财混用）+ 结构性 `_EastmoneyJson` 单一实现（五处拷贝删除）。
  `fundflow.py` W1 po 排序方向 + W9 BSE 市场归属（f13=0 经 domain.symbol 判
  bj）；`adapters_ext.py` W6 分时 `len<4` 越界；`facade.py` W7 `longhu_rank`
  @staticmethod；`hot_rank.py` W8 空值防御；`corporate.py` W14 北交所 .BJ
  后缀 + report 参数化；`history.py` NaN/inf 过滤 + 周期/复权未知值**显式报错**
  （旧静默回退，含 hk/us 1min 误落日线数据缺陷）+ 翻页上限。`sources.py`
  「140 笔/页」→ 单一事实源指针。复核不成立 6 条（boards is\_member/normalize
  \_i/market\_stats zip/longhu super None/\_code\_to\_secid/wencai 守卫，均行号
  证据）。新增回归 62 条。

- **F4 服务面与可观测（V 域）**：`http_server.py` V1/V2 安全批——`SAFE_CLIENT_METHODS`
  24 方法白名单（`getattr(client,*)` 任意派发收口，破坏性 → 403 / 未知 → 400/404）+
  TaskStore max\_tasks=200 上限（活跃满 409 + 已完结按时间逐出 + 结果体

  > 1MB/>1000 行钳制）+ `_LazyClient` 类级存在性探测（`getattr(...,None)` 探测
  > 永真修复）+ POST body 4MB 中间件 + V6 错误面脱敏（原生异常 → E9001 无泄漏

  - logger.exception）+ 端点文档 42 实测清点。`ws_server.py` V4/V7——
    同步 handler 阻塞事件循环修复（`asyncio.to_thread`）+ path/max\_size 接线
    （现代/legacy websockets 双轨）+ 真推送 `quote_update`（每连接独立
    JsonRpcHandler 订阅集，30s 停推送改真实轮询推送）+ unsubscribe 可哈希校验 +
    asyncio.run 收敛；**SHA-1 项复核不成立**（握手由库内部完成，本包无 sha1 调用点）。
    `mcp_server.py` V6 三处 size/count 钳制 + 错误面脱敏 + stdio 异常日志；
    **route/force 透传与 \_write\_loop 死代码两项复核不成立**（grep 无此符号）。
    `metrics.py` V3 Histogram observe 累积语义修复（黄金序列 + histogram\_quantile
    手算对拍）+ 标签值转义 + 带锁快照贯穿导出器；`statsd` 增量快照/真解析标签/
    port=0 禁用/start\_pushing 真实线程；`observability start_exporter` 统一装配
    工厂。`feedback/` stats 环形缓冲死分支修复（`idx%max` 恒\<max 致 append 无界
    增长）+ telemetry 深拷贝 + 丢弃计数 + reporter 键名脱敏 + 混合类型排序防御。
    新增回归 66 条（含真回环 socket 收 quote\_update、1009 超限关闭）。

- **裁决（防重复误修）**：P 域上报的「0x0530 单票请求市场字节翻转真缺陷」经
  编排者独立实测**不成立**——真实 0x0530 走 `std7709.build_realtime_quote_body`
  （`quote_request_market`，SH→0/SZ→1），golden 逐字节对拍 600000/000651 全中；
  P 引用的 `build_security_quote_body` 全库不存在，`1-m` 实为 `_quote_body`
  服务 goods/ex/mac 三族（S 域已注明 ⚠️ 未经真机、与 quote\_request\_market
  同语义）。系并行读在途快照的误归属，0x0530 链无需改动。

- **存储/配置/CLI/工具域（G）**：`domain/symbol` 归一为单一事实源；`domain/calendar`
  交易日历惰性加载 + `RLock` 并发守护；`sinks` 分发表化 + 原子写统一 + `_TABLE_NAME_RE`
  预校验表名；`sources` 路由 `default_empty_ok` 语义收敛；`profile` 探测去重 +
  `_price_scale_sanity` 合理性守卫；`native` dict-only 签名 + `lot_factor` 语义对齐；
  `adjust` docstring 与防御性入参；`cli` 收敛至 19 子命令（新增 `probe`/`feedback`，
  退出码统一 TdxError→2 / KeyboardInterrupt→130）；`tools` 补 `_yaml_min.dump_yaml`
  （YAML 往返）、`golden_expand`、`check_originality --fix` 门控、`spec_audit`
  `coverage_summary(results)` 缓存化 + `_print_table`；`config` schema `_deep_merge`
  / `config_from_dict` 严格校验 + 移除未用枚举。新增 11 个测试文件 154 用例
  （含 10 条 YAML round-trip）。经核实反驳审计误报 4 项（`spec_audit._walk_python`
  等全库不存在的引用）。

### Documentation

- `docs/INDUSTRIAL_OPTIMIZATION_PLAN.md`：修复状态回填（逐项 ✅/⚠️/↩ 反驳）。

- `docs/api/README.md`、`docs/cookbook/06_custom_command.md`、`docs/adr/README.md`、
  `Makefile` 审计行：清除已删模块引用；Web 源规模口径修正为
  14 源模块 / 45 Source 类（按 grep 实数）。

- `tstdx/errors.py`：E6xxx 流式异常四类处置决议入注释（保留为预留公共错误
  分类；兑现背压/补数承诺的批次必须接线本分类）。

## \[1.1.0] - 2026-09-02

### Added

接口面吸收（对照 thsdk / levistock 公开能力面，洁净室实现，见 `docs/archive/ABSORPTION_ANALYSIS.md`）：

- **tstdx/facade/response.py**: 统一响应形态 `ApiResponse{success,error,data,extra,code}`

  - `__bool__` + `to_dict()` + 惰性 `.df`（pandas 可选）；`ok/err/from_result/wrap`
    工厂；`TdxError` 自动转失败响应（保留错误码与 context）。
    「成功但空」与请求失败严格区分。

- **UnifiedQuoteAPI.query(method, ...)**: 任意门面方法的统一响应化入口
  （永不抛异常边界）；HTTP 网关同步暴露 `POST /query`。

- **tstdx/web/wencai.py**: i问财自然语言选股（`WencaiSource`，注册名 `wencai`，
  capability `wencai`，1 req/s）。cookie 由调用方持有（参数或
  `TSTDX_WENCAI_COOKIE`），不依赖任何第三方 cookie 中继服务；
  返回 title+rows zip 后的 `list[dict]`。

- **search\_symbols(pattern, limit=, market=)**: 统一证券搜索（WebQuoteSession
  与 UnifiedQuoteAPI 双入口）：`suggest` 之上补齐 display 展示名与市场过滤。

- **corporate\_action(symbol)**: 权息资料语义别名（TDX 0x000F 股本变迁通道）。

- **index\_list()**: 常用指数离线目录（与 `index()` 行情互补）。

- **HTTP 网关新端点**: `GET /search`、`GET /index_list`、`GET /wencai`、
  `GET /corporate_action/{symbol}`、`POST /query`。

- **tests/**: `tests/facade/test_response.py`（21 用例）、
  `tests/web/test_wencai.py`（13 用例，全罐头）、
  `tests/web/test_search_symbols.py`（12 用例）；registry 一致性门禁扩展
  `wencai` capability ↔ facade 方法映射。

- **docs/archive/ABSORPTION\_ANALYSIS.md**: 上游接口吸收差距矩阵（能力对照 +
  洁净室红线 + 后续路线图）。

### Added（信任度批次：Golden origin 分级 + L1 真实样本门禁）

- **tstdx/tools/golden\_audit.py**: Golden 语料 origin 分级审计工具——
  样本按 meta.source 三级归一（real=self-captured / synthetic / unknown），
  输出分命令 real/syn 统计与维度覆盖（K 线类别、市场、代码数）。
  报告与门禁输出 ASCII 安全（Windows GBK 控制台兼容）。

- **L1 真实样本门禁**: 账本中 `tier=L1 且 verified=True` 的命令必须有
  ≥1 个 real 样本，`--gate` 缺失即退出码 1（CI 阻断）——
  「已宣布精确解析却没有真实主站样本」视同回归。
  追加开关：`--require-markets`（市场敏感命令须双市场 real 覆盖）、
  `--require-kline-categories`（K 线类别集合）。缺样本时给出
  `python -m tstdx.tools.capture --plan <kline|core|quotes>` 补录指引。

- **CI**: 新增 `golden-gate` job（`python -m tstdx.tools.golden_audit --gate`）；
  Makefile 新增 `audit-golden` 目标。

- **当前基线**: 500 样本（real 30 / synthetic 470 / unknown 0），
  L1 verified 命令 0x44E / 0x052D / 0x0530 / 0x000F 全部具备 real 样本，
  0x052D real 类别覆盖 0-11 全集；0x44E / 0x052D 的 parse\_ctx 市场维度
  缺失被软性标记（INFO，不阻断）。

- **tests/unit/test\_golden\_audit.py**: 24 用例（分级归一 / 聚合 / 缺口 /
  市场与类别门禁 / CLI 两路），门禁用真实账本 + 动态构造语料自适应验证。

### Added（接口扩展批次：个股所属板块 / IPO 日历 / 大单流向）

- **`stock_boards(symbol)`**: 个股所属板块（东财 push2 `slist` `spt=3` 接口，
  2026-09 实测验证）——行业/概念/地域全量 BK 板块（实测一只家电龙头 38 个），
  按板块涨跌幅降序；与 `em_board_members`（板块→成分）方向互补，
  返回 `code` 可回查成分形成板块网络遍历。
  `EastmoneyBoardSource.fetch_stock_boards`（复用 push2 主机池容灾）。

- **`ipo_calendar(apply_date=, page=, size=)`**: IPO 申购日历（东财
  datacenter-web `RPTA_APP_IPOAPPLY`，2026-09 实测验证）——申购/中签/缴款/
  上市日期、发行价与预测价、发行量、网上申购上限与顶格市值、行业 PE。
  未定价/未上市字段为 `None`（保留 null 语义，不填充 0）；
  `apply_date` 非空即「今日申购」视图。`EastmoneyIpoSource`
  （继承 datacenter 报表基类，`VALID_REPORTS["ipo"]` 注册）。

- **`big_order_flow(symbol)`**: 个股大单流向语义别名（单只标的的五档
  资金分档明细：主力/超大单/大单/中单/小单净流入与占比），
  与批量 `fund_flow` 同通道（东财 ulist）。

- **HTTP 网关新端点**: `GET /stock_boards/{symbol}`、`GET /ipo`、
  `GET /big_order_flow/{symbol}`。

- **registry 扩展**: `eastmoney` capability += `stock_boards`，
  `corporate` capability += `ipo`；一致性门禁同步
  （`fund_flow` 回归守卫 += `big_order_flow`）。

- **capture 元数据富化**: `_bars` ctx 补记 `market`/`code`，
  core 计划 0x000F/0x0010/0x044E/0x0537/0x0FC5 补记 `market`/`code`——
  后续补录的 Golden 样本可直接喂给 golden\_audit 的市场维度覆盖
  （消除 0x44E/0x052D 的 market\_gaps INFO 缺口）。

- **盘中异动**: push2ex `getStockChanges` 端点参数组合经 7 组实测均返回
  rc=102，无法离线确证——**本批不实现**（路线图标注「端点待重抓包验证」，
  不上线未经验证的接口）。

- **tests/web/test\_stock\_boards.py**: 17 用例（slist 罐头解析 / secid /
  null 语义 / 过滤器 URL / facade 双入口 / live 结构冒烟）。

### Added（补全批次：盘中异动 / 股吧人气榜 + Golden 实采补录）

- **`stock_changes(types=(), page=, size=)`**: 盘中异动池（东财 push2ex
  **`getAllStockChanges`**）。端点事实经前端公开 JS 提取 + 实测验证
  （此前 7 组探测失败系方法名与 `dpt` 参数错误：真实为
  `dpt=wzchanges` + `getAllStockChanges`，非 `getStockChanges`）。
  16 类异动枚举（火箭发射/大笔买入/60日新高等）随源暴露
  （`EastmoneyStockChangesSource.CHANGE_TYPES`）；`i` 指标串拆为
  `metrics: list[float]`（含义随类型不同，不强行归一）；
  非交易时段空列表为合法状态。注册名 `stock_changes`。

- **`hot_rank(page=, size=)`**: 股吧个股人气榜（东财 emappdata
  `stockrank/getAllCurrentList`，POST JSON，实测验证）。返回
  rank/symbol/code/market/rank\_change/his\_rank\_change；榜单仅含排名
  与代码，行情回查 `quotes`。`globalId` 本库生成（uuid4）。
  同花顺人气榜旧端点（dq.10jqka fuyao hot\_list）实测 404 已下线，
  不采用。注册名 `hot_rank`。

- **HTTP 网关新端点**: `GET /stock_changes`、`GET /hot_rank`。

- **registry 扩展**: `stock_changes` / `hot_rank` 源 + capability +
  normalizer + adapter 四方注册，一致性门禁同步。

- **Golden 实采补录**: 交易时段外运行 `capture --plan core/kline`（2026-09-02
  午休窗口，20/20 成功），语料 **real 30 → 50**：0x44E 双市场（mkt=\[0,1]）、
  0x052D 28 条实采（类别 0-11 全集 + 双市场）、0x000F/0x0010/0x0537/0x0FC5
  各 +1；`golden_audit --gate --require-markets` 最严格组合通过，
  market\_gaps 元数据缺口完全消除。回放类数值断言测试改为锚定设计罐头
  （`_pinned_sample`），不再随补录漂移。

- **tests/web/test\_hot\_rank.py**: 24 用例（异动罐头解析/枚举完整性/
  类型过滤与分页/POST 体形态/size 钳制/人气榜解析排序/live 冒烟）。

### 1.1.0 升级批次（docs/archive/OPTIMIZATION\_PLAN.md，合并两轮梳理的优化点）

- **批次 A（TDX 账本校准与门禁增强）**:

  - `facade/api.py` 注释命令号修正：`block_quotes` 0x02CF→**0x07E5**、
    `volume_price` 0x02EE→**0x051A**（与 client 实际请求一致）。

  - `commands.py`：`Command` 新增 **`status`** 字段（online/offline/degraded）

    - `by_status()` 助手；0x054C/0x053E/0x0450 实测下线入账；
      0x0010/0x0537/0x0FC5 升 `verified=True`（golden 实采支撑），
      0x0FB4/0x06B9 保持未验证（无实采样本不虚标）。

  - `golden_audit.py` 新增 **payload 有效性下限**（`MIN_PAYLOAD_BYTES`
    按命令单记录最小布局）与 `suspect_short` 分类；`--require-payloads`
    纳入硬门禁——「real 样本 ≠ 有效样本」盲区修复（0x537 空分时样本
    4B 已被标记警示）。

- **批次 B（发布卫生）**: `pyproject.toml` ruff 配置段迁移至
  `[tool.ruff.lint]`（废弃写法清理）；**lint 债务 417 项清零**
  （F401×133 / UP×112 / I001×53 / SIM105×28 / F841 / B905 / E741 /
  F821×9 真实引用缺失修复——含 `client._PREFIX_MARKET` 未定义的
  潜在 NameError、`requests.py` `__all__` 15 个幻影导出）；
  `ruff format` 全仓 137 文件归一。

- **批次 C（服务面 parity）**: CLI 新增 `changes` / `hot` 子命令；
  MCP 10→**12 工具**（`get_stock_changes` / `get_hot_rank`）；
  WS 新增 `stock_changes` JSON-RPC 方法；`docs/api` 全量更新
  （统一门面/异步门面/Web 源矩阵/集成服务面）。

- **批次 D（韧性与体验）**: `tstdx/tools/_console.py` 统一控制台
  UTF-8 出口（capture/spec\_audit/golden\_expand/check\_originality/
  golden\_audit 全部接入，GBK 控制台乱码修复）；
  **`AsyncUnifiedQuoteAPI`**（`facade/async_api.py`）：核心 10 方法
  `asyncio.to_thread` 桥接 + `arun()` 泛化通道 + `aquery()` 永不抛
  异常边界；版本 1.0.0 → **1.1.0**。

- **tests**: `tests/unit/test_commands.py`（账本校准 12 用例）、
  `tests/unit/test_upgrade_1_1_0.py`（C/D 批次 13 用例）；
  MCP 工具数断言同步 12。

### 1.1.0 批次 E 落地（docs/archive/OPTIMIZATION\_PLAN.md 长期队列可即启项）

- **E1 并发批采**: `TdxClient.quotes_concurrent`（线程池 + 连接池并行，
  保序 + 单只失败容错记 `last_errors`）、`AsyncTdxClient` 异步镜像、
  facade `quotes_concurrent`。

- **E3 符号库导出**: `export_security_list`（0x044D 分页遍历，短页停 +
  max\_pages 防御）、async 镜像、facade `security_list_all`。

- **E8 深市补录**: capture `core` 计划补 market=0 变体（0x000F/0x0010/
  0x0537/0x0FC5），午休窗口实采 10/10，**语料 real 50→60，
  四命令 mkt=\[0,1] 全覆盖**。

- **E5/E7 实测闭环**: capture 新增 `pool` 计划；0x07E5/0x051A/0x056A
  三主站实测无响应 → 账本 **offline**（client 方法保留待参数校正）；
  0x0FEB 断连（不识别 → offline）；0x051B 存活但空布局（degraded）；
  0x0537 沪市空布局根因独立（深市 619B 有效）→ degraded。

- **E10 bench 基线**: `benches/bench_web_parsers.py`（异动池 33.7 万
  rec/s / 人气榜 98.4 万 rec/s，5k 罐头 p50/p95）。

- **注释漂移三连修**: facade `security_list` 0x0514→0x044D、`finance`
  0x0223→0x0010、`capital_changes` 0x0A03→0x000F，加 docstring 防回归。

- **tests**: `tests/unit/test_batch_e.py`（13 用例）；
  账本 status 断言更新（offline 7 / degraded 2）。

## \[1.0.0] - 2026-08-31

GAP\_ANALYSIS\_v0 全部四档（A/B/C/D）实现完成的首个稳定版。

### Added

- **PROTOCOL\_SPEC/**: YAML spec system with 8 core command specs (handshake, heartbeat, kline, realtime\_quote, minute\_today, trade\_today, trade\_today\_alt, security\_count) + UNKNOWN/ 自动草案目录

- **tstdx/integration/**: HTTP 服务（FastAPI，6 组 32 端点 + 后台任务）、WebSocket JSON-RPC 2.0（bars/quotes/订阅/退避错误码）、MCP stdio 服务（10 工具）

- **tstdx/profile/**: 六步数据规格探测（证据链 + 置信度 + ProfileUndetectable）与 9 市场预设（SH/SZ/BJ A股、基金、债券、黄金、期货）

- **tstdx/protocol/prober.py**: 未知命令主动探测（1 req/s 限速、非交易时段门禁、spec DRAFT 归档）

- **tstdx/transport/sniff.py**: 被动嗅探（环形样本缓冲、未知命令发现、DRAFT YAML 导出）

- **tstdx/\_async\_bridge.py**: run\_sync 主线程/子线程/活动 loop 三态语义 + AsyncBridge 守护线程

- **tstdx/streaming/push.py**: 0x0547 PushChannel 原始字节推送

- **tstdx/tools/capture.py**: 合规强化（交易时段阻断、zlib/zstd 归档、hex dump、structure.md、schema v2 元数据）

- **tstdx/tools/golden\_expand.py**: Golden 语料合成扩充至 500 案例（幂等、SHA256 去重）

- **tstdx/tools/check\_originality.py**: AST 原创性门禁（许可头/拷贝指纹/外部导入审计）+ pre-commit 接入

- **tests/**: 11 类目测试矩阵（config/errors/web/observability/i18n/sinks/sources/streaming/protocol/client/compatibility）、24 项贯通 bridges 测试、流韧性测试、弃用策略测试、HTTP/WS/MCP 服务测试

- **benches/**: bench\_reader.py（记录数/秒、p50/p95）+ bench\_parser.py（黄金 payload 分派吞吐）

- **ORIGINALITY/AUDIT\_REPORT.md**: 合规审计报告（84/84 文件通过，0 疑似抄袭）

- **.github/workflows/wheels.yml**: 3 OS × 4 Python 发布矩阵（tag 触发 PyPI 发布）

- **docs/**: quickstart/FAQ/troubleshooting/migration×3/cookbook×6/ADR×10/API ref

### Changed

- **tstdx/web/adapters.py**: 归一化逻辑集中到 web/normalize.py（7 个 normalizer 注册制）

- **版本策略**: SemVer 正式版；弃用策略 = 2 个 minor 窗口（DeprecationPolicy.removal\_gap）

## \[0.4.0] - 2026-08-31

### Added

- **PROTOCOL\_SPEC/**: YAML spec system with 8 core command specs (handshake, heartbeat, kline, realtime\_quote, minute\_today, trade\_today, trade\_today\_alt, security\_count)

- **tstdx/web/normalize.py**: Centralized volume/amount normalization across 7 HTTP Web sources

- **tstdx/i18n/encoding.py**: Charset auto-detection (UTF-8/GBK/GB18030/Big5)

- **tstdx/tools/codegen.py**: YAML spec → parser codegen

- **tstdx/tools/spec\_audit.py**: Spec↔implementation coverage validation

- **tstdx/feedback/**: Feedback reporter, telemetry collector, user stats (opt-in, 7-step sanitization)

- **tstdx/security/credentials.py**: 3-tier credential storage (keyring → env → encrypted file)

- **tstdx/compat/easy\_tdx.py**: easy\_tdx compatibility shim

- **tstdx/compat/eltdx.py**: eltdx compatibility shim

- **tstdx/tools/check\_originality.py**: AST-based originality checker

- **ORIGINALITY/LICENSE\_ALLOWLIST.md**: License allowlist for compliance scanning

- **GOVERNANCE.md**: Project governance document

- **CODE\_OF\_CONDUCT.md**: Community code of conduct

- **SECURITY.md**: Security policy

- **CONTRIBUTING.md**: Contribution guidelines

- **.github/**: CI workflows (lint, type-check, test, originality, spec-coverage, bridges)

- **.github/ISSUE\_TEMPLATE/**: Bug report, feature request, docs, security advisory templates

- **.github/PULL\_REQUEST\_TEMPLATE.md**: PR template

- **Dockerfile**: Docker image for Python 3.11-slim

- **docker-compose.yml**: Docker Compose configuration

- **Makefile**: Development commands (install, test, lint, audit-bridges, etc.)

- **git-cliff.toml**: Changelog generation configuration

- **docs/adr/**: Architecture Decision Records (5 ADRs)

- **README.md**: Project documentation

### Changed

- **tstdx/web/adapters.py**: Updated to use centralized normalize module

### Fixed

- Various bug fixes and improvements

## \[0.1.0] - 2026-08-31

### Initial Release

- Core protocol parsing (60 parsers, 85 command ledger)

- Sync/Async dual API (TdxClient + AsyncTdxClient)

- Multi-protocol-family clients (Goods, ExMarket, MAC, F10)

- HTTP Web 7 Adapter + easyquotation shim

- 5-source fallback routing

- Streaming engine with reconnect/gap-fill/backpressure

- 3 data sinks (DataFrame, Parquet, DuckDB)

- 40+ error classes with RetryAdvice

- Config schema with 6-source merge

- Local vipdoc reader

- Zero-dep observability (Prometheus metrics)

- CLI with 12 subcommands

