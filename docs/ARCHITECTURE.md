# atst 架构（ARCHITECTURE）

> 本文只描述**当前代码事实**（v1.0.0，v13/v17 单一执行内核）。演进历史与逐轮重构台账见
> [`docs/archive/plans/`](archive/plans/)；历史方案（v1–v16）的 L1/L2 缓存、UnifiedQuoteAPI
> 门面、五级降级路由、sources/sinks 层、v14 信封运行时、`execution/` DAG 与 `provider/`
> 路由件均已物理删除，不再是事实。

## 1. 项目定位

通达信（TDX）行情数据通用协议基础设施：协议编解码 → 多源 Provider → 统一查询内核 →
CLI / HTTP / WS / MCP 服务面。同步交付物为 `atst` Python 包，**协议实现 100% 自有**
（洁净室流程，协议事实源于自有抓包与本地文件分析，禁止复制开源代码）。

设计目标：

- **协议全覆盖**：7709 标准 / 7727 扩展市场 / MAC 专属 / F10 资料 / 商品语义，未知命令走
  L2 启发式 + L3 原始透传，**永不丢包**。
- **数据全兼容**：市场 × 品种 × 周期 × 口径差异全部参数化为 `DataProfile`。
- **实时为一等公民**：PushChannel + 增量合并 + 断线补数 + 背压 + 重连。
- **Provider-first 运行时**：公开查询先编译为单 Provider / 单 Channel 的 `QueryPlan`，
  跨 Provider fallback 只能由显式策略层触发。
- **零缓存直达数据源**：每次公开查询都编译为唯一 `QueryPlan` 并直接请求绑定的 Provider；
  不存在结果级缓存、结果级负缓存或请求合并层（web 传输层对失败主机有进程级 TTL 排序，
  只改变尝试顺序、不省掉任何一次数据请求），provenance 始终反映真实直连。
- **Fail-closed Streaming**：canonical stream 采用显式 `StreamState`，worker 半死、启动失败、
  stop 超时与终态重启都不能静默生成第二 worker。

## 2. 唯一主执行链

```
CLI / HTTP(runtime_http) / WS(runtime_ws) / MCP(integration/mcp)
        │  （四个服务面全部只翻译，委托 Client，禁止任何执行逻辑）
        ▼
atst.Client / AsyncClient（client/api.py，唯一业务入口，172 capabilities）
        │  QuerySpec（query.py：capability + symbols + provider + currentness …）
        ▼
UnifiedRuntime（runtime/kernel.py，唯一内核，零缓存）
        │  QueryPlanner.compile(spec) → QueryPlan（单 Provider / 单 Channel）
        ▼
DirectProviderExecutor（runtime/executor.py）
        │  DIRECT_BINDINGS[(provider, channel, capability)] → 精确 executor 方法
        ▼
providers/ 注册表（Provider / Channel / Capability 单一事实源）
   ├─ tdx        → protocol/ + client/ + transport/（85 命令、61 解析器、连接池）
   ├─ tencent/sina/eastmoney/baidu/jsl/boc → web/<provider>/（45+ HTTP 源，按 Provider 归组）
   ├─ local_vipdoc → reader/（本地 .day/.lc1/.lc5/.dat/gpcw 二进制）
   ├─ iwencai    → web/ 问答式源
   ├─ builtin    → 内置聚合/常量
   └─ derived    → 显式聚合能力
        │  QueryResult（result.py：data + meta.provenance 溯源）
        ▼
跨 Provider 回退：仅限显式 FallbackPolicy → ProviderOrchestrator（runtime/orchestration.py，
唯一通道），四面入口的 provider= 与 fallback= 互斥（同时给出即 ValidationError）。
流式：StreamSpec / StreamPlanner（stream_contract.py）→ StatefulQuoteStream（streaming/）
```

**核心不变量**（由 `tests/provider_isolation/`、`tests/runtime/`、`tests/streaming/` 锁定）：

