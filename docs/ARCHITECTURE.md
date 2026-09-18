# tstdx 当前架构事实（ARCHITECTURE）

> 快照日期：2026-09-19 · 对应 v16 Phase 2b + V17 Phase 3A/3B（单内核收口、typed 契约对齐内核）
> 本文只描述**代码现状**；演进计划见 [REFACTOR_PLAN_V17_CLOSURE.md](REFACTOR_PLAN_V17_CLOSURE.md)。
> 历史方案（docs/v1–v16）所述 L1/L2 缓存、UnifiedQuoteAPI 门面、5 级降级路由、
> sources/sinks 层，以及 v14 信封运行时（`runtime/{runtime,gateway,request,response,typed,stream}.py`、
> `execution/`、`provider/`）均已物理删除，不再是事实。

## 1. 项目定位

通达信（TDX）行情数据通用协议基础设施：协议编解码 → 多源 Provider → 统一查询内核 →
CLI/HTTP/WS/MCP 服务面。同步交付物为 `tstdx` Python 包（协议实现 100% 自有）。

## 2. 唯一主执行链

```
CLI / HTTP(runtime_http) / WS(runtime_ws) / MCP(integration/mcp)
        │  （四个服务面全部只翻译，委托 Client）
        ▼
tstdx.Client / AsyncClient（client_api.py，唯一业务入口，172 capabilities）
        │  QuerySpec（query.py：capability+symbols+provider+currentness…）
        ▼
UnifiedRuntime（runtime/kernel.py，零缓存）
        │  QueryPlanner.compile(spec) → QueryPlan（单 Provider/单 Channel）
        ▼
DirectProviderExecutor（runtime/executor.py）
        │  DIRECT_BINDINGS[(provider, channel, capability)] → 精确 executor 方法
        ▼
providers/ 注册表（Provider/Channel/Capability 单一事实源）
   ├─ tdx        → protocol/ + client/ + transport/（85 命令、61 解析器、连接池）
   ├─ tencent/sina/eastmoney/baidu → web/（45+ HTTP 源）
   ├─ local_vipdoc → reader/（本地 .day/.lc1 二进制）
   └─ derived    → 显式聚合能力
        │  QueryResult（result.py：data + meta.provenance 溯源）
        ▼
跨 Provider 回退：仅限显式 FallbackPolicy → ProviderOrchestrator（runtime/orchestration.py，唯一通道）
流式：StreamSpec/StreamPlanner（stream_contract.py）→ StatefulQuoteStream（streaming/）
```

**核心不变量**（由 `tests/provider_isolation/`、`tests/runtime/` 锁定）：
- 零缓存：每次请求直达绑定 Provider，无结果/负/提升缓存，无请求合并。
- provider-first：一个 Plan 永不私选第二 Provider；provenance 校验失败即抛。
- 单内核：执行只发生在 `UnifiedRuntime → DirectProviderExecutor`。

## 3. 分层与包职责

| 层 | 模块 | 状态 |
|---|---|---|
| 协议层（冻结） | `codec/`（帧/变长数/字符集）、`protocol/`（命令账本+三级解析）、`transport/`（池/心跳/测速）、`client/`（`core.py` 共享纯协议 SSOT + `sync.py`/`async_.py` TdxClient）、`charset/` | 独立完备 |
| 数据源层 | `providers/`（静态注册表）、`web/`、`reader/`、`profile/`（DataProfile 复权/周期口径） | 活 |
| 契约层（无执行） | `query.py`、`result.py`、`batch.py`、`typed_query.py`、`stream_contract.py`、`errors.py`、`error_envelope.py`、`deprecation.py`、`catalog/`（capability 目录与调用校验、Provider channel→adapter 绑定表、Provider 隔离契约/守卫/一致性审计） | 活 |
| 内核层 | `runtime/`（`kernel.py` 唯一内核、`executor.py` 精确绑定执行、`orchestration.py` 显式跨源编排、`audit.py` 启动三方对账、`identity.py`/`provenance.py` 执行身份与溯源守卫） | 活 |
| 服务面层 | `cli/`、`integration/`（runtime_http/ws/tasks/mcp + serialization）、`output/`（DataFrame/Parquet/DuckDB）、`sink/` | 活，全部 Client-backed |
| 类型化糖衣 | `typed_query.py`（CapabilityQuery + Domain Record）、`domain/`（records/symbol/日历） | 全量接通：`Client.typed` / `AsyncClient.typed`，字段名与内核方法签名一一对应 |
| 基础设施 | `config/`、`security/`、`observability/`、`feedback/` | 活 |
| **待归位** | `client_api.py`（唯一入口，经决策保留根级以最大化可发现性）、`trade/`（实验模块） | Phase 3C 后仅剩这两项，见 V17 方案 |

