# runtime-contract：内核与契约层审查（atst/runtime + atst/query|result|batch|typed_query + atst/providers）

审查范围：`atst/runtime/`（kernel、executor、orchestration、autoselect、bindings、execute_*、composed、audit*、identity、provenance、freshness、capability_contract、provider_guard）、`atst/query.py`、`atst/result.py`、`atst/batch.py`、`atst/typed_query.py`、`atst/providers/`（registry、contract、各声明模块、derived、`__init__`）。方法：逐文件读实现 + 针对性运行验证（死声明、auto provenance 两项已实跑复现）。日期 2026-11-22，包版本 v2.1.0。

## 1. 结论摘要

v13「单内核、零缓存、provider-first」的骨架基本立住了：跨源只发生在显式 orchestration、结果统一 `Provenance.direct`、`strict`/空结果/区间截断都有判据。但**三方对账并不闭环**——`capability_audit` 的双向对账是恒真式，`audit_direct_bindings` 不在启动审计里，`executor_name` 是否真有方法无人校验；**composed 内层查询绕过单内核**，健康反馈、溯源校验、deadline 预算在内层全部失效；**derived/builtin 以自有身份代理 sina**，provenance 报 `derived` 不报真实出数源，provider 身份作为信任边界的自述在这 100+ 能力上不成立。整体置信：高（关键结论均有代码行级证据，其中两条经运行复现）。

## 2. 发现清单

| # | 严重度 | 类型 | 位置 | 一句话问题 |
|---|--------|------|------|------------|
| A1 | 高 | 架构 | atst/runtime/capability_audit.py:44-62 | 三方对账是恒真式：`declared` 与 `executable` 同源生成，unbound/ghost 永远为空；启动审计不含 `audit_direct_bindings`，`executor_name` 无方法存在性校验 |
| A2 | 高 | 架构 | atst/runtime/composed.py:92 | composed 内层查询直连 `executor.execute(QueryPlanner().compile(...))`，绕过 UnifiedRuntime：无健康反馈、无溯源校验、内层各自重置 5s deadline、忽略配置的 default_provider/auto |
| A3 | 中 | 架构 | atst/providers/derived.py:1-20 | derived/builtin 是 sina 代理（100+ 能力 `source="sina"`），provenance/注册表却以 `derived` 身份示人，provider 信任边界失守 |
| A4 | 中 | 架构 | atst/client/api.py:380-383 | composed 能力 `provider=` 语义二义（路由 vs 原始 K 线腿），只靠 Client.call 一处特判翻译；直构造 QuerySpec 的调用方没有这层翻译 |
| A5 | 中 | 架构 | atst/query.py:400-545 | query.py 749 行职责过载；`normalized()` 名为归一实为校验+语义改写（quotes→hk_quotes/us_quotes、bars 市场路由） |
| A6 | 中 | 架构 | atst/query.py:64-77 | query 契约层硬编码 provider 事实（canonical channel 表、非 TDX bars 偏好），provider-first 被倒置 |
| A7 | 中 | 架构 | atst/query.py:675,793 | query ↔ runtime 循环依赖，靠函数级 import 规避；bindings 反向依赖 query 私有函数 |
| A8 | 中 | 架构 | atst/runtime/autoselect.py:63-95 | 健康反馈是进程级 TTL 缓存，与零缓存自述有张力；成败双重记账；内核级 auto 与 Client 级 auto 行为分叉 |
| A9 | 中 | 架构 | atst/providers/tdx.py:72-74 | tdx extended/goods/mac 通道声明的 `bars`/`quotes` 是规划期必拒的死能力（已运行复现） |
| A10 | 中 | 架构 | atst/runtime/kernel.py:124-161 | `quotes_batch` 在 kernel 与 Client 两处各写一份，语义已分叉（auto 链、并发、容错） |
| B1 | 中 | 细节 | atst/query.py:202-203,392-397 | `_OPTIONS_CACHE` 模块级缓存明文 options（含 cookie 等凭证），浅拷贝共享嵌套结构 |
| B2 | 中 | 细节 | atst/runtime/orchestration.py:169-172 | `except Exception` 不分类：调用方参数错误也触发跨源重试，最终 AllSourcesExhausted 掩盖真实错误 |
| B3 | 中 | 细节 | atst/runtime/kernel.py:145 | `quotes_batch` 的 `normalize_symbol` 在 try 外，一个非法符号中断整批 |
| B4 | 中 | 细节 | atst/runtime/composed.py:74-78 | composed 本地 vipdoc 直读 `DayBarReader`，绕过 provider 注册表，窗口切片逻辑与 `_local_bars` 重复 |
| B5 | 中 | 细节 | atst/providers/derived.py:143-1105 | 重复绑定表：SPEC.capabilities 与 BINDINGS 双处声明；derived.py 用同一 6 行模板手抄 110 遍 |
| B6 | 低 | 细节 | atst/runtime/executor.py:204 与 kernel.py:56 | 每次 Client 初始化 `audit_runtime()` 跑两遍（UnifiedRuntime 与 DirectProviderExecutor 各一次） |
| B7 | 低 | 细节 | atst/query.py:434-437,451-454 | `except Exception: markets = set()` 吞掉 parse_symbol 的真实错误 |
| B8 | 低 | 细节 | atst/typed_query.py:66-68,150-155 | futures_* 能力映射到 `BondRecord`；`call_payload_from_typed` 把空默认值字段全量下发 |
| B9 | 低 | 细节 | atst/batch.py:137-141 | `partial` 只跟 `errors` 联动，`missing`（空结果）不进 errors，partial=False 而 success=False |
| B10 | 低 | 细节 | atst/runtime/freshness.py:44-52 | currentness 只防 `channel.local`，web 非 live 通道的 `business` 口径不设防（注释已自认） |

