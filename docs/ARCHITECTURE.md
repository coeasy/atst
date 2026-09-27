# atst 当前架构事实（ARCHITECTURE）

> 快照日期：2026-09-27 · 对应 V19 第 29 轮（流式 `stop` 纳入 G41 停机名单并改为"先排空再原样抛回
> 取消"、WS 协议层失败也挂同一个错误信封、四面 query 入口拒绝 `kwargs` 里的路由字段、
> `quotes_batch` 的 `requested`/`errors`/`partial` 真源补齐、撤单后成交判死、腾讯枚举截断发告警）
> （第 28 轮：停机路径有界化、流式层入册、包模块名册门禁）
> + V20 技术债清偿（hardening 合并回基类、executor 分派规则化、`web/` 按 Provider 归组；
> 见 [REFACTOR_PLAN_V20_DEBT_SYNTHESIS.md](REFACTOR_PLAN_V20_DEBT_SYNTHESIS.md) §8）
> 本文只描述**代码现状**；演进计划见 [REFACTOR_PLAN_V18_RESTRUCTURE.md](REFACTOR_PLAN_V18_RESTRUCTURE.md)，
> 已闭合的 V17 收口案连同它的逐轮读数留在
> [REFACTOR_PLAN_V17_CLOSURE.md](REFACTOR_PLAN_V17_CLOSURE.md) 里。
> 历史方案（docs/v1–v16）所述 L1/L2 缓存、UnifiedQuoteAPI 门面、5 级降级路由、
> sources/sinks 层，以及 v14 信封运行时（`runtime/{runtime,gateway,request,response,typed,stream}.py`、
> `execution/`、`provider/`）均已物理删除，不再是事实。

## 1. 项目定位

通达信（TDX）行情数据通用协议基础设施：协议编解码 → 多源 Provider → 统一查询内核 →
CLI/HTTP/WS/MCP 服务面。同步交付物为 `atst` Python 包（协议实现 100% 自有）。

## 2. 唯一主执行链

```
CLI / HTTP(runtime_http) / WS(runtime_ws) / MCP(integration/mcp)
        │  （四个服务面全部只翻译，委托 Client）
        ▼
atst.Client / AsyncClient（client/api.py，唯一业务入口，172 capabilities）
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
   ├─ tencent/sina/eastmoney/baidu → web/<provider>/（45+ HTTP 源，按 Provider 归组）
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
- 配置面即执行面契约：`Config` 里每个键都被内核读取并改变行为，配置→传输只有
  `atst.transport.pool.pool_settings_from_config` 一个翻译点，无第二读者
  （`tests/runtime/test_kernel_config_wiring.py`、
  `tests/transport/test_pool_settings_from_config_contract.py`）。

## 3. 分层与包职责

| 层 | 模块 | 状态 |
|---|---|---|
| 协议层（冻结） | `codec/`（帧/变长数/字符集）、`protocol/`（命令账本+三级解析）、`transport/`（池/心跳/测速）、`client/`（`api.py` 唯一业务入口 Client/AsyncClient + `core.py` 共享纯协议 SSOT + `sync.py`/`async_.py` TdxClient + `factory.py` 按市场族造客户端的 `get_client`）、`charset/` | 独立完备 |
| 数据源层 | `providers/`（静态注册表）、`web/`（六个 Provider 子包 `tencent`/`sina`/`eastmoney`/`baidu`/`jsl`/`boc` 各承载一家适配器，跨 Provider 的域模块留包根）、`reader/`、`profile/`（DataProfile 复权/周期口径） | 活 |
| 契约层（无执行） | `query.py`、`result.py`、`batch.py`、`typed_query.py`、`stream_contract.py`、`errors.py`、`error_envelope.py`、`diagnostics.py`（运行期告警码与"未证实"登记簿）、`catalog/`（capability 目录与调用校验、Provider channel→adapter 绑定表、Provider 隔离契约/守卫/一致性审计） | 活 |
| 内核层 | `runtime/`（`kernel.py` 唯一内核、`executor.py` 精确绑定执行、`orchestration.py` 显式跨源编排、`audit.py` 启动三方对账、`identity.py`/`provenance.py` 执行身份与溯源守卫、`freshness.py` 当期性证据裁决——`currentness` 声明无人读的 F-44 由它收口） | 活 |
| 流式层 | `streaming/`（`base.py` 传输与订阅底座、`engine.py` 引擎、`state.py` 状态格、`stateful.py` `StatefulQuoteStream`、`push.py` 推送面） | 活，入参契约在根级 `atst.stream_contract` |
| 服务面层 | `cli/`、`integration/`（`runtime_http.py` HTTP 面、`runtime_ws.py` JSON-RPC 分派、`runtime_ws_server.py` WS 服务器、`serialization.py` 结果序列化、`wire_fields.py` 三面入参白名单、`integration/mcp/` MCP 面）、`output/`（DataFrame/Parquet/DuckDB）、`sink/` | 活，全部 Client-backed |
| 类型化糖衣 | `typed_query.py`（CapabilityQuery + Domain Record）、`domain/`（records/symbol/日历） | 全量接通：`Client.typed` / `AsyncClient.typed`，字段名与内核方法签名一一对应 |
| 基础设施 | `config/`、`observability/`、`feedback/` | 活 |
| 实验模块 | `trade/`（自设模拟红线，未进 README 能力账主链） | 唯一剩余待裁定项，见 V17 决策点 3 |

> 本表此前还写着 `security/` 一层"活"：`atst/security/` 已随 `fcf8e92`（撤回一项过期的
> 安全承诺）一并删除——它删除前也只有一个 docstring 与 `__all__ = []`，包内从来没有独立的安全
> 目录。错误越界时的凭据脱敏由 `atst/error_envelope.py` 的按关键字过滤承担，
> 安全口径见 [SECURITY.md](../SECURITY.md)。

## 4. 断链清偿状态（V17）

1. **F-1 第二执行接缝 —— 已消灭（2026-09-19）**：v14 信封层（`runtime/{runtime,gateway,
   bootstrap,request,response,typed,stream,context}.py`）、`execution/` DAG 与 `provider/`
   v14 router/adapters 已物理删除；`Client` 是唯一业务入口，`UnifiedRuntime` 是唯一执行内核，
   测试通过 `UnifiedRuntime(executor=...)` 注入假执行面。
2. **F-2 registry 三件套 —— 已消灭（2026-09-19）**：`executor_bindings.py`、
   `executor_binding_registry.py`、`executor_registry.py` 及其 4 个契约测试删除，
   `DIRECT_BINDINGS` 恢复为唯一三元组事实源。
3. **F-9 孤儿模块 —— 已消灭（Phase 3C）**：`atst/freshness.py`（427 行）、`atst/health.py`
   （256 行）全仓零引用（含测试与脚本），`atst/failure.py` 仅被自身测试引用；三者随
   Phase 3C 删除。新鲜度/健康/失败决策若重来，必须挂到内核执行面上并有消费者，不再先写契约。
4. **F-10 文档面失真 —— 已清偿（Phase 4，2026-09-19）**：`from atst import TdxClient`
   （14 处，含 `ops/smoke_30d.py` 的运行期 ImportError）、v14 信封运行时用法
   （`create_runtime`/`RuntimeGateway`/`QueryRequest`/语义缓存）、`DataSourceRouter`
   五级降级、失效的落地 URI（`output://`/`parquet://`/`duckdb://…?table=`）、
   幻影符号（`read_lc1_file`/`read_lc5_file`/`adjust_bars`/`domain.records` 旧类名清单）、
   以及 5 组动作数（CLI 32→31、HTTP ~40→10、MCP 12→9、capability 167→172、
   `integration.http_server/ws_server/mcp_server` → `runtime_*`/`mcp`）全部按运行期事实重写。