- **零缓存**：每次请求直达绑定 Provider，无结果/负/提升缓存，无请求合并。
- **provider-first**：一个 Plan 永不私选第二 Provider；provenance 校验失败即抛。
- **单内核**：执行只发生在 `UnifiedRuntime → DirectProviderExecutor`。
- **配置面即执行面契约**：`Config` 里每个键都被内核读取并改变行为，配置 → 传输只有
  `atst.transport.pool.pool_settings_from_config` 一个翻译点，无第二读者
  （`tests/runtime/test_kernel_config_wiring.py`、
  `tests/transport/test_pool_settings_from_config_contract.py`）。

## 3. 分层与包职责（含实现细节）

| 层 | 模块 | 状态 / 实现要点 |
|---|---|---|
| 协议层（冻结） | `codec/`（帧/变长数/字符集）、`protocol/`（命令账本 + 三级解析 L1/L2/L3）、`transport/`（池/心跳/测速）、`client/`（`api.py` 唯一业务入口 `Client`/`AsyncClient` + `core.py` 共享纯协议 SSOT + `sync.py`/`async_.py` `TdxClient` + `factory.py` 按市场族造客户端的 `get_client`）、`charset/` | 独立完备。`COMMANDS` 账本 85 条、`PARSERS` 三级解析器 61 个；未知命令走 L3 原始透传。 |
| 数据源层 | `providers/`（静态不可变注册表）、`web/`（六个 Provider 子包 `tencent`/`sina`/`eastmoney`/`baidu`/`jsl`/`boc` 各承载一家适配器，跨 Provider 的域模块留包根）、`reader/`、`profile/`（DataProfile 复权/周期口径） | 活。`ProviderSpec`/`ChannelSpec` 是 Provider/Channel/Capability 唯一事实源；`ChannelSpec.operationally_supports()` 按 `capabilities - unavailable_capabilities` 判「声明了但已下线」。 |
| 契约层（无执行） | `query.py`、`result.py`、`batch.py`、`typed_query.py`、`stream_contract.py`、`errors.py`、`error_envelope.py`、`diagnostics.py`（运行期告警码与「未证实」登记簿）、`catalog/`（capability 目录与调用校验、Provider channel→adapter 绑定表、Provider 隔离契约/守卫/一致性审计） | 活。 |
| 内核层 | `runtime/`（`kernel.py` 唯一内核、`executor.py` 精确绑定执行、`orchestration.py` 显式跨源编排、`audit.py` 启动三方对账、`identity.py`/`provenance.py` 执行身份与溯源守卫、`freshness.py` 当期性证据裁决） | 活。`UnifiedRuntime` 是配置面唯一读者；`DIRECT_BINDINGS` 启动时由 `audit_runtime()` 三方对账。 |
| 流式层 | `streaming/`（`base.py` 传输与订阅底座、`engine.py` 引擎、`state.py` 状态格、`stateful.py` `StatefulQuoteStream`/`AsyncStatefulQuoteStream`、`push.py` 推送面） | 活，入参契约在根级 `atst.stream_contract`。`StreamState` 五态：`CREATED`/`RUNNING`/`STOPPING`/`CLOSED`/`FAILED`。 |
| 服务面层 | `cli/`、`integration/`（`runtime_http.py` HTTP 面、`runtime_ws.py` JSON-RPC 分派、`runtime_ws_server.py` WS 服务器、`serialization.py` 结果序列化、`wire_fields.py` 三面入参白名单、`integration/mcp/` MCP 面）、`output/`（DataFrame/Parquet/DuckDB）、`sink/` | 活，全部 Client-backed。`WS` 控制面：`subscribe`/`unsubscribe`/`list` + `push` 帧（snapshot/tick/error），订阅表**每连接私有**。 |
| 类型化糖衣 | `typed_query.py`（`CapabilityQuery` + Domain Record）、`domain/`（records/symbol/日历） | 全量接通：`Client.typed` / `AsyncClient.typed`，字段名与内核方法签名一一对应。 |
| 基础设施 | `config/`（6 源合并 + 严格校验）、`observability/`（Prometheus 风格指标，零硬依赖）、`feedback/`（错误/用量上报） | 活。 |
| 实验模块 | `trade/`（交易协议模拟器，纯内存模拟，不接入内核） | 唯一剩余待裁定项。 |