## 3. 证据与说明

### A1【高·架构】三方对账不闭环

`capability_audit.audit_capability_bindings` 自称"双向对账"（atst/runtime/capability_audit.py:32-36："The Provider registry is an independently authored declaration, so it is reconciled against the executor bindings in **both** directions"），但 `executable` 来自 `DIRECT_BINDINGS`，而 `DIRECT_BINDINGS` 就是从同一个注册表三元组生成的（atst/runtime/bindings.py:73-76）：

```python
DIRECT_BINDINGS = tuple(
    DirectBinding(provider, channel, capability, _executor_for(provider, channel, capability))
    for provider, channel, capability in _registry_triples()
)
```

因此 `unbound = declared - executable` 与 `ghost = executable - declared`（capability_audit.py:54-62）是恒空集，这两条 raise 是死代码，唯一有效检查只剩 `migrated ⊆ executable`。

真正能抓"统一可达却无执行元数据"的 `audit_direct_bindings`（bindings.py:101-124，unroutable 即 raise）**不在启动审计里**：`audit_runtime()` 只调 `audit_capability_bindings` 和 `audit_provider_registry`（audit.py:31-36），`audit_direct_bindings` 只在测试里被调用（tests/runtime/test_v13_architecture_alignment.py:43）。另外 `fn = getattr(self, binding.executor_name)`（executor.py:223）没有任何前置校验 `_executor_for` 返回的方法名真实存在——拼错名字只会在运行期炸裸 `AttributeError`，且发生在 warning_sink 保护区之外。

**改法**：`audit_runtime()` 纳入 `audit_direct_bindings()`；对账改为三方独立集合（注册表声明 / MIGRATED_BINDINGS / executor 方法名集合，用 `hasattr(DirectProviderExecutor, name)` 现算），两两求差。

### A2【高·架构】composed 内层查询绕过单内核

`_adjusted_raw_bars`（composed.py:92）、`_adjusted_events`（composed.py:127-128）、`daily_enriched` 的估值查询（execute_composed.py:237-238）都是：

```python
rows = executor.execute(QueryPlanner().compile(spec)).data
```

