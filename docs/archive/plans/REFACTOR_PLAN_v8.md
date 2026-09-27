# atst 重构方案 v8（2026-09）

> 基于对全仓 43,836 行 Python 代码（atst/ 29 个子包 + 100+ 模块）的静态梳理与依赖分析制定。
> 原则：**行为不变、公开 API 不变、测试先行**。每一项完成后立即跑全量 pytest + 可达性门禁，绿灯才进入下一项。

## 一、项目功能与架构现状梳理

### 1.1 主要功能（自顶向下）

| 层 | 内容 | 关键模块 |
|---|---|---|
| 协议层 | 5 套 TDX 协议族（7709 标准 / 7727 扩展市场 / MAC / F10 / 商品语义），85 命令账本，61 个 L1 精确解析器，三级分派（L1 精确 → L2 启发 → L3 透传） | `protocol/`、`codec/` |
| 传输层 | 同步 TCP（RLock 租约）、异步 TCP、4 槽连接池、限流、测速 bestip、主机嗅探 | `transport/` |
| 客户端层 | `TdxClient` / `AsyncTdxClient`（签名镜像）+ Goods/ExMarket/Mac/F10 四族子客户端 | `client.py`（1715 行） |
| 门面层 | `UnifiedQuoteAPI`（~60 方法，auto/tdx/web/local 四路由 + 熔断 + adjust 口径守卫）、统一响应 `ApiResponse{success,error,data,extra}` + 惰性 `.df` | `facade/`（2453 行） |
| Web 行情层 | 50 个 Source 类 17 模块（东财/新浪/腾讯/集思录/百度/港股…），模板方法基类 `BaseWebSource` + `_EastmoneyJson`；`WebQuoteSession` 63 方法统一入口 | `web/`（10,637 行，占全库 24%） |
| 降级路由 | tdx → web → reader(本地 vipdoc) → cache(golden 回放) → synthetic 五级 | `sources/` |
| 域模型 | symbol 单一事实源、复权、交易日历、财务模型 | `domain/` |
| 流式与存储 | QuoteStream/AsyncQuoteStream（轮询+diff）；DataFrame/Parquet/CSV/DuckDB Sink（`sinks/`）与 vipdoc `.day` 写回（`sink/`，**两个包职责不同，非重复**） | `streaming/`、`sinks/`、`sink/` |
| 服务面 | HTTP REST 网关（42 端点白名单）、WebSocket JSON-RPC、MCP stdio 12 工具 | `integration/` |
| 横切面 | 零依赖指标注册表 + Prometheus/StatsD/OTLP 导出、40+ 异常类错误树 + RetryAdvice、6 源配置合并、凭据三级存储、字符集探测、CLI 19 子命令 | `observability/`、`errors.py`、`config/`、`security/`、`charset/`、`cli.py` |

### 1.2 关键实现细节

- **三级解析分派**：`protocol/registry` 按命令号注册 L1 解析器，异常统一收口为 `ParseError`；对抗矩阵（9 payload × 85 命令）保证逃逸为 0。
- **同步/异步镜像**：`AsyncTdxClient` 与 `TdxClient` 逐方法镜像，由 `tests/client/test_sync_async_parity.py` 奇偶门禁保证签名一致；共享纯函数层在 `client_core.py`。
- **模板方法模式**（web 层核心抽象）：`BaseWebSource` 子类只需实现 `source_name/build_url/parse`，基类统一限流/反爬头/重试退避/失败双桶计数；`_EastmoneyJson` 上收主机池 failover；`EastmoneyDataCenterSource` 承载 datacenter-web 报表分页通用逻辑，7 个子类仅覆盖报表名与字段映射。
- **永不抛异常边界**：门面层所有方法返回 `ApiResponse`，异常在边界内捕获并转 `error/code`。
- **零硬依赖**：所有第三方（pandas/httpx/fastapi/pydantic…）均为 optional extra，惰性导入。

## 二、问题清单（按风险与收益排序）

| # | 问题 | 证据 | 风险 |
|---|---|---|---|
| P1 | `atst/i18n/` 是已废弃 shim（原 charset 更名遗留），带 DeprecationWarning，生产代码零引用，仅 3 个测试仍走旧路径 | `atst/i18n/__init__.py:4-52`；`tests/i18n/test_encoding.py:11`、`tests/test_bridges.py:154`、`tests/unit/test_i18n_priority.py:13`；`scripts/_reach_allow.txt:10-11` | 低 |
| P2 | `facade/api.py` 内 17 处「新建 WebQuoteSession→调用→close」复制粘贴模板 | baidu_kline 865 / baidu_minute 875 / baidu_ticks 885 / baidu_quote 895 / fund_nav_history 913 … 约 865–1260 行 | 低 |
| P3 | `cli.py` 1210 行单文件 ~40 个 `_cmd_*` 函数 + 210 行 argparse 长函数 | `atst/cli.py` | 低 |
| P4 | `web/facade.py` 1336 行单类 63 方法，全部 Web 域能力堆在一个 `WebQuoteSession` | `atst/web/facade.py:124` 起；v7 计划 B6 已预留 | 低-中 |
| P5 | `client.py` 1715 行 9 个类：sync/async 镜像 + 8 个子客户端 + 工厂 | `atst/client.py` | 中（`tests/facade/test_compat_layer.py` monkeypatch `atst.client` 内部符号） |
| P6 | README 代码地图缺 `sink/`（vipdoc 写回）与 `charset/` 条目，易被误判为与 `sinks/`/`i18n` 重复 | README「核心代码文件地图」 | 低 |
| 明确不做 | `sink/` 与 `sinks/` **不合并**（职责不同：输出 Sink vs vipdoc .day 写回，两包 docstring 已交叉声明）；web/ Source 层模板抽象已达标，仅留两个小优化（KlineSource 分页器复用、corporate 两处 BaseWebSource 归入 _EastmoneyJson），本期不动 | `atst/sink/__init__.py:4-7`、`atst/sinks/__init__.py` | — |

