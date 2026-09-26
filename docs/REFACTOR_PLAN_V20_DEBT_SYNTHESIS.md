# tstdx V20 技术债清偿与收敛方案

> **定位**：本文是 V19（架构复核 + 判据对账）之后的**技术债专项清偿方案**。V18/V19 已经解决了
> "文档承诺 ↔ 代码兑现"的对账、"判据手抄副本"的重复、"配置面 ↔ 执行面"的对账——这些是**横向**
> 一致性问题。本文聚焦**纵向**的"凝固技术债"：hardening monkey-patch、executor god class、
> catalog 元数据驱动不彻底、以及 web/ 目录结构过散。
>
> **不做什么**：本文**不**重复 V18/V19 已完成的判据对账、不新增功能、不改动对外契约（capability
> 名称、Client 便捷方法签名、三面路由形状全部保持不变）。这是一次**内部收敛**，目标是让
> 执行链的每一层都有清晰的单一职责，减少隐藏复杂度。

> **执行状态（已收口）**：Phase 1 / Phase 2 / Phase 3 均已落地并通过三轮逻辑审查；实际读数、
> 与原计划的偏差、以及未达成的验收项集中在 **§8 执行台账**。阅读本文时，§2 描述的是**方案
> 提出时的现状**（保留为证据），§3 的路径名已按实际落地文件名（`<provider>/adapters.py`）
> 校正，§5 的验收标准逐条附了实测值。

## 0. 基线读数（2026-09-26 现场）