这里 `executor` 是 `DirectProviderExecutor`，不是 `UnifiedRuntime`。后果：① 内层成败不进 `provider_health`（kernel.py:99-104 的记账只在 UnifiedRuntime.execute）；② 内层结果不经过 `validate_runtime_provenance`（kernel.py:105）；③ 每次内层调用 `QueryPlanner()` 新建，`default_provider` 取注册表缺省而非运行时配置，auto 偏好/TTL 全丢；④ 每个 `QuerySpec.build` 缺省 `deadline_ms=5000`（query.py:341），内层各自重置 5 秒预算，外层 `plan.budget` 对 composed 三段组合不设限——一次 `adjusted_bars`（原始 K 线 + 事件翻页最多 10 页×2 + 窗口延伸倍数增长）实际耗时可达外层 deadline 的数倍；⑤ 内层查询的 provenance 被丢弃，最终结果只盖外层 `derived` 的章。

**改法**：composed 编排上移到 UnifiedRuntime（或给 executor 注入 runtime 句柄），内层走 `runtime.execute` 并把外层 budget 传入内层 spec 的 `deadline_ms`。

### A3【中·架构】derived/builtin 代理 sina，身份不可见

derived.py 声明 110+ 能力，全部 `backend="web_session", source="sina"`（如 derived.py:146-153 `announcements`），builtin.py 同构（builtin.py:36-52）。执行时 `WebQuoteSession(meta.source or "sina", ...)`（execute_capability.py:41-45）真实出数源是 sina，但 `Provenance.direct` 只记 `plan.provider`（result.py:88-96），wire 上报 `provider="derived"`。providers/__init__.py:9-12 自述"Provider 身份是信任边界：选 tdx 绝不会静默执行腾讯/东财/新浪"，而选 derived 必然执行 sina——边界在聚合 Provider 上被自己的实现推翻，且调用方无从得知量纲/口径属于哪家。模块 docstring 自认"能力名里的 provider 指'取原始数据的那条腿'"（derived.py:5），但这句话没有落到 Provenance 字段上。

**改法**：Provenance 增加 `upstream` 字段（从 `meta.source` 透传），或 derived 能力在结果 meta.warnings/诊断面披露真实 source；至少文档面要把这条边界写清。

### A4【中·架构】composed 能力 provider= 语义二义

`QuerySpec.provider` 对所有能力是"谁来执行"；对 composed 三能力，执行者恒为 derived，`options.kwargs.provider` 却指"原始 K 线那条腿"（execute_composed.py:54 `raw_provider`，缺省 tdx；execute_composed.py:183 `bar_provider`）。这个二义只靠 Client.call 一处特判翻译（api.py:378-383）：

```python
if provider and cap in COMPOSED_CAPABILITIES:
    kwargs["provider"] = provider
    selected = default_provider_for(cap)
```

注释自己承认"过去这个值被当成路由字段递进 planner，于是 `Client.adjusted_bars(..., provider="tdx")` 在规划期就 E1010"。绕过 Client 直接构造 `QuerySpec.build("adjusted_bars", provider="tdx", ...)` 的调用方（服务面、脚本）仍会撞上同一个 E1010。同一能力还有第三个旋钮 `event_source`（execute_composed.py:55，缺省 eastmoney）——一条查询三个"源"参数、两套缺省（tdx/eastmoney），语义负担全压在调用方。

**改法**：把 raw-provider/event-source 提升为 QuerySpec 显式字段（或在 normalized() 里对 COMPOSED_CAPABILITIES 做与 Client 相同的归位），不要只在一层门面特判。

### A5【中·架构】query.py 职责过载 + normalized() 夹带语义改写

query.py 749 行装了 8 类职责：CurrentnessMode、ExecutionBudget、QuerySpec（含 options 白名单/别名折叠/市场感知翻译）、QueryFingerprint（含凭证脱敏）、QueryPlanner（channel 选择、auto 解析、migrated 参数预绑）、canonical channel 表、options 缓存、日期区间校验。最重的是 `normalized()`（query.py:400-545）：名为归一，实际做三件越权的事——`quotes` 全hk/us 时把 capability 改写成 `hk_quotes`/`us_quotes`（query.py:430-446）、`bars` 港美股时改写缺省 provider（query.py:448-466）、`_market_routed` 只记布尔不改 spec 字段，选择依据只活在函数栈里。调用方拿到 normalized spec 后无法从数据本身看出 capability 被换过。749 行里真正的"契约"只有 ~200 行，其余是策略与规划。