## 三、改造步骤

### P1 删除 `atst/i18n/` 废弃 shim（预计 -54 行）
1. 三个测试文件改 `from atst.charset import …`（或 `atst.charset.encoding`）。
2. 删除 `atst/i18n/` 目录；`scripts/_reach_allow.txt` 删除 i18n 两行。
3. 更新 `docs/api/README.md`、`docs/cookbook/03_offline_vipdoc.md` 中的 `atst.i18n` 引用。
4. 验证：全量 pytest + `python scripts/audit_reachability.py --strict`。

### P2 `facade/api.py` 提取 `_with_web_session`（消除 17 段重复，预计 -150 行）
1. 新增私有帮助方法：`_with_web_session(fn: Callable[[WebQuoteSession], T]) -> T`，统一负责 `WebQuoteSession()` 构造、调用、`close()`（含异常路径）。
2. 17 处薄委托改为 `return self._with_web_session(lambda s: s.xxx(...))`，行为逐处等价。
3. 同步检查 `facade/async_api.py` 是否存在同型重复，如有则用同一思路收口。
4. 验证：`pytest tests/facade tests/web -q` 后全量。

### P3 `cli.py` 拆分为 `cli/` 包（行为不变，公开入口仅 `build_parser`/`main`）
1. 布局：`cli/__init__.py`（re-export build_parser/main 兼容旧 import）、`cli/_table.py`（输出工具）、`cli/cmds_market.py`（bars/quotes/count/info…）、`cli/cmds_web.py`（baidu/fund/index/changes/hot…）、`cli/cmds_hosts.py`（hosts_scan/serve/probe/server-test…）、`cli/parser.py`（build_parser，改 (name, add_args, handler) 注册表驱动）。
2. 顶层 `atst/cli.py` 若被 `import atst.cli` 引用，保留一个 shim 模块 re-export（grep 确认引用面后再定）。
3. 验证：`pytest tests/test_cli_hosts.py tests/unit/test_cli_semantics.py -q` + 手动 `atst --help`。

### P4 `web/facade.py` 拆域 Mixin（v7 B6 兑现，公开面 `web_session()/WebQuoteSession` 不变）
1. 按域拆为 Mixin（方法纯搬移，无逻辑改动）：
   - `QuoteSessionMixin`：quotes/hk/us/global/market_stat
   - `KlineSessionMixin`：klines/minute/history/ticks
   - `BoardSessionMixin`：boards/rank/hot
   - `FundFlowSessionMixin`：fund_flow/limit_pool/breadth
   - `CorporateSessionMixin`：profile/notices/shareholders/unlocks/performance/forecast/ipo/longhu/news
   - `BaiduSessionMixin`：baidu 系列 + fund/index/suggest/wencai
2. `WebQuoteSession(QuoteSessionMixin, …)` 组合保持方法全集不变；文件落位 `web/facade_mixins/` 子包或同级 mixin 模块（以 <600 行/文件 为准）。
3. 验证：`pytest tests/web -q`（重点 `test_web_facade.py`、`test_registry_consistency.py`）+ 全量。

### P5 `client.py` 拆 sync/async 文件（纯文件搬移，最后做）
1. 布局：`client/__init__.py`（re-export 全部 9 类 + `get_client`，保持 `from atst.client import TdxClient` 等所有现有 import 路径不变）、`client/sync.py`（TdxClient + 4 子类）、`client/async_.py`（Async* 4 类）、`client/factory.py`（get_client）。
2. **先 grep `tests/facade/test_compat_layer.py` 的 monkeypatch 目标**，同步把 patch 路径指到新模块；其余 `import atst.client as …` 引用逐一核对。
3. sync/async 抽象合并（mixin 化去镜像）**本期不做**——重载密集，风险中高，留待 v9。
4. 验证：全量 pytest + `test_sync_async_parity.py` 奇偶门禁。

### P6 文档同步
- README 代码地图补 `sink/`（vipdoc .day 写回）与 `charset/`（i18n 更名后现役模块）两行。
- CHANGELOG.md 记录 v8 重构条目（行为不变说明）。

## 四、回归门禁（每步后执行）

```bash
python -m pytest tests/ -q                      # 全量
python scripts/audit_reachability.py --strict   # 可达性（孤儿=0）
python -m pytest tests/adversarial -q           # 对抗矩阵（逃逸=0）
```

## 五、预期收益

- 删除废弃 shim，消除「charset/i18n 双包」误解面。
- `facade/api.py` 约 -150 行重复；`cli.py`、`web/facade.py`、`client.py` 单文件全部降到 <700 行，职责按域内聚。
- 全程公开 API 与行为零变化，测试全绿为准出条件。