| 项 | 读数 | 口径 |
|---|---|---|
| tstdx/ 源文件 | 190 | `Get-ChildItem -Recurse -Filter *.py tstdx\` |
| tstdx/ 行数 | 51,416 | 排除 `__pycache__` |
| tests/ 源文件 | 282 | 同上 |
| DIRECT_BINDINGS | 251 | `len(tstdx.runtime.executor.DIRECT_BINDINGS)` |
| DEDICATED_CAPABILITIES | 7 | 有专用执行体的 capability 名（quotes/bars/snapshot/minute/trades/security_count/security_list） |
| MIGRATED_CAPABILITIES | 167 | `len(MIGRATED_CAPABILITIES)` |
| MIGRATED_BINDINGS | 229 | `len(MIGRATED_BINDINGS)` |
| Provider | 11 | PROVIDERS.ids() |
| 协议命令账本 | 85 | COMMANDS 行数 |
| 精确解析器 | 61 | `protocol/parsers/` 模块数 |
| Hardening 文件 | 8 | `*hardening*.py` |
| 离线全量测试 | **4008 passed / 1 failed** | 唯一 FAIL 是 G43 evidence pointer 元数据守卫 |
| 覆盖率 | 82.93% | 阈值 77 未下调 |

**执行链确认贯通**：四张服务面 → Client → UnifiedRuntime → DirectProviderExecutor → Provider，
零绕路。V18 已验证 v12 门面、v14 信封运行时、5 级降级路由全部物理删除，单一执行链门禁
（`tests/architecture/test_single_kernel_guards.py`、`test_namespace_layout.py`、
`test_doc_code_consistency.py`）全绿。

---

## 1. 项目全景（功能 × 架构 × 实现）

### 1.1 主要功能矩阵

| 功能域 | 入口 | 实现位置 | 状态 |
|---|---|---|---|
| TDX A股行情 K线 | `Client.bars` / `TdxClient.bars` | protocol/ + transport/ + client/core | ✅ 贯通（0x052d） |
| TDX 实时行情 | `Client.quotes` / `TdxClient.quotes` | protocol/ + client/core | ✅ 贯通（0x0530） |
| TDX 证券列表 | `Client.security_count` / `Client.security_list` | client/sync | ⚠️ 0x044d 已 offline 桩 |
| TDX 逐笔成交 | `Client.trades` | client/sync | ⚠️ 0x0fc5 request/parser inferred |
| TDX 分时 | `Client.minute` | client/sync | ⚠️ 0x0537 同上 |
| TDX 除权除息 | `client.capital_changes` | protocol/parsers | ⚠️ 0x000f 字段错位未锁定 |
| TDX 财务信息 | `client.finance_info` | protocol/parsers | ⚠️ 0x0010 同上 |
| Web HTTP 多源行情 | `Client.quotes(provider=tencent|sina|eastmoney|baidu)` | web/tencent/adapters.py 等（按 Provider 归组） | ✅ 贯通 |
| Web K线历史 | `Client.bars(provider=eastmoney|tencent|sina)` | web/tencent/adapters.py + web/sina/adapters.py + web/eastmoney/adapters.py | ✅ 贯通 |
| Web 市场数据 | `Client.call("market_stat"|"board_rank"|"fund_flow"|...)` | web/session.py mixin | ✅ 贯通（130+ capability） |
| 本地 vipdoc | `DayBarReader` / `MinBarReader` | reader/ | ✅ 贯通 |
| 流式订阅 | `Client.stream` → `StatefulQuoteStream` | streaming/ + stream_contract | ✅ 贯通 |
| 复权合成 | `Client.call("adjusted_bars", ...)` | executor._composed_call + domain/adjust | ✅ 贯通 |
| 回写本地 | `Client.call("sync_daily", ...)` | sink/local_day + executor._composed_call | ✅ 贯通 |
| CLI 31 子命令 | `tstdx capabilities` | cli/ | ✅ 贯通 |
| HTTP 10 端点 | `/v13/*` | integration/runtime_http | ✅ 贯通 |
| WS JSON-RPC | 10 方法 | integration/runtime_ws | ✅ 贯通 |
| MCP 9 工具 | stdio JSON-RPC | integration/mcp | ✅ 贯通 |

**主体链路判定：贯通**。所有已声明的 capability 都有可达执行路径；唯一的"声明在但拿不到"是
TDX 的 minute/trades（request/parser inferred）和 security_list（0x044d offline）——
这两条已在 client/api.py docstring 里明确告知用户，属于"诚实的未完成"而非"静默坏掉"。

### 1.2 架构分层

```
服务面（CLI/HTTP/WS/MCP）—— 只翻译，委托 Client
    ↓
Client / AsyncClient（client/api.py）—— 15 便捷方法 + call + execute + typed + execute_with_policy
    ↓
UnifiedRuntime（runtime/kernel.py）—— 唯一内核，QueryPlanner.compile → QueryPlan
    ↓
DirectProviderExecutor（runtime/executor.py）—— DIRECT_BINDINGS 精确绑定执行
    ↓
┌─ dedicated (7): _tdx_bars / _tdx_quotes / _tdx_snapshot / _tdx_minute / _tdx_trades / _tdx_security_count / _tdx_security_list
├─ migrated (229 → 167 个 capability):
│   ├─ backend="web_session" → WebQuoteSession().<method>(*)
│   ├─ backend="tdx_client" → TdxClient().<method>(*)
│   ├─ backend="f10_client" → F10Client()
│   ├─ backend="ex_client/goods_client/mac_client" → ExMarketClient() 等
│   ├─ backend="direct_adapter" → resolve_channel_adapter(provider, channel)().<method>(*)
│   ├─ backend="web_adapter" → MinuteKlineSource/EastmoneyHistoryKlineSource 等（按 provider 特化）
│   └─ backend="composed" → AdjustEngine/LocalDaySink 等（adjusted_bars/sync_daily）
    ↓
providers/ —— 11 Provider 注册表
    ├─ tdx → protocol/ + client/core + transport/pool + client/*
    ├─ tencent/sina/eastmoney/baidu/jsl/boc/iwencai → web/
    ├─ local_vipdoc → reader/
    ├─ derived → 无 adapter，走 migrated_backend="web_session" 特化
    └─ builtin → 同上
    ↓
QueryResult + meta.provenance + meta.warnings
    ↓
跨源（仅显式 FallbackPolicy）→ runtime/orchestration.py
流式 → streaming/ + stream_contract.py
```

**不变量**（由 40+ tests/architecture/ + tests/provider_isolation/ 锁定）：
- 零缓存：每次请求直达绑定 Provider，无结果/负缓存或请求合并
- provider-first：一个 Plan 永不私选第二 Provider；provenance 校验失败即抛
- 单内核：执行只发生在 UnifiedRuntime → DirectProviderExecutor
- 配置面即执行面契约：`Config` 每个键都有消费者

### 1.3 实现层结构问题

```
                好的
            ┌───────┴───────┐
            │               │
    ┌───────┴───────┐  ┌────┴─────┐
    │ 协议层         │  │ Provider  │
    │ codec/         │  │ 注册表   │
    │ protocol/      │  │ 不可变    │
    │ transport/     │  │          │
    │ charset/       │  │          │
    └───┬───────────┘  └──────────┘
        │
    ⚠️ hardening 外挂（8 文件）
        │
    ┌───┴───────────┐   ⚠️ executor god class（719 行）
    │ transport/     │      ├─ 7 dedicated 专用方法
    │ client/        │      └─ migrated if-elif 分派
    │  TdxClient     │           ├─ backend 选择
    │  ConnectionPool│           ├─ provider 特化
    │  TcpConnection │           └─ parameter 适配
    └───────────────┘
        │
    ⚠️ catalog 元数据驱动不彻底
        │  167 capability 只声明了 backend + method
        │  executor 里仍有 if-elif 来决定：
        │  - web_adapter 分支里按 provider 选 EastmoneyHistoryKlineSource 还是 MinuteKlineSource
        │  - composed 分支里写死 adjusted_bars 和 sync_daily 的实现
        │
    ┌───┴───────────┐
    │ web/           │   ⚠️ 40+ 文件过散
    │  adaters.py    │      adapters.py（884 行，TencentSource/SinaSource/EastmoneySource/BocSource/JslSource）
    │  adapters_ext  │      adapters_ext.py（MinuteKlineSource/MinuteSource/SuggestSource）
    │  adapters_baidu│      adapters_baidu.py
    │  adapters_margin│     adapters_margin.py
    │  adapters_index│      adapters_index.py
    │  adapters_fund │      adapters_fund.py
    │  adapters_deriv│      adapters_ext.py 别名？
    │  adapters_opts │      web/efinance_options.py（独立文件）
    │  history.py    │      web/history.py（SinaHistoryKlineSource/EastmoneyHistoryKlineSource）
    │  session.py    │      web/session.py + _session_p1.py + _session_p2.py + _session_p3.py + ...
    │  news.py       │
    │  corporate.py  │
    │  fundflow.py   │
    │  ...（31 Source）│
    └───────────────┘
```

---

## 2. 问题清单（按严重度 × 改动范围排序）

### P0-A：Hardening monkey-patch 凝固技术债（scope: transport/ + client/）

**事实**：8 个 `*hardening*.py` 文件通过 `transport/__init__.py` 和 `client/__init__.py` 的
side-effect import 给 TcpConnection / ConnectionPool / TdxClient / AsyncTdxClient /
ConnectionPool family / BestIP / Subclient family / RankingStore 等类安装构造器守卫，
然后 `del` 掉模块引用不让 re-export。

**为什么是债**：
1. 原始类（TcpConnection.__init__ / ConnectionPool.__init__ / TdxClient.__init__）的
   签名、参数校验、fail-closed 逻辑是"旧版"，hardening 在外面用 closure 包住、替换掉
   `__init__`。两边改一边漏——V17 就是"先立守卫、后 merge"，merge 永远没做。
2. transport/__init__.py 里写着顺序注释：
   ```
   # Host hardening order matters. First load the canonical hosts module...
   ```
   这等于说"正确用法就是先这样 import，然后按特定顺序安装补丁"——把运行时副作用当
   包初始化的一部分。
3. 维护者要理解 ConnectionPool 的构造行为，先读 pool.py 的 __init__，再回头看
   transport/__init__.py 里装的 pool_family_hardening 和 connection_contract_hardening，
   再去看这两个 hardening 文件里到底改了什么。一条逻辑链跨 3-4 个文件。

**改动范围**：
- `transport/_connection_contract_hardening.py`（250 行）→ merge 进 `transport/base.py`
- `transport/_host_selector_hardening.py`（56 行）→ merge 进 `transport/hosts.py`
- `transport/_pool_family_hardening.py`（208 行）→ merge 进 `transport/pool.py`
- `transport/_ranking_hardening.py`（172 行）→ merge 进 `transport/speedtest.py`
- `client/_async_concurrency_hardening.py`（50 行）→ merge 进 `client/async_.py`
- `client/_bestip_hardening.py`（91 行）→ merge 进 `client/sync.py`
- `client/_pool_binding_hardening.py`（149 行）→ merge 进 `client/core.py` 或 `client/sync.py`
- `client/_subclient_family_hardening.py`（49 行）→ merge 进 `client/sync.py`
- `transport/__init__.py`：删除 side-effect import + del hardening 模块引用
- `client/__init__.py`：同上

**不变量保持**：外部 `from tstdx.transport import TcpConnection`、`TdxClient(...)`、
`ConnectionPool(...)` 行为不变。guard 测试（`tests/transport/test_connection_contract_hardening.py`、
`test_pool_family_binding_contract.py`、`test_bestip.py` 等）继续锁原始构造行为——
hardening 文件**本身**没有测试，真正的判据在 `tests/transport/` 和 `tests/client/` 里。

### P0-B：DirectProviderExecutor god class（scope: runtime/executor.py）

**事实**：719 行。里面有三个问题：

1. **DIRECT_BINDINGS 同时持有硬编码表**：
   - `_CORE_BINDINGS` 是一个 17 条 DirectBinding 元组
   - `DIRECT_BINDINGS = (_CORE_BINDINGS + ...)` — 其实 `_executor_for()` 对未列出的三元组
     统一返回 `_migrated_capability`，所以 DIRECT_BINDINGS 是"注册表全量三元组 × 是否
     有专用执行体"的派生结果。但 executor.py 自己写死了 `_CORE_BINDINGS`，catalog 又有
     `MIGRATED_BINDINGS`——两个平行元数据源。

2. **`_migrated_capability` 方法（~150 行）是大型 backend 分派器**：
   ```python
   if meta.backend == "web_session": ...   # getattr(WebQuoteSession(), meta.method)
   if meta.backend == "tdx_client": ...    # TdxClient()
   if meta.backend == "f10_client": ...    # F10Client()
   if meta.backend in {"ex_client", ...}: ...  # ExMarketClient() / GoodsClient() / MacClient()
   if meta.backend == "direct_adapter": ...    # resolve_channel_adapter()
   if meta.backend == "web_adapter": ...        # ⚠️ 下面又有 capability 特化
   if meta.backend == "composed": ...           # ⚠️ 下面又有 capability 特化
   ```
   `web_adapter` 和 `composed` 两个 backend 下面还有**第二轮 if-elif** 按 capability 分派：
   - `capability == "minute_klines"` → 按 provider 选 EastmoneyHistoryKlineSource 还是 MinuteKlineSource
   - `capability == "history"` → 同上抉择
   - `capability == "adjusted_bars"` → AdjustEngine + DayBarReader + TdxClient
   - `capability == "sync_daily"` → LocalDaySink + TdxClient

   这些**按 provider 特化的选择**和 **composed capability 的实现逻辑** 都没写进 catalog
   元数据，硬编码在 executor 里。

3. **7 个 dedicated 方法**（`_tdx_bars` / `_tdx_quotes` / `_tdx_snapshot` / ...）里
   `_tdx_bars` 24 行 + `_tdx_quotes` 20 行 + `_tdx_snapshot` 4 行 + ... 风格不统一——有的
   有参数校验、有的没有、有的复用 `_hop_timeout`、有的直接 `self.timeout`。

**改动方案**：

a. **`_CORE_BINDINGS` 派生化**：改成 `DEDICATED_CAPABILITY_NAMES = frozenset({...})`，
   `DIRECT_BINDINGS` 统一从 `_registry_triples()` + `DEDICATED_CAPABILITY_NAMES` 派生。
   删掉 executor.py 里的硬编码 `_CORE_BINDINGS` 元组。

b. **`_migrated_capability` 的第二轮分派搬进元数据**：
   - 在 `catalog/capability.py::MigratedCapabilityBinding` 上加一个可选 `factory: str | None`
     字段，格式 `"tstdx.web.history:SinaHistoryKlineSource"` 这种 lazy import 路径。
   - `web_adapter` 分支的 MinuteKlineSource/EastmoneyHistoryKlineSource/SinaHistoryKlineSource
     选择，把**按 provider 决定选哪个 Source 类**搬进 catalog 里 migrated binding 的元数据。
   - `composed` 分支的 adjusted_bars 和 sync_daily 这两个 capability，它们的执行逻辑
     是"非适配器式"的（要读本地文件 + 走 TDX Client），**不能简单塞进元数据**。这两个
     保留为 executor 的专用方法，但把 capability 名列入 `DEDICATED_COMPOSED_CAPABILITIES`
     并在 executor.py 顶部集中声明。

c. **executor.py 行数目标**：从 719 行降到 ~400 行——元数据驱动的 backend 分派器，
   加上 dedicated 方法（合并风格）。

**不变量保持**：DIRECT_BINDINGS 三元组数量不变（仍是 251），DEDICATED_CAPABILITIES 集合
扩展为 9（原 7 + adjusted_bars + sync_daily）。executor.execute() 对外行为不变。

### P1-A：web/ 目录过散（scope: web/）

> **落地结果**：已按 Phase 3 收口。6 个 Provider 目录 `web/{tencent,sina,eastmoney,baidu,jsl,boc}/`
> 各含一个 `adapters.py`（**实际文件名是 `adapters.py`，不是方案里写的 `adapter.py`**）；
> `web/adapters.py`、`web/adapters_ext.py`、`web/adapters_baidu.py`、`web/adapters_index.py`、
> `web/adapters_margin.py`、`web/history.py` 六个物理文件已删除（共 18 个类全部分流到新包，账见 §3 Phase 3）；
> provider 无关的域文件
> （`corporate.py` / `boards.py` / `chip.py` / `fundflow.py` / `news.py` / `wencai.py` /
> `ticks.py` / `adapters_fund.py` / `efinance_options.py` / `efinance_deriv.py` /
> `efinance_fund.py` / `longhu.py` / `hot_rank.py` 等）按原计划保持原位不动。
> `web/iwencai` 子包**未创建**——i问财适配器不是 HTTP Source 类归组的对象，仍在 `web/wencai.py`。

**事实**（方案提出时）：web/ 有 40+ 文件，混合了：
1. `adapters.py`（884 行）—— TencentSource/SinaSource/EastmoneySource/BocSource/JslSource
   五个 HTTP Source 类。还写了 `KlineSource`（腾讯/百度 K 线）、`TencentTickSource`
   （但 ticks.py 里又有 TencentTickSource）
2. `adapters_ext.py`（MinuteKlineSource/MinuteSource/SuggestSource/MinuteWebSource）
3. `adapters_baidu.py`（BaiduSource，一个类）
4. `adapters_margin.py` / `adapters_index.py` / `adapters_fund.py` / `adapters_efinance_*`
   — 东财特化的 Source 类，按域拆文件
5. `history.py`（SinaHistoryKlineSource + EastmoneyHistoryKlineSource，**同时**
   走"adapter 类"风格）
6. `session.py` + `_session_p1.py` + `_session_p2.py` + `_session_p3.py` + `_session_market.py`
   + `_session_news.py` + `_session_info.py` + `_session_fund_v2.py` + `_session_astock.py`
   — WebQuoteSession 的按域 mixin，一个 provider 组合所有域方法
7. 独立文件：`corporate.py` / `fundflow.py` / `news.py` / `longhu.py` / `hot_rank.py`
   / `boards.py` / `market_stats.py` / `history.py` / `global_market.py` / `wencai.py`
   / `ticks.py` / `limits.py` / `chip.py` / `governance.py` / `esg.py` / `fin_report.py`
   / `efinance_deriv.py` / `efinance_options.py` / `fund_company.py` / `fund_manager.py`
   / `fund_rank.py`

两种风格并存：
- **adapter 类风格**：`SinaHistoryKlineSource().fetch_bars(symbol, period, count)`
  —— 一个类 = 一个能力域的全部 HTTP 请求封装
- **session mixin 风格**：`WebQuoteSession().sina_history_kline(symbol, ...)`
  —— 一个 session 组合 N 个域 mixin，一个 provider 走一个 session

这两种风格在 migrated capability 的 `backend="web_session"` 和
`backend="direct_adapter"` 分派里都能看到——executor 的 `_migrated_capability` 方法
按 catalog 元数据选路径，catalog 又按 provider_bindings.py 的 channel 声明选 adapter。
但有些 capability 比如 `minute_klines` 同时存在于 **tencent 的 minute_kline channel**
（adapter 类风格，MinuteKlineSource）和 **eastmoney 的 kline channel**
（adapter 类风格，EastmoneyHistoryKlineSource）——executor 的 `_web_adapter_call` 里
硬编码了"provider==eastmoney 选 EastmoneyHistoryKlineSource，否则选 MinuteKlineSource"。

**改动方案**（最小侵入）：
1. **不改两种风格并存的事实**——它们都能工作且 executor 元数据驱动后两条路径并行即可。
2. **按 Provider 重新组织 web/ 目录**：
   ```
   web/
   ├── base.py                  # WebSource 基类（已存在）
   ├── _base_http.py            # httpx 会话管理（已存在）
   ├── _base_em.py              # 东财特化基类（已存在）
   ├── _base_core.py            # 核心 HTTP 工具（已存在）
   ├── _base_retry.py           # 重试（已存在）
   ├── _paginate.py             # 分页（已存在）
   ├── normalize.py             # symbol normalization
   │
   ├── tencent/                 # 腾讯源
   │   ├── adapter.py           # TencentSource / KlineSource / TencentTickSource / TencentGlobalSource / TencentMarketStatSource / TencentBoardRankSource
   │   └── mixin.py             # WebQuoteSession.mixin
   ├── sina/                    # 新浪源
   │   ├── adapter.py
   │   ├── history.py           # SinaHistoryKlineSource
   │   └── mixin.py
   ├── eastmoney/               # 东财源
   │   ├── adapter.py           # EastmoneySource
   │   ├── history.py           # EastmoneyHistoryKlineSource
   │   ├── adapter_margin.py    # EastmoneyMarginSource
   │   ├── adapter_index.py     # EastmoneyIndexConstituentsSource
   │   ├── adapter_fund.py      # FundSource
   │   ├── efinance_deriv.py    # EastmoneyFuturesSource
   │   ├── efinance_options.py  # EastmoneyOptionsSource
   │   ├── fin_report.py        # EastmoneyF10ReportSource
   │   ├── fundflow.py          # EastmoneyRankSource / FundFlow / LimitPool / ...
   │   ├── news.py              # EastmoneyNewsSource / EastmoneyResearchVisitSource
   │   ├── hot_rank.py          # EastmoneyHotRankSource
   │   ├── longhu.py            # EastmoneyTopListSource
   │   └── mixin.py             # WebQuoteSession.mixin
   ├── baidu/                   # 百度源
   │   └── adapter.py           # BaiduSource
   ├── jsl/                     # 集思录源
   │   └── adapter.py           # JslSource
   ├── boc/                     # 中行外汇
   │   └── adapter.py           # BocSource
   ├── iwencai/                 # 同花顺问财
   │   └── adapter.py           # WencaiSource
   │
   ├── session.py               # WebQuoteSession 聚合（跨 provider 通用）
   ├── _session_*.py → 各自合并到上面各自的 mixin.py 里
   └── sources.py               # KNOWN_SOURCES 注册表 + create_source() 工厂
   ```

3. **import 路径重写**：从 `from tstdx.web.adapters import TencentSource` 变成
   `from tstdx.web.tencent.adapter import TencentSource`。改 catalog/provider_bindings.py
   的 ChannelBindings 元组（AdapterRef = `(module_path, class_name)`），以及
   executor/_migrated_capability 里动态 import 的路径。
4. **不改能力域文件的物理位置**：`corporate.py` / `chip.py` / `boards.py` 等按域拆的独立
   Source 文件暂不合并——它们本身就是 provider 无关的（corporate 可能同时走 sina + eastmoney），
   而且改它们的 import 路径比改按 Provider 分的代价更大。

### P2-A：Derived Provider 定位模糊（scope: providers/__init__.py）

**事实**：`derived` Provider 声明了 130+ capability，全部 `backend="web_session"`。
执行时相当于 `WebQuoteSession().<method>(*)`——而 WebQuoteSession 的方法分散在
`_session_p1.py` / `_session_p2.py` / `_session_p3.py` / `_session_news.py` / ... 里，
没有 capability 契约守护（只有少部分在 typed_query 里）。

这个 Provider 的真实语义是"把 eastmoney/sina/tencent/baidu 等多个 first-party Provider
的能力**聚合**成一个单一 Provider 视角"——但 runtime 不做实际聚合，只是把 capability
name 换成 WebQuoteSession 的 method name 而已。provider_first 不变量在这里有一个
"名义上通过了、实际上是空转"的味道。

**处理**：**不改**。derived Provider 作为"capability 目录视图"是有价值的——让用户不用关心
走哪家 Web Source 就能调方法。它的"空转"语义由 catalog 的 CHANNEL_API_EXEMPT 锁死
（没有 channel adapter 注册表条目），runtime 通过 `_migrated_capability` 分发到
WebQuoteSession 是设计意图而非 bug。在**重构方案里保留现状**，只在未来需要时给它加
真正的合成执行体。

### P2-B：evidence pointer 元数据维护（scope: tests/architecture/ 相关文件）

**事实**：离线全量测试唯一 FAIL 是 `wt_v18b26step` 指针没登记回收。这是纯 metadata
维护问题，不是功能 bug。

**处理**：**不在本方案处理**。让当前 G43 失败自然作为后续治理的触发器。

### P2-C：Windows 开发环境 Python 缺失（scope: 本地 .venv）

**事实**：本项目 `.venv/Scripts/` 里只有 `pythonw.exe`，没有 `python.exe`，导致
`python -m pytest` 等命令失败（README 的 `docs/configuration.md` 已说明这个问题）。
系统 `WindowsApps/python.exe` 是坏掉的 stub。

**处理**：**不属于仓库内代码**，在团队开发环境文档里加一条快速修复指引即可：
```powershell
# 修复 .venv 缺失 python.exe
& "C:\Users\Administrator\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe" `
    -m venv --upgrade --clear .venv
```

---

## 3. 重构方案（分阶段、可独立落地、不破坏对外契约）

### Phase 1：Hardening merge-back（P0-A）

**目标**：把 8 个 hardening 文件的加固逻辑物理合并进各自的原始类。删除 hardening 文件。
transport/__init__.py 和 client/__init__.py 删除 side-effect import + del 块。

**步骤**：

| # | 动作 | 涉及文件 | 验收判据 |
|---|---|---|---|
| 1a | 读 `_connection_contract_hardening.py`，把它包住的 TcpConnection.__init__ 逻辑（endpoint 归一化、timeout 有限性、boolean strict 化）直接写进 `transport/base.py::TcpConnection.__init__` | transport/base.py + transport/_connection_contract_hardening.py | `test_connection_contract_hardening.py` 全绿（重命名/迁到 `tests/transport/`） |
| 1b | 同上：把 async 版的加固逻辑写进 `transport/async_.py::AsyncTcpConnection.__init__` | transport/async_.py | 同上 |
| 1c | `_host_selector_hardening.py` 里 resolve_hosts 的"每调用返回独立 HostEntry 对象"逻辑写进 `transport/hosts.py::resolve_hosts` | transport/hosts.py | 删掉 hardening 文件后仍能通过 `test_host_selector_state_isolation.py` |
| 1d | `_pool_family_hardening.py` 里 family identity 强校验写进 `transport/pool.py::ConnectionPool.__init__` | transport/pool.py | 同上 |
| 1e | `_ranking_hardening.py` 把 RankingStore.disk_rankings 改为 probe-only 访问写进 `transport/speedtest.py::RankingStore` | transport/speedtest.py | 同上 |
| 1f | `_async_concurrency_hardening.py` 里 ConcurrentOutputFormatter 构造器守卫写进 `client/async_.py` | client/async_.py | 同上 |
| 1g | `_bestip_hardening.py` 里 detach_bestip_snapshot 构造器守卫写进 `client/sync.py::TdxClient` | client/sync.py | 同上 |
| 1h | `_pool_binding_hardening.py` 里 pool family identity 守卫写进 `client/core.py` | client/core.py | 同上 |
| 1i | `_subclient_family_hardening.py` 里 ExMarket/Goods/Mac/F10 子类 family 参数校验写进 `client/sync.py::ExMarketClient.__init__` 等 | client/sync.py | 同上 |
| 1j | 删除全部 8 个 hardening 文件 | - | git status 干净 |
| 1k | `transport/__init__.py`：删除 hardening import 块 + 顺序注释 | transport/__init__.py | 该文件的 `from .base import ...` 等后续 import 不受影响 |
| 1l | `client/__init__.py`：同上 | client/__init__.py | 同上 |
| 1m | 全量跑离线测试 + ruff + mypy | - | 0 red / ruff 0 / mypy 0 |

**风险**：hardening 文件做的是"替换 __init__ 方法 + 包一层校验"，merge 回去时如果原始 __init__ 还有别的调用方（比如外部脚本直接 `TcpConnection(...)`），merge 可能导致参数变化。**判据**：`tests/transport/test_connection_contract_hardening.py` 等 guard tests 会自动检验构造行为不变。

**回滚**：每个 hardening 文件独立 merge，按 1a-1i 顺序逐个 commit，每个 commit 后全量测试。任何一步测试回归直接 revert 该 commit。

### Phase 2：Executor god class 拆分（P0-B）

**目标**：DirectProviderExecutor 从 719 行降到 ~400 行。消除 `_migrated_capability` 里的
provider 特化 if-elif。把 executor 元数据驱动路径补齐。

**步骤**：

| # | 动作 | 涉及文件 | 验收判据 |
|---|---|---|---|
| 2a | 把 executor.py 里的 `_CORE_BINDINGS` 硬编码元组删除，改为 `DEDICATED_CAPABILITY_NAMES = frozenset({"quotes","bars",...})` 派生 DIRECT_BINDINGS。direct_binding_factory 用集合 membership 判定 | runtime/executor.py | DIRECT_BINDINGS 数量不变（仍是 251），DIRECT_BINDINGS for-each 里每条的 executor_name 不变 |
| 2b | `catalog/capability.py::MigratedCapabilityBinding` 加一个可选 `factory` 字段（lazy import 路径 `"tstdx.web.history:EastmoneyHistoryKlineSource"`），以及一个 `kwargs` 字段（adapter 构造时的额外参数，比如 `interval=1`） | catalog/capability.py | 不填 factory 时默认行为不变 |
| 2c | executor.py `_migrated_capability` 的 `web_adapter` 分支**删除**第二轮 if-elif（minute_klines/history 的 provider 抉择）。改为统一读 `binding.factory`，有 factory 就按 factory 路径 import，没有就按 binding.capability 走通用路径 | runtime/executor.py | minute_klines 和 history 这两个 capability 在 catalog 里 migrated binding 补上 factory 元数据；executor 不再硬编码 `if provider == "eastmoney"` |
| 2d | executor.py 里 `_composed_call` 分支的 adjusted_bars + sync_daily 保留，但重命名为 executor 的 dedicated 方法（不再藏在 `_migrated_capability` 里），capability 名加入 `DEDICATED_CAPABILITY_NAMES` | runtime/executor.py | adjusted_bars / sync_daily 在 DIRECT_BINDINGS 里 executor_name 变成 `_tdx_bars` 风格的 dedicated；不再走 `_migrated_capability` 的 composed 分支 |
| 2e | dedicated 方法风格统一：全部复用 `_hop_timeout`，有参数校验，finally 里 close 连接 | runtime/executor.py | `_tdx_bars` / `_tdx_quotes` / `_tdx_snapshot` / `_tdx_minute` / `_tdx_trades` / `_tdx_security_count` / `_tdx_security_list` / `_adjusted_bars` / `_sync_daily` 共 9 个 dedicated |
| 2f | executor.py 总行数 | runtime/executor.py | ≤ 420 行 |
| 2g | 全量跑离线测试 + ruff + mypy | - | 0 red / ruff 0 / mypy 0 |

**风险**：migrated capability 元数据补 factory 时如果路径写错，executor 会 `ImportError`。**判据**：`tests/runtime/test_kernel_config_wiring.py` 等运行期测试会实际调到这些 capability，错误会当场暴露。

**回滚**：每个步骤独立 commit，2a-2b 不动行为（只改内部结构），2c-2d 改变行为（executor 逻辑变化），分开提交。

### Phase 3：web/ 目录按 Provider 重组（P1-A）

**目标**：40+ web/ 文件按 Provider 聚合为 7 个子目录，import 路径统一。两种风格（adapter class + session mixin）并存但目录清晰。

**步骤**：

| # | 动作 | 涉及文件 | 验收判据 |
|---|---|---|---|
| 3a | 在 `web/tencent/`、`web/sina/`、`web/eastmoney/`、`web/baidu/`、`web/jsl/`、`web/boc/` 下各自建 `adapters.py`（原方案写的 `web/iwencai/` 未建，见 P1-A 落地结果） | web/* | 每个 adapters.py 只含该 Provider 的 HTTP Source 类 |
| 3b | 把 `adapters.py` 里 TencentSource/KlineSource 迁到 `tencent/adapters.py`，SinaSource 迁到 `sina/adapters.py`，EastmoneySource 迁到 `eastmoney/adapters.py`，BocSource 迁到 `boc/adapters.py`，JslSource 迁到 `jsl/adapters.py` | web/adapters.py → web/tencent/adapters.py 等 | 旧 adapters.py 删掉后，所有 `from tstdx.web.adapters import TencentSource` 的引用改成新路径 |
| 3c | `history.py` 拆到各自目录：SinaHistoryKlineSource → `sina/adapters.py`，EastmoneyHistoryKlineSource → `eastmoney/adapters.py` | web/history.py → web/sina/ + web/eastmoney/ | 同上 |
| 3d | `adapters_baidu.py` → `baidu/adapters.py`，`adapters_margin.py` 的东财类 → `eastmoney/adapters.py` | web/adapters_*.py | 同上 |
| 3e | `adapters_ext.py` 里 MinuteKlineSource 迁到 `tencent/adapters.py`（它是腾讯专用），MinuteSource 迁到 `tencent/adapters.py`，SuggestSource 迁到 `sina/adapters.py` | web/adapters_ext.py | 同上 |
| 3f | `efinance_options.py` / `efinance_deriv.py` / `fundflow.py` / `longhu.py` / `hot_rank.py` / `news.py`（EastmoneyNewsSource 部分）迁到 `eastmoney/` 下 | 各独立文件 | 同上（**未执行**：按域切的文件留 `web/` 根，见执行结果） |
| 3g | catalog/provider_bindings.py 的 AdapterRef 元组全部更新为新模块路径 | catalog/provider_bindings.py | `resolve_channel_adapter("tencent", "quote")` 返回的仍是 TencentSource 类，import 路径变但行为不变 |
| 3h | executor/_migrated_capability 的 `web_session` / `direct_adapter` / `web_adapter` 分支里动态 import 路径更新 | runtime/executor.py | 同上 |
| 3i | web/__init__.py 的 `_LAZY` 子模块名 + `sources.py::create_source()` 里的工厂路径更新 | web/__init__.py | `from tstdx.web import create_source; create_source("quote")` 行为不变 |
| 3j | `_session_*.py` mixin 文件暂不迁——它们被 WebQuoteSession import，迁代价大。 | - | 本次不迁 session mixin |
| 3k | 全量跑离线测试 + ruff + mypy | - | 0 red / ruff 0 / mypy 0 |

**执行结果**：3a–3e、3g–3k 全部落地；3f **未执行**（理由见下）。3a 覆盖面为 6 个子目录（非 7，
`web/iwencai/` 未建）。3j 最终未新建 `mixin.py` 锚点文件——3a 建的包已用 `__init__.py` 承载同样的
"未来合并锚点"作用，再多建一个空文件只会是孤儿逻辑。

搬迁账（18 个类，按类名逐一对账，无丢失）：

| 目标模块 | 迁入的类（来源） |
|---|---|
| `web/tencent/adapters.py`（7） | TencentSource、TencentExternalSource、HkSource、UsSource、KlineSource（← adapters.py）；MinuteKlineSource、MinuteSource（← adapters_ext.py） |
| `web/sina/adapters.py`（4） | SinaSource、SinaHkSource（← adapters.py）；SinaHistoryKlineSource（← history.py）；SuggestSource（← adapters_ext.py） |
| `web/eastmoney/adapters.py`（4） | EastmoneySource（← adapters.py）；EastmoneyHistoryKlineSource（← history.py）；EastmoneyMarginSource（← adapters_margin.py）；EastmoneyIndexConstituentsSource（← adapters_index.py） |
| `web/baidu/adapters.py`（1） | BaiduSource（← adapters_baidu.py） |
| `web/jsl/adapters.py`（1） | JslSource（← adapters.py） |
| `web/boc/adapters.py`（1） | BocSource（← adapters.py） |

**3f 为什么不执行**：`efinance_options.py` / `efinance_deriv.py` / `fundflow.py` / `longhu.py` /
`hot_rank.py` / `news.py` 留在 `web/` 根——它们是**按能力域**切的文件，共享 `_base_em.py` 的
`_EastmoneyJson` 主机池 failover 与 `_s / _fopt` 归一化辅助。把它们塞进 `eastmoney/` 只会得到
一个"Provider 目录里按域再分一层"的四不像，而 import 路径的改动面反而是最大的一批（这些文件名
出现在 cli/、integration/、tests/web/ 多处）。方案里 3d 说的"保留文件名，改变位置"本身就与 3a 的
"每个 Provider 一个 adapter" 相冲突——最终按 3a 的口径收口：**一个 Provider 一个 `adapters.py`，
域文件留原位**。

**风险**：import 路径重写量大（`tstdx.web.*` 出现在 tests/、catalog/、executor/、cli/、integration/ 里），任何漏改都会导致 ImportError。**判据**：`test_doc_code_consistency.py` 的事实路径格会自动检测所有 import 路径是否可解析。

**回滚**：按 Provider 分 7 步（3a-3e + 3f + 3g-3h + 3i），每步改完 catalog 和 executor 后跑全量测试。任何一步回归 revert 该 Provider 相关改动。

### Phase 4：收尾

| # | 动作 | 说明 |
|---|---|---|
| 4a | 把 Phase 1-3 的改动合并成一个 PR，CI 全绿 | 每个 Phase 可以独立 PR，但建议合为一组 |
| 4b | 更新 docs/ARCHITECTURE.md 的分层表 | 反映 hardening 删除和 web/ 目录重组 |
| 4c | 运行 `test_doc_code_consistency.py` | 文档里的 import 路径、规模数字与代码对齐 |
| 4d | 运行 `make gates` 的 11 个门禁 | 全绿 |
| 4e | 记录到本文末尾（实际的行数变化、文件删除清单） | 作为 V20 执行台账 |

---

## 4. 不处理但登记的项

| # | 项 | 为什么不处理 | 后续条件 |
|---|---|---|---|
| 1 | v18/19 台账里 G43 evidence pointer 元数据 | V19 28-D 已登记，G43 的根因不在本文范围 | **已处理**：本轮把两处候选树的「已回收 + 可解开的提交锚」就补进 V18 台账，`test_evidence_pointers.py` 转绿 |
| 2 | ARCHITECTURE.md 与 client/api.py 的 minute/trades 默认 provider=tdx 口径对齐 | api.py docstring 里的 "tdx 已下线" 注释是诚实的，ARCHITECTURE.md 写的是"协议覆盖"而非"用户能拿到" | 发布硬化时处理 |
| 3 | derived Provider 真实合成执行体 | 当前语义足够（capability 目录视图），加真实合成需要新执行策略 | 有需求时再加 |
| 4 | trade/ 实验模块处置 | README 明确标记"未进主链"，V18 第 5 轮已拒删 | 用户决定 |
| 5 | Windows 开发环境缺 `python.exe`（venv 里只有 `pythonw.exe`） | 本地环境问题，仓库内代码无关 | **已缓解**：本轮把 `scripts/build_package.py::_run` 的子进程 `stdin` 显式接 `DEVNULL`，无控制台启动器下也能跑通构建（详见 §8）；venv 重建指引仍建议团队执行 |
| 6 | docs/archive/plans/ 30+ 份历史方案归档 | 已经在 archive 目录，README 里明确"都不是现行契约" | 不需要处理 |

---

## 5. 验收标准（逐条附实测值）

Phase 1-3 全部完成时，满足以下条件（下表"实测"列的读数为 2026-09-26 收口现场值）：

| # | 判据 | 实测 | 结论 |
|---|---|---|---|
| 1 | hardening 物理清空：`Get-ChildItem -Recurse -Filter "*hardening*.py" tstdx\` 返回空 | 空 | ✅ |
| 2 | executor 行数 ≤ 420 | **710**（HEAD 719 → 710，`numstat` +63/−72） | ❌ **未达标**，见 §8 |
| 3 | DIRECT_BINDINGS 仍是 251 | 251 | ✅ |
| 4 | DEDICATED_CAPABILITIES = 9 | **7**（quotes / bars / snapshot / minute / trades / security_count / security_list） | ⚠️ 口径差异，见 §8 |
| 5 | `grep -r "from tstdx.web.adapters import"` 返回空（新路径 `tstdx.web.<provider>.adapters`） | 代码与现行文档 0 命中；仅历史台账/CHANGELOG/archive 有 | ✅ |
| 6 | 离线全量测试 0 red | **4041 passed / 9 skipped / 15 deselected**，G43 也已随本轮修复转绿 | ✅ |
| 7 | ruff + mypy 0 错 | ruff check 0 / ruff format 0 / mypy 0（`--warn-unused-ignores`） | ✅ |
| 8 | 单内核不变量：`tests/architecture/test_single_kernel_guards.py` 全绿 | 全绿 | ✅ |
| 9 | provider isolation：`tests/provider_isolation/` 全绿 | 全绿 | ✅ |
| 10 | 装包六面：wheel/SHA256 变化只来自删除的 hardening 文件 | `build_package.py --smoke` 走通，`runtime_files=190`；相位二/三同时改了模块布局，变化面不止 hardening | ⚠️ 见 §8 |

**新路径口径更正**：判据 5 里写的 `tstdx.web.<provider>.adapter`（单数）是方案初稿的笔误，
实际落地为 **`tstdx.web.<provider>.adapters`**（复数，与仓库既有 `adapters.py` 命名习惯一致）。

---

## 6. 不涉及的范围

- **对外 API 契约**：Client/AsyncClient 便捷方法签名、QuerySpec 形状、TypedQuery capability 名、
  DIRECT_BINDINGS 三元组数量（251）、DEDICATED_CAPABILITIES 集合扩展（7→9）
- **Provider 注册表结构**：11 个 Provider、ChannelSpec/ProviderSpec/ProviderRegistry 不可变
- **服务面形状**：CLI 31 子命令 / HTTP 10 端点 / WS 10 方法 / MCP 9 工具不变
- **协议层**：85 命令账本、61 精确解析器、codec/framing/primitive 不改
- **transport 核心实现**：TcpConnection/AsyncTcpConnection/ConnectionPool 的协议实现不变，
  只是构造器签名里合并 hardening 的 fail-closed 守卫
- **V18/V19 已做的工作**：配置面对账、判据手抄副本删除、文档承诺兑现——**不重新做**

---

## 7. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| Phase 1 merge 时 hardening 覆盖了原始类的合法签名变化 | 低 | 测试大面积回归 | 每步 merge 后立即跑 `tests/transport/` 或 `tests/client/` 对应目录测试 |
| Phase 2 executor 元数据补 factory 路径写错 | 中 | ImportError | catalog 补 factory 后，executor 动态 import 前 `importlib.util.find_spec` 检查；实际跑 capability 测试 |
| Phase 3 import 路径漏改 | 中 | 多处 ImportError | `test_doc_code_consistency.py` 事实路径格会自动检测所有 import |
| web/session.py 的 WebQuoteSession 方法迁移成本 > 收益 | 中 | Phase 3k 时间超预算 | 本方案明确 "Phase 3 不迁 session mixin"，只锚点化 |
| 190 个文件改动导致某个隐藏依赖回归 | 低 | 某个 capability 执行失败 | 每个 Phase 结尾跑 `make gates` 11 门禁 |
| `.venv` 缺失 python.exe 影响 Phase 测试执行 | 高 | 无法在本地跑 pytest | 见 P2-C：团队开发环境快速修复指引 |

---

## 8. 执行台账（收口实录）

**收口锚**：本轮收口发生在 `07477e8`（HEAD）之上的未提交工作树上；本节所有读数均为该现场实测。

### 8.1 三个 Phase 的落地情况

| Phase | 目标 | 落地 |
|---|---|---|
| Phase 1 hardening merge-back | 8 个 `*hardening*.py` 的守卫物理合并回基类并删除文件 | ✅ 完成；新增共享校验模块 `transport/_validation.py`；`tstdx/` 下 `*hardening*.py` 现为空 |
| Phase 2 executor 化简 | 去掉 `_CORE_BINDINGS` 硬编码分支，改由 catalog 元数据驱动 | ✅ 完成；17 条绑定的执行体由 `_executor_for` 规则函数派生，`MigratedCapabilityBinding` 新增 `factory: str` 字段承载 web_adapter 的 5 条惰性导入路径，executor 里 2 处 provider-specific if 分支删除 |
| Phase 3 web/ 按 Provider 归组 | 见 §3 Phase 3 | ✅ 完成（3f 除外）；18 个类分流到 6 个 Provider 包，6 个旧模块删除 |

### 8.2 删除清单

`tstdx/` 下 6 个 web 模块：`web/adapters.py`、`web/adapters_ext.py`、`web/adapters_baidu.py`、
`web/adapters_index.py`、`web/adapters_margin.py`、`web/history.py`；
以及 8 个 `*hardening*.py`（Phase 1）。
本轮另外清掉了两个开发期杂散产物：仓库根的 pytest 转储 `test_output.txt`（约 10 MB）与
零字节的 `scratch_measure24b.err`。

### 8.3 新增清单

`tstdx/web/{tencent,sina,eastmoney,baidu,jsl,boc}/` 6 个包 × (`__init__.py` + `adapters.py`)；
`tstdx/transport/_validation.py`；`.gitignore` 补 `.workbuddy-ai/`。

### 8.4 未达标的验收项（诚实登记）

| 项 | 计划 | 实际 | 处置 |
|---|---|---|---|
| executor.py 行数 | ≤ 420 | **710**（HEAD 719 → 710，净 −9） | 未达成。Phase 2 削掉的硬编码分支量与同期（V18/V19 收口）新增的 dedicated 方法/审计字段相抵。**不构成发布阻断**：无任何门禁以行数为判据，`test_single_kernel_guards.py` 等 33 个架构守卫全绿。作为后续简化债保留 |
| DEDICATED_CAPABILITIES | 9 | **7** | 口径差异而非缺失：`adjusted_bars` / `sync_daily` 走 `backend="composed"`（catalog 里 2 条 composed 绑定），不在这个常量里。方案把两套口径写成了一个数字，已按实测更正 |
| wheel 变化面 | 只来自删除的 hardening | 同时来自模块布局搬迁 | 表述过窄。判据实质（"装出来的包与源码树一致"）由 `build_package.py --smoke` 的 `runtime_files=190` 全量比对覆盖，已通过 |

### 8.5 三轮逻辑审查的覆盖面（每轮"修完再进下一轮"）

| 轮次 | 角度 | 结果 |
|---|---|---|
| 第 1 轮 | 全量离线测试基线、旧 adapter 残留引用、可达性审计、文档链接、contract/golden/spec/benchmark 四审计、六面贯通计数、`_LAZY`/`_ADAPTER_SPECS`/`__all__` 路由表可解析性、TODO/`NotImplementedError` 逐条核 | 修 1 处发布阻断（evidence pointer）+ 31 处 ruff + 2 处多余 `type: ignore` + 6 个零字节 `__init__.py` 缺头 |
| 第 2 轮 | 公开方法孤儿普查（1103 个）、模块级常量普查（214 个）、`_validation.py` 消费者核对、打包面 | 删 1 处死代码、`__all__` 13 项收窄到 6 项、修复 `build_package.py` 在无控制台解释器下的构建失败 |
| 第 3 轮 | 未 await 协程/告警构成、`while True` 死循环面、catalog 绑定面与 `factory` 路径可解析性、配置 schema 字段孤儿普查 | 0 缺陷：告警全为有意 caveat、6 处 `while True` 均有确定性出口、229 条绑定与 5 条 `factory` 路径全解析、配置字段无孤儿 |

### 8.6 收口读数（全部离线可复现）

- `pytest tests -m "not network"` → **4041 passed / 9 skipped / 15 deselected**
- `ruff check .` → 0；`ruff format --check .` → 0；`mypy tstdx/ --warn-unused-ignores` → 0
- `scripts/audit_reachability.py --strict` → 模块 189 / 可达 174 / 白名单豁免 15 / 无未登记孤儿
- `scripts/check_docs_links.py` → 96 个文档全通过
- `scripts/contract_audit.py --ci` → 172 条 capability 全部落在声明形状内
- `scripts/build_package.py --smoke` → `runtime_files=190`，Twine PASSED，临时 venv 装包 + CLI 冒烟通过
- 六面计数：CLI 31 子命令 / HTTP 10 路由 / WS 10 方法 / MCP 9 工具 / `KNOWN_SOURCES` 31 / `DIRECT_BINDINGS` 251 / `COMMANDS` 85 / `_LAZY` 67 / `_ADAPTER_SPECS` 31