**改法**：拆 `spec.py`（纯契约）/ `planning.py`（channel 选择与 auto）/ `fingerprint.py`；市场感知翻译移到 planner 的显式阶段并写回 spec（如 `translated_from` 字段），让改写可见。

### A6【中·架构】query 层硬编码 provider 事实

`_CANONICAL_UNIFIED_CHANNELS`（query.py:64-77）与 `_NON_TDX_BAR_PROVIDERS = ("eastmoney", "tencent")`（query.py:62）把"哪个 provider 的 bars 走哪条 channel""港美股缺省用谁"写在契约层。registry.py:4-8 明说 ChannelSpec"不做市场声明：市场正确性 fail-close 在 Symbol.tdx_market，第二套市场词汇没人执行"——同一逻辑也适用于这里：provider 事实的第二持有者没人对账。bindings.py:88 还 import 这个私有函数 `_canonical_unified_channel` 来判"统一可达"，把私有契约变成了跨模块公共依赖。

**改法**：canonical channel 进 ChannelSpec（`unified_for: frozenset[str]`），市场缺省偏好进 Provider 声明模块（现有 DEFAULT_CAPABILITIES 机制），query 层只读注册表。

### A7【中·架构】循环依赖

模块级依赖：runtime/executor.py:18 `from ..query import QueryPlan`、runtime/kernel.py:26 同、runtime/bindings.py:88 函数级 `from ..query import _canonical_unified_channel`；反向 query.py:675 `from .runtime.autoselect import AUTO_PROVIDER`、query.py:793 `from .runtime.capability_contract import validate_call` 全靠函数级 import 避免加载环。契约层与运行时层互为依赖，"四面只翻译不执行"的分层在 import 图上不成立。autoselect.py:1-15 的模块 docstring 同时承担策略说明与 Client 文档，也是这层耦合的症状。

**改法**：auto 解析常量（AUTO_PROVIDER）与候选排序下沉到 providers 或独立 `selection.py`；validate_call 的规划期预检从 query.py 移除（执行面已有同一道闸，execute_capability.py:22-25）。

### A8【中·架构】健康反馈与零缓存的张力

`ProviderHealthStore` 是进程级、TTL 失效的失败缓存（autoselect.py:63-95），模块 docstring 自辩"只存失败不存行情"（autoselect.py:20-22）——但它在 `auto` 路径上决定排序，属于"会改变执行决策的缓存状态"，与 kernel.py:7-9 "No result/negative/coalescing cache exists on this path" 的零缓存自述存在张力，且重启即失忆、多进程不共享（文档未提）。实现层面三处问题：

1. **双重记账**：`UnifiedRuntime.execute` 记成败（kernel.py:99-104），`ProviderOrchestrator.execute_auto` 对同一次尝试再记一遍（orchestration.py:228-233）——同一失败写两次，后者纯冗余。
2. **内核级 auto 丢 provenance**：`QueryPlanner._resolve_auto` 把 `provider="auto"` 静默替换成具体 provider（query.py:687-712），`Provenance.direct` 则记 `requested_provider=plan.provider`（result.py:95）。实跑验证：`UnifiedRuntime().planner.compile(QuerySpec.build("quotes", symbols="sh600519", provider="auto"))` 得 `plan.provider=tdx`，`Provenance.direct(plan).requested_provider == "tdx"`、`fallback == False`——auto 请求在内核层与钉死 tdx 的请求 provenance 完全相同，违背 autoselect.py:9-11 "每个结果仍然带 Provenance——requested_provider='auto'" 的自述（Client 层 `_execute_auto` 补了，api.py:242-287，但内核层没有）。
3. **两处 auto 排除集不一致**：Client 路径未配 vipdoc_root 时排除 `local_vipdoc`（api.py:250-254），planner 静态 auto 路径（query.py:687-712）不排除——同是 `provider="auto"`，走 Client 会避开配置错误，直连内核不会。