5. **F-11 命名债 —— 已清偿（Phase 5，2026-09-19）**：`atst/web/facade.py` → `atst/web/session.py`，
   11 个 `atst/web/_facade_mixin_*.py` → `_session_*.py`（clean-break，不留别名或再导出 shim）。
   改名动因：这些模块承载的是 `WebQuoteSession`（Web Provider 会话组合层）的按域方法组，
   而"门面"这个名字指向的 `UnifiedQuoteAPI` 已随 v16 Phase 2 物理删除，继续叫 facade 会让
   读者以为存在跨源聚合门面。运行期引用同步更正 47 个文件（含 `catalog/provider_bindings.py`
   的绑定字符串 `("atst.web.session", "WebQuoteSession")` 与 `atst/web/__init__.py` 的
   `_LAZY` 子模块名），`docs/api/README.md` 的事实路径同步。
6. **F-12 死守卫 —— 已修（Phase 5，2026-09-19）**：`Prober.only_offline_hours()` 比较
   从未存在的 `SessionState.IN_SESSION`，未打桩调用必抛 `AttributeError`，"盘中不探测
   主站"的保护实际为死代码（测试全打桩故全绿）。现按 `call_auction/continuous` 判定，
   并由 `tests/protocol/test_prober_offline_guard.py` 逐时段回归。同批把 `mypy atst/`
   从 47 项压到 **0**（含删除零消费者的缓存时代残留 `RuntimeCacheIdentity`）。