> 根级白名单 11 项（`__init__.py` / `_version.py` / `batch.py` / `diagnostics.py` /
> `error_envelope.py` / `errors.py` / `query.py` / `result.py` / `stream_contract.py` /
> `typed_query.py` / `__main__.py`）由 `tests/architecture/test_namespace_layout.py` 钉死；
> 新增顶层模块必须同步进 README 结构树与 `__all__` ⇔ `_LAZY`。

## 4. Provider 注册表与跨源编排

`PROVIDERS`（`providers/__init__.py`）是静态、不可变的 Provider / Channel / Capability 单一事实源，
回答「谁能提供某能力、走哪条 Provider 内部 Channel」。Provider 身份是信任边界：选 `tdx` 绝不会
静默执行腾讯/东财/新浪；主机故障转移仍合法地发生在选中的 TDX Provider 内部。本地 vipdoc 数据是
独立 Provider，历史文件永远不能冒充实时 TDX 网络数据。

- **单 Plan 执行**：`QueryPlanner.compile` 产出单 Provider / 单 Channel 的 `QueryPlan`，
  `DirectProviderExecutor` 按 `DIRECT_BINDINGS`（`(provider, channel, capability) → executor 方法`，
  运行时从注册表生成，251 条，其中 17 条走专用执行体如 `_tdx_quotes`/`_tdx_bars`/`_web_quotes`，
  其余回落 `_migrated_capability`）精确派发。
- **跨源回退**：`FallbackPolicy` + `ProviderOrchestrator` 是**唯一**跨 Provider 容错通道；
  禁止隐式跨 Provider fallback（`DataSourceRouter` 已退化为单 Provider 选择器）。
- **当期性**：`currentness` 由 `runtime/freshness.py` 裁决，声明无人读的 F-44 在此收口。

## 5. 流式订阅

`StreamSpec`/`StreamPlanner` 把订阅请求编译为单 Provider 的流式计划，`StatefulQuoteStream` 维护
显式 `StreamState` 状态机（`CREATED → RUNNING → STOPPING → CLOSED`，异常走 `FAILED`）。
推送协议为 `push` 帧三类：`snapshot`（首帧全量）/ `tick`（增量）/ `error`；订阅表每连接私有，
`bind_connection` 用 `get_running_loop()` 绑定事件循环，断开即清空本连接订阅。

Fail-closed 要点（`tests/streaming/` 锁）：worker 半死、启动失败、stop 超时、终态重启都不能
静默生成第二 worker；`stop()` 必须排空在途数据后再原样抛回取消。

## 6. 服务面（只翻译，不执行）

| 服务面 | 入口 | 实现要点 |
|---|---|---|
| CLI | `cli/`（31 子命令） | 只翻译为 `Client` 调用；`build_parser()` 是四面共用的参数契约。 |
| HTTP | `integration/runtime_http.py`（`/v13/`，10 路由） | `create_runtime_app()` 绑定 `Client`；`wire_fields.py` 三面（CLI/HTTP/WS）入参白名单。 |
| WS | `integration/runtime_ws.py` + `runtime_ws_server.py` | `RuntimeJsonRpcHandler` 绑定 `Client`（非 raw runtime），内部读 `client.runtime.planner`；方法 `subscribe`/`unsubscribe`/`list`/`push`。 |
| MCP | `integration/mcp/`（9 工具） | stdio MCP 面，同样只翻译为 `Client.call(<capability>, …)`。 |

四面入口都经 `Client.call(<capability>, …)` / `Client.typed` 这一个业务入口；v16 Phase 2
已物理删除旧的门面层（`UnifiedQuoteAPI`），它不再是今天的接口。

## 7. 配置（5 段 / 6 源合并）