**改法**：删掉 execute_auto 里的重复记账；`_resolve_auto` 也走 orchestrator 或至少在 plan/spec 上保留 `requested_provider='auto'`；local_vipdoc 排除逻辑下沉到 `auto_candidates`。

### A9【中·架构】注册表死声明（已运行复现）

tdx.py:72-74 在 extended/goods/mac 通道上声明了 `bars`/`quotes` 能力，但这些三元组无 migrated 绑定（`binding_for` KeyError）、executor_name 为 `_migrated_capability`，且 `QuerySpec.normalized` 对统一 quotes/bars 一律拒绝非 canonical channel（query.py:169-196）。实跑结果：

```
('tdx','goods','quotes')   → ValidationError: 统一 quotes QueryPlan 不能执行 provider 'tdx' channel 'goods'
('tdx','extended','bars')  → ValidationError: 统一 bars QueryPlan 不能执行 provider 'tdx' channel 'extended'
('tdx','mac','quotes')     → ValidationError: 统一 quotes QueryPlan 不能执行 provider 'tdx' channel 'mac'
```

这些能力进入能力发现面（capabilities 列表、文档生成物），调用方按名字调用必然 E1010。A1 的恒真对账对此免疫。

**改法**：要么删掉这 5 个死声明，要么给它们接真实执行体（ex/goods 客户端的 bars/quotes 已有 `ex_bars`/`goods_bars` 等独立能力名，疑似历史残留）；并在 audit 里加"声明能力必须可规划"检查。

### A10【中·架构】quotes_batch 双实现

`UnifiedRuntime.quotes_batch`（kernel.py:124-161）与 `Client.quotes_batch`（api.py:450-508）是同一业务的两份实现：Client 版支持 auto 逐只全链、`batch_workers` 线程池、normalize 容错在 try 内；kernel 版无 auto 链（provider="auto" 被静态解析成第一跳，见 A8）、串行、normalize 在 try 外。Client 版并不调用 kernel 版。两份代码已经开始分叉，改一处漏一处。

**改法**：Client.quotes_batch 改为调用 kernel 版并把 auto/并发作为参数下沉，删掉 kernel 版或 Client 版之一。

### B1【中·细节】_OPTIONS_CACHE 缓存明文凭证 + 浅拷贝

query.py:202-203 模块级 `_OPTIONS_CACHE`（上限 2048，从不淘汰，只停写）。`options` 属性每次返回 `dict(cached)` 浅拷贝（query.py:392-397），嵌套结构（`kwargs` 里的 dict/list）与缓存共享。fingerprint 对 options 做了脱敏（query.py:560-575），缓存里却是明文——wencai cookie 这类凭证长期驻留进程内存。任何实现若原地改写嵌套 options，会污染后续所有同 options_json 的查询。

**改法**：缓存 value 存 JSON 串或 deepcopy；或干脆去掉缓存（options 解析本就便宜），零缓存原则下少一个例外。

### B2【中·细节】fallback 不分类错误

orchestration.py:169-172 `except Exception as exc: ... continue`：ValidationError（调用方传错代码/参数）与网络故障同罪，逐家重试后以 `AllSourcesExhausted` 收场（orchestration.py:182-189），真实错误码被埋进 `errors` 列表。execute_auto 同型（orchestration.py:228-232）。fail-closed 的本意是"不静默换源"，现在变成了"错误也换源"。

**改法**： ValidationError / FreshnessViolation 一类 4xx 语义直接上抛，不进候选循环。

### B3【中·细节】quotes_batch 一个坏符号中断整批

kernel.py:145 `symbol = normalize_symbol(raw_symbol)` 在 try 块外，抛错即整批失败——与同函数"逐 symbol 三态"的设计目标相悖（Client 版 api.py:476 同位置同样裸奔，但其调用点在 `one()`/`run()` 之外，同样中断）。batch.py 的 `not_attempted` 状态就是为这种场景准备的，却没人用。

**改法**：normalize 移入 try，失败记 `BatchItem("failed")` 或 `not_attempted`。

### B4【中·细节】composed 直读本地文件