## 4. 断链清偿状态（V17）

1. **F-1 第二执行接缝 —— 已消灭（2026-09-19）**：v14 信封层（`runtime/{runtime,gateway,
   bootstrap,request,response,typed,stream,context}.py`）、`execution/` DAG 与 `provider/`
   v14 router/adapters 已物理删除；`Client` 是唯一业务入口，`UnifiedRuntime` 是唯一执行内核，
   测试通过 `UnifiedRuntime(executor=...)` 注入假执行面。
2. **F-2 registry 三件套 —— 已消灭（2026-09-19）**：`executor_bindings.py`、
   `executor_binding_registry.py`、`executor_registry.py` 及其 4 个契约测试删除，
   `DIRECT_BINDINGS` 恢复为唯一三元组事实源。
3. **F-9 孤儿模块 —— 已消灭（Phase 3C）**：`tstdx/freshness.py`（427 行）、`tstdx/health.py`
   （256 行）全仓零引用（含测试与脚本），`tstdx/failure.py` 仅被自身测试引用；三者随
   Phase 3C 删除。新鲜度/健康/失败决策若重来，必须挂到内核执行面上并有消费者，不再先写契约。
4. **F-10 文档面失真 —— 已清偿（Phase 4，2026-09-19）**：`from tstdx import TdxClient`
   （14 处，含 `ops/smoke_30d.py` 的运行期 ImportError）、v14 信封运行时用法
   （`create_runtime`/`RuntimeGateway`/`QueryRequest`/语义缓存）、`DataSourceRouter`
   五级降级、失效的落地 URI（`output://`/`parquet://`/`duckdb://…?table=`）、
   幻影符号（`read_lc1_file`/`read_lc5_file`/`adjust_bars`/`domain.records` 旧类名清单）、
   以及 5 组动作数（CLI 32→31、HTTP ~40→10、MCP 12→9、capability 167→172、
   `integration.http_server/ws_server/mcp_server` → `runtime_*`/`mcp`）全部按运行期事实重写。
5. **F-11 命名债残留 —— 待办**：`tstdx/web/_facade_mixin_*.py`（11 文件）与
   `tstdx/web/facade.py` 仍沿用已删除的"门面层"命名；语义是 Web Provider 会话分组，
   契约无影响，留作纯改名小 PR。
6. **防回潮守卫**：`tests/architecture/test_single_kernel_guards.py`（已删模块/符号不可再现、
   `tstdx.runtime.__all__` 仅内核、runtime 包不再引用已删分层、Client 执行面类型为
   `DirectProviderExecutor`）；`tests/architecture/test_namespace_layout.py`（根级白名单 11 项、
   旧模块路径不可导入）；`tests/architecture/test_doc_code_consistency.py`（活文档 import 可解析、
   事实型文档 `tstdx.*` 路径可解析、`__all__` ⇔ `_LAZY`、README 数字 == 运行期事实）。

## 5. 契约与真相源

| 事实 | 唯一真相源 |
|---|---|
| Provider/Channel/Capability | `tstdx/providers/__init__.py`（`PROVIDERS` 注册表） |
| 可执行绑定 | `runtime/executor.py`（`DIRECT_BINDINGS`，启动时 `audit_runtime()` 三方对账） |
| capability 语义/参数校验 | `query.py`（`QuerySpec`）+ `catalog/capability.py`（`validate_call`） |
| 协议命令账本 | `protocol/` YAML 规范 + codegen + golden_audit |
| 公开导出面 | `tstdx/__init__.py::__all__`（懒加载 `_LAZY`） |

## 6. 开发环境（重要）

- 本机裸 `python` 是坏掉的 WindowsApps stub：一律用 `.venv/Scripts/python.exe`
  或 `uv run`（PATH 加 `~/.local/bin`）。
- 门禁：`pytest`（全量离线）+ `ruff check` + `mypy tstdx/` + 覆盖率（V17 起按有效代码重校准）。