`atst.config.schema.Config` 由 5 段组成：`core` / `hosts` / `rate_limit` / `web` / `security`
（均为 dataclass，loader 对未知段 fail-closed；`cache`/`output`/`profile`/`sources`/
`observability`/`compatibility` 七段已删除）。`Client()` 缺省经 `atst.config.get_config()` 取
**6 源合并**的进程级惰性单例（env / file / …，显式构造参数仍优先），`UnifiedRuntime` 是配置面
唯一读者；配置 → 传输只有 `transport/pool.pool_settings_from_config` 一个翻译点。取舍见
[ADR-016](adr/ADR-016-config-surface-covers-execution-only.md)，用户面见
[docs/configuration.md](configuration.md)。

## 8. 契约与真相源

| 事实 | 唯一真相源 |
|---|---|
| Provider / Channel / Capability | `atst/providers/__init__.py`（`PROVIDERS` 注册表） |
| 可执行绑定 | `runtime/executor.py`（`DIRECT_BINDINGS`，启动时 `audit_runtime()` 三方对账） |
| capability 语义 / 参数校验 | `query.py`（`QuerySpec`）+ `catalog/capability.py`（`validate_call`） |
| 协议命令账本 | `protocol/` YAML 规范 + codegen + golden_audit |
| 公开导出面 | `atst/__init__.py::__all__`（懒加载 `_LAZY`） |
| 配置结构 | `atst.config.schema`（5 段，全部由内核读取；用户面 `docs/configuration.md`） |
| 配置 → 传输层参数 | `atst.transport.pool.pool_settings_from_config`（唯一翻译点） |

## 9. 门禁与防回潮

- **文档-代码一致性**（`tests/architecture/test_doc_code_consistency.py`）：活文档里的
  `atst.*` 引用、`docs/...md` 引用、README 与 `docs/ARCHITECTURE.md`/`docs/api/` 宣称的规模数字
  （命令账本 / 解析器 / 配置段 / 根级白名单 / 服务面方法数 / HTTP 源与契约下界）一律钉回运行期真相源；
  历史快照（`docs/archive/`、`docs/adr/`、`DESIGN.md`）记录当时语境，不参与门禁。
- **单内核守卫**（`tests/architecture/test_single_kernel_guards.py`）：已删模块/符号不可再现、
  `atst.runtime.__all__` 仅内核、runtime 不再引用已删分层、`Client` 执行面类型为 `DirectProviderExecutor`。
- **命名空间守卫**（`tests/architecture/test_namespace_layout.py`）：根级白名单 11 项、旧模块路径不可导入。
- **本地门禁**：`pytest`（全量离线）+ `ruff check` + `mypy atst/`（CI 参数，0 项）+ 覆盖率
  （阈值单一事实源 `pyproject.toml [tool.coverage.report] fail_under`）。

## 10. 历史重构台账

v13 clean break 之后的逐轮重构（断链清偿、命名债、配置面接线、技术债合成）全部记在
[`docs/archive/plans/`](archive/plans/) 的 `REFACTOR_PLAN_V17_CLOSURE.md`、
`REFACTOR_PLAN_V18_RESTRUCTURE.md`、`REFACTOR_PLAN_V19_RESTRUCTURE.md`、
`REFACTOR_PLAN_V20_DEBT_SYNTHESIS.md` 里；就绪快照（V1–V4）在
[`docs/archive/`](archive/)。本文只描述代码现状，不重复历史账。

## 11. 开发环境（重要）

- 本机裸 `python` 是坏掉的 WindowsApps stub（`python -c` 静默返回，什么都不执行）。
- 仓库里的 `.venv` 由 uv 创建，它**不是**一个自足的入口面：`Scripts/` 里只有 `pythonw.exe`
  （没有 `python.exe`），`pytest.exe`/`mypy.exe` 两个 trampoline 脚本在 Windows 上以
  「uv trampoline failed to canonicalize script path」失败，`ruff.exe` 正常。
- 可用路线只有两条：`uv run …`（PATH 加 `~/.local/bin`），或直接调用 uv 管理的解释器跑
  `python -m pytest` / `python -m mypy`，并把 `.venv/Lib/site-packages` 与仓库根一起放进 `PYTHONPATH`。
- 跑全量测试必须设 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 并把 `--basetemp` 指到 OS 临时目录，
  否则 turn 内累积删除会撞 safe-delete 批量护栏被 `SystemExit` 误判为测试失败。