composed.py:74-78 按 `vipdoc_root/market/lday/{market}{code}.day` 拼 path 直调 `DayBarReader`，与 execute_channel.py:121-164 `_local_bars` 的路径拼接/窗口切片逻辑重复，且绕过 local_vipdoc Provider 声明（不走注册表、不产生 Provenance、不受 `currentness` 闸门管）。改动本地布局要改两处。

**改法**：`_adjusted_raw_bars` 统一走 `local_vipdoc/vipdoc/bars` 的 DirectBinding。

### B5【中·细节】重复绑定表

同一份 capability 清单存在于：① 各声明模块 SPEC 的 channel capabilities（tdx.py:20-60）；② 同模块 BINDINGS 逐条 CapabilityBinding（tdx.py:84-216）；③ bindings.py:32-56 `_executor_for` 硬编码 dedicated 能力集（`{quotes, bars, snapshot, minute, trades, security_count, security_list}`）；④ tests TIER_A 又抄一遍。derived.py:143-1105 用完全相同的 6 行模板手抄 110 遍（仅 capability/method 两个词变化），1105 行里约 950 行是机械重复——`(_c("catalog", *CAPS),)` + 循环生成 BINDINGS 即可，声明期校验（`_aggregate_bindings`、`_validate_channel_declarations`）一个不少。

**改法**：derived/builtin 改为能力名表 + 生成器；dedicated 集合从 executor 方法名现算（`_tdx_*`/`_<provider>_bars` 命名约定已有），删 `_executor_for` 的硬编码清单。

### B6-B10【低】

- B6：`audit_runtime()` 在 `UnifiedRuntime.__init__`（kernel.py:56）和 `DirectProviderExecutor.__init__`（executor.py:204）各跑一遍，每次 Client() 全注册表扫两遍。改法：进程内一次性缓存审计结果。
- B7：query.py:434-437/451-454 `except Exception: markets = set()`，parse_symbol 的真实缺陷被吞、静默不走市场路由。改法：只捕预期的解析异常类型。
- B8：typed_query.py:66-68 `futures_kline`/`futures_base_info`/`futures_realtime`/`futures_trades` 映射 `BondRecord`；`call_payload_from_typed`（typed_query.py:157-167）把所有 dataclass 字段（含空串默认值）塞进 payload，实现签名收到显式 `symbol=""` 而非缺省。改法：futures 用独立 Record；只下发非默认字段。
- B9：batch.py:137-141 `partial` 必须等于 `bool(errors)`，而 kernel.py:155-156 把空结果记为 `missing` 不进 errors——10 只里 5 只 missing 时 `partial=False`、`success=False`，两个布尔讲两个故事。改法：partial 定义改为"存在非 ok 项"或文档写死。
- B10：freshness.py:44-52 只对 `channel.local` 判"无法证明当期"；web 非 live 通道在 `business` 口径下不设防（模块 docstring 自认"refusing those would be inventing policy"）。既然自认不管，至少别在 docstring 里暗示覆盖。

## 4. 待证项

- composed 内层查询的实际耗时放大倍数（A2-④）：需要一次真机 `adjusted_bars` 计时对比外层 deadline，本地未跑网络 I/O。
- `create_source(plan.provider, ...)`（execute_channel.py:184）对 provider id 与 source 名的兼容面：未逐家验证 baidu/tencent/sina/eastmoney 四个 id 都能被 create_source 接受（有测试覆盖的迹象，未读）。
- 服务面（HTTP/WS/MCP/CLI）是否有第三处 composed provider= 翻译或第三份 quotes_batch：本次只读了 Client 与 kernel 两层。

## 5. 建议的下一步

1. 先做 A1（对账闭环 + executor 方法存在性审计）——成本低、能立刻把死声明（A9）和未来绑定漂移变成启动期红灯。
2. A2+A4+A8 一起动：composed 上移进 UnifiedRuntime、auto provenance 保留、错误分类（B2），这三条都在同一条执行链上。
3. 结构性重构（A5/A6/A7 拆 query.py、断循环依赖、provider 事实回归注册表）放在行为修复之后，避免同时动契约与执行。