7. **F-13/F-16 配置面未接线且装饰化 —— 已清偿（Phase 6，2026-09-19）**：`UnifiedRuntime`
   现在是配置面唯一读者（`Client()` 缺省经 `atst.config.get_config()` 取进程级惰性
   六源合并单例，显式入参仍优先），`core.default_provider`/`core.timeout`/
   `core.heartbeat_interval`/`core.max_retries`/`core.vipdoc_root`/`hosts.servers`/
   `hosts.slots_per_host`/`rate_limit.*`/`security.use_tls` 逐个贯通到
   `DirectProviderExecutor → TdxClient → ConnectionPool`（配置→传输的**唯一**翻译点
   `transport/pool.py::pool_settings_from_config`），`web.*` 贯通到 `WebQuoteClient`。
   12 段收缩为 5 段：`cache`/`output`/`profile`/`sources`/`observability`/
   `compatibility`/`feedback` 七段的 dataclass 与再导出物理删除，loader 对未知段
   fail-closed。同时删除第二个配置读者 `ConnectionPool.from_config`（生产链从未引用，
   且它读的 `rate_*` 键名与 `RateLimitConfig` 字段从不重合 ⇒ 配置值被静默丢弃）。
   口径与取舍见 [ADR-016](adr/ADR-016-config-surface-covers-execution-only.md)，
   用户面见 [docs/configuration.md](configuration.md)，回归锁见
   `tests/runtime/test_kernel_config_wiring.py`。
8. **F-15 门禁基线 —— 格式/类型/覆盖率三项均绿**：`ruff format --check` 现存
   **65 文件待重排已清零**（一次纯格式提交，重排前后 `ast.dump()` 逐个比对无差异），
   dev 依赖把 `ruff==0.15.2` / `mypy==2.3.1` 钉死，格式/类型门禁不再随工具版本漂移；
   `mypy atst/` 为 **0**（Phase 5 已归零）。覆盖率：**离线全量（CI 等价范围
   `-m "not network"`）已越过阈值 ⇒ 本地为绿**；本条**刻意不写百分比**——本机读数会随
   每一轮改动漂移，把它抄进"只描述代码现状"的本文就是下一次失真的来源，逐轮实测数字
   一律记在 [REFACTOR_PLAN_V17_CLOSURE.md](REFACTOR_PLAN_V17_CLOSURE.md) 的步骤日志里。
   阈值数字收敛为单一事实源
   `pyproject.toml [tool.coverage.report] fail_under`（Makefile/CI 的
   `--cov-fail-under` 副本删除，由 `tests/compatibility/test_local_gate_contract.py`
   与 `test_ci_workflow_contracts.py` 锁定）；**阈值本身一次都没有下调**。
   重钉需要 CI 环境（ubuntu+py3.11）的实测数字，本机 Windows 数字不作为依据。
9. **防回潮守卫**：`tests/architecture/test_single_kernel_guards.py`（已删模块/符号不可再现、
   `atst.runtime.__all__` 仅内核、runtime 包不再引用已删分层、Client 执行面类型为
   `DirectProviderExecutor`）；`tests/architecture/test_namespace_layout.py`（根级白名单 10 项、
   旧模块路径不可导入）；`tests/architecture/test_doc_code_consistency.py`（活文档 import 可解析、
   事实型文档 `atst.*` 路径可解析、`__all__` ⇔ `_LAZY`、README 数字 == 运行期事实、
   README、本文与 `docs/api/` 等事实文档宣称的规模数字 == 命令账本 / 解析器表 / 配置
   schema / 根目录实际文件数 / 服务面方法与 Record 名单）。

## 5. 契约与真相源

| 事实 | 唯一真相源 |
|---|---|
| Provider/Channel/Capability | `atst/providers/__init__.py`（`PROVIDERS` 注册表） |
| 可执行绑定 | `runtime/executor.py`（`DIRECT_BINDINGS`，启动时 `audit_runtime()` 三方对账） |
| capability 语义/参数校验 | `query.py`（`QuerySpec`）+ `catalog/capability.py`（`validate_call`） |
| 协议命令账本 | `protocol/` YAML 规范 + codegen + golden_audit |
| 公开导出面 | `atst/__init__.py::__all__`（懒加载 `_LAZY`） |
| 配置结构 | `atst.config.schema`（5 段，全部由内核读取；用户面 `docs/configuration.md`，取舍见 ADR-016） |
| 配置 → 传输层参数 | `atst.transport.pool.pool_settings_from_config`（唯一翻译点） |

## 6. 开发环境（重要）

- 本机裸 `python` 是坏掉的 WindowsApps stub（`python -c` 静默返回，什么都不执行）。
- 仓库里的 `.venv` 由 uv 创建，它**不是**一个自足的入口面：`Scripts/` 里只有
  `pythonw.exe`（没有 `python.exe`），`pytest.exe`/`mypy.exe` 两个 trampoline 脚本在
  Windows 上以「uv trampoline failed to canonicalize script path」失败，
  `ruff.exe` 正常。把这些路径抄进任何脚本或文档前，先按上面的名单核一遍。
- 可用路线只有两条：`uv run …`（PATH 加 `~/.local/bin`），或直接调用 uv 管理的解释器
  跑 `python -m pytest` / `python -m mypy`，并把 `.venv/Lib/site-packages` 与仓库根
  一起放进 `PYTHONPATH`。
- 门禁：`pytest`（全量离线）+ `ruff check` + `mypy atst/` + 覆盖率（阈值单源：
  `pyproject.toml [tool.coverage.report] fail_under`，Makefile/CI 不再各传
  `--cov-fail-under`）。
