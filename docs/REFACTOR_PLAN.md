# atst 架构梳理与优化改进重构方案（v2.1.0）

> 审查日期：2026-11-22。对象：工作区快照（v2.1.0，`/v13/` 单内核）。
> 方法：六路并行深审（内核契约 / sources / 传输客户端 / 流式服务面 / 协议读取 / 工程化），
> 每条结论以 `文件:行号` 一手证据为准，关键结论经实跑复现；本文是汇总与决策文档，
> 分层证据全文见 [docs/arch-review/](arch-review/) 六份报告。
> 严重度：高＝正确性/架构自我违背；中＝漂移、重复、可维护性；低＝卫生与打磨。

---

## 1. 项目架构现状梳理

### 1.1 定位与规模

atst 是通达信（TDX）行情协议基础设施：协议编解码 → 多源 Provider → 统一查询内核 →
CLI / HTTP / WS / MCP 四面服务面。协议实现 100% 自有（洁净室流程）。

| 指标 | 数值 |
|---|---|
| 源码 | 223 个 py 文件，约 61,800 行 |
| 测试 | 321 个 py 文件，约 54,500 行（其中 golden 样本 1,847 个文件） |
| 覆盖率 | 全包 line 86.0% / branch 75.6%（门禁 `fail_under=77`） |
| 最大包 | `sources/` 69 文件 18,436 行（占全仓 30%） |
| 最大文件 | `transport/async_.py` 1,329 行、`cli/runtime_commands.py` 1,145 行、`providers/derived.py` 1,105 行、`client/_mixin.py` 1,091 行 |
| import 环 | **0**（AST 全量扫描确认） |
| 第三方依赖 | 主包 `dependencies = []` 成立，全部惰性导入 |

### 1.2 分层结构（当前事实）

```
CLI / HTTP(runtime_http) / WS(runtime_ws) / MCP(server/mcp)   ← 只翻译
        ▼
atst.Client / AsyncClient（client/api.py，217 capabilities）
        ▼ QuerySpec
UnifiedRuntime（runtime/kernel.py，零缓存单内核）
        ▼ QueryPlanner.compile → QueryPlan（单 Provider / 单 Channel）
DirectProviderExecutor（runtime/executor.py，DIRECT_BINDINGS 304 条）
        ▼
providers/ 注册表（14 个声明模块）→ tdx 协议栈 / sources/ 九家 web 源 / reader/ 本地 vipdoc
        ▼
QueryResult（data + meta.provenance）
```

流式：`StreamSpec → StatefulQuoteStream`（五态状态机）；配置：6 段 / 6 源合并。

### 1.3 已核实成立的设计（正面清单，重构时不要动）

- **无循环依赖**：全仓 AST 扫描 0 环（query↔runtime 靠函数级 import 规避，见 §2.6，但图上无环）。
- **零依赖主包成立**：`atst/` 无顶层第三方 import，运行时镜像 `--no-deps` 裸装 wheel。
- **配置无死键**：6 段配置全部被内核/传输/legacy 门面真实读取，`pool_settings_from_config`
  是配置→传输唯一翻译点。
- **四面「只翻译不执行」主链路成立**：CLI `cmd_*` / HTTP 路由 / WS 分派 / MCP handler
  确实只翻译为 `Client.*`（豁免项均有文档声明：CLI 六支直连、hosts/universe 运维命令）。
- **`make gates` 13 目标 ↔ CI 13 作业 1:1**，由 `tests/support/gate_inventory.py` 钉死。
- **协议 L1/L2/L3 接缝结构清楚**：dispatch → parser → generic → L3 raw，降级路径完整，
  未知命令永不丢包；`encode_leb128` 有符号往返实测 -3000..3000 无损。
- **仓库卫生正确**：`.coverage`、`coverage.xml`、`dist/`、`release-dist/`、`*.log` 均被
  .gitignore 覆盖且未跟踪；契约测试主体是 AST 派生判据（代码为真值源），不是「改文档才能改代码」。
- **providers ↔ sources 导入期对账干净**：282 条绑定 factory/method 全部可解析。

**总评**：v13 的骨架（单内核、provider-first、四面翻译、fail-closed 意图）是清晰的，
问题不在「没有架构」，而在**架构自述与实现之间存在系统性落差**——多条红线被自家代码打破、
多套平行实现开始漂移、若干「已修复」的历史问题以同型复活。以下按主题列出。

---

## 2. 架构设计不合理之处

### 2.1 【高】provider-first 禁令被自家代码四处打破

`docs/ARCHITECTURE.md` 与 `runtime/execute_channel.py:174` 明文：「一个 Plan 永不私选第二
Provider」「有序降级是内核禁止的形状」。但：

| 位置 | 落差 |
|---|---|
| `sources/__init__.py:419-441` | `WebQuoteClient.quotes` / `get_quotes(source=None)` 就是一条多源有序降级链，且把 `AttributeError/KeyError` 当「源不可用」吞掉继续换源——公开门面是活着的降级实现 |
| `providers/derived.py` + `builtin.py` | 110+ 能力 `backend="web_session", source="sina"`，真实出数源是新浪，provenance 却报 `provider="derived"`。`providers/__init__.py:9-12` 自述「选 tdx 绝不会静默执行新浪」——聚合 Provider 上信任边界失守 |
| `runtime/composed.py:92` | composed 内层查询直连 `executor.execute(QueryPlanner().compile(...))`，**绕过 UnifiedRuntime**：无健康反馈、无溯源校验、内层各自重置 5s deadline、忽略配置缺省源 |
| `sources/_session_market.py:338` | 会话层自称「不是路由层」，`klines`/`history` 却按市场/复权 hardcode 换源，与 providers 的 `MARKET_DEFAULT_CAPABILITIES` 重复 second-guess |

同一仓库两套裁决并存：内核禁止降级，门面与聚合层继续降级。调用方无法从文档判断自己走在哪条路上。

**方向**：降级只保留内核 `FallbackPolicy` 一个出口；`WebQuoteClient` 降级链标 legacy 并默认
fail-fast；derived/builtin 的 Provenance 增加 `upstream` 字段透传真实 source；composed 编排
上移进 UnifiedRuntime。

### 2.2 【高】假异步 + 连接池语义在主链路不成立

- `client/api.py:823` 起：`AsyncClient` 全部方法是 `asyncio.to_thread` 转调**同步**内核，
  连 `close()` 也是。真异步栈 `AsyncTdxClient`/`AsyncConnectionPool`（`transport/async_.py`，
  1,329 行）在生产代码**零调用**，只被测试供养。
- `runtime/executor.py:301`：内核每一跳现造 `TdxClient + ConnectionPool + 心跳线程`，用完即弃。
  一次 `quotes_batch`（逐 symbol）= N 次 Client 构造 ×（resolve_hosts + 8 主机×4 槽 + 心跳线程
  start/join）。`Client.close()` docstring 自己承认「关不掉连接池，因为默认内核不跨调用持有连接」。
- 后果：M6 空闲回收（300s）与熔断冷却（30s）在「用完即关」的池上**永不生效**；异步用户并发度
  被 asyncio 默认线程池封顶；1,329 行异步池与 1,070 行同步池手抄同构约 700 行（retry 循环、
  update_hosts 事务、multi/iter 帧、心跳、close 全部两份），`policy.py` 只抽走了熔断纯函数，
  异步侧还把 `_select_allowed_slot` 又抄了一遍。

**方向**：`DirectProviderExecutor` 持有按 family 缓存的常驻 `TdxClient`（`update_hosts` 代际
热更新机制现成）；async 战线二选一——要么内核提供 async 执行体直连 `AsyncTdxClient`，要么
删除 `AsyncConnectionPool` 公开承认 to_thread。拖着两套实现是持续的漂移税。

### 2.3 【高】启动审计是恒真式，死声明与死 API 无人拦

- `runtime/capability_audit.py:44-62`：所谓「双向对账」中 `declared` 与 `executable` 同源
  （`DIRECT_BINDINGS` 就是从注册表三元组生成的，`bindings.py:73-76`），`unbound`/`ghost`
  **恒为空集**，两条 raise 是死代码；真正能抓漂移的 `audit_direct_bindings()`（bindings.py:101）
  **不在** `audit_runtime()` 启动审计里，只在测试里被调用。
- `executor.py:223` `getattr(self, binding.executor_name)` 无前置方法存在性校验，拼错名字
  运行期炸裸 `AttributeError`。
- 已实跑复现的死声明：`('tdx','goods','quotes')`、`('tdx','extended','bars')`、`('tdx','mac','quotes')`
  等进入能力发现面，调用即 E1010。
- `client/core.py:494` `_bars_body`/`_quote_body` 无条件抛 `NotImplementedFeature`，
  Goods/Ex/Mac 报价方法是「调用即炸」的死 API，却仍在工厂 `get_client("goods"|"ex"|"mac")`
  正常导出。

**方向**：`audit_runtime()` 纳入 `audit_direct_bindings()` + executor 方法名存在性集合；
对账改为三方独立集合两两求差；死声明删除或接真实执行体；死 API 在工厂层不导出或统一
`_guard_offline` 式拦截。

### 2.4 【高】流式 fail-closed 在生产入口不可达；WS 反压与订阅隔离双双失守

- `streaming/base.py` 两套基类 `_run` 都吞掉一切 `Exception` 继续转，`StreamLifecycle.fail()`
  的主要触发前提（worker 异常穿出 `_run`）不成立；`StatefulQuoteStream` 又没接
  `ReconnectPolicy.max_attempts`——永久失败的流恒为 `RUNNING`，FAILED/CLOSED 语义在生产
  入口近乎死码。
- `streaming/base.py:659-681`（async stop）与 `:441-460`（sync stop）：超时分支清 `_task`/
  关 owned runtime，worker 可能仍卡在不可取消的 `to_thread` 上——正是同文件 docstring 声称
  已修复的 F-91/F-92 同型复活；孤儿 worker 下一轮 `_get_runtime()` 重建 runtime 无人回收。
- `server/runtime_ws.py:566-596`：「超阈值丢最旧一帧」只 `del pending[:overflow]` 删 future
  引用不 `cancel()`，帧照发、内存照涨，**慢客户端反压不存在**。
- `server/runtime_ws_server.py:62`：调用方传入 handler 时所有连接共用同一实例，
  `bind_connection` 互相覆盖 `_send`，A 连接的推送打到 B 的 socket——「订阅表每连接私有」
  只在 `handler=None` 分支成立，且测试全部走该分支。

**方向**：超时分支不清句柄不关池、改走 `fail()`；Stateful 流接 max_attempts 落 FAILED；
溢出 future 逐个 `cancel()`；handler 传入时 per-connection 克隆。

### 2.5 【高】sources 层：上帝对象 + 三条第二账本 + 组织双轨

`sources/` 占全仓 30%（18,436 行 / 69 文件），是最大也最乱的一层：

- `WebQuoteSession` 组合 **19 个 Mixin、184 个公开方法**（`sources/session.py:176-198`），
  catalog channel 全部绑到它——按主题切分（`_session_*`）与按 Provider 归组（九个子包）
  两套架构互相妥协出来的上帝对象。
- 三条第二账本没人对账：能力词表（`sources.py` 用 `quote`，providers 用 `quotes`）、
  默认顺序（`DEFAULT_FALLBACK_ORDER=(tencent,sina,eastmoney)` vs
  `_DEFAULT_PREFERENCE=(derived,tdx,eastmoney,…)`）、量纲系数（SourceSpec / VolumeNormalizer /
  解析层内联三处，`test_b8_scale_consistency.py` 用例外清单补偿设计）。
- 包根散落 51 个域模块：`fundflow.py`（895 行）混装东财 5 类 + 新浪资金流；sina 的
  boards/esg/news 又散落别处；`sina/adapters.py:174` 从 `tencent.adapters` 导
  `DEFAULT_MAX_PAGES`（跨 Provider 耦合，注释自认循环 import 风险）。
- 传输底座「已收口」名不副实：仍有 3 份手写 host failover 循环、4 份独立 `_get_json`、
  2 处自写退避 `time.sleep`。

**方向**：会话面按 Provider 域拆 3-4 个会话类，路由决策查 `MARKET_DEFAULT_CAPABILITIES`；
三本账各留一份并由 providers 声明派生；域模块按 Provider 归组。

### 2.6 【中】query.py 职责过载 + 契约层与运行时互为依赖

- `query.py` 749 行装了 8 类职责；`normalized()` 名为归一实为校验+语义改写
  （quotes 全 hk/us 时改写 capability、bars 港美股改写缺省 provider），改写依据只活在函数栈里，
  调用方从数据上看不出 capability 被换过。
- 契约层硬编码 provider 事实：`_CANONICAL_UNIFIED_CHANNELS`（query.py:64-77）、
  `_NON_TDX_BAR_PROVIDERS = ("eastmoney","tencent")`（query.py:62）——provider-first 被倒置，
  注册表自述「不做第二套市场词汇」的原则在这里被自己违反。
- 循环依赖靠函数级 import 规避：query.py:675/793 反向 import runtime.autoselect 与
  runtime.capability_contract；bindings.py:88 反向 import query 私有函数。「四面只翻译」
  的分层在 import 图上不成立。

**方向**：拆 `spec.py`（纯契约）/ `planning.py`（规划）/ `fingerprint.py`；canonical channel
进 ChannelSpec；auto 常量下沉，断开 query↔runtime 环。

### 2.7 【中】sync/async、双 API 面、双领域模型——系统性漂移税

| 双实现 | 漂移证据 |
|---|---|
| 同步池 vs 异步池 | 异步无 `retry`/`compress`/`PoolStats`/后台测速/心跳租约；parity 测试只锁方法名 |
| `Client` vs `TdxClient` | 同名方法语义不同（`quotes` 一个走 planner 一个走 0x0530）；`_call_core` 用 `getattr` 把核心能力表绑在 Client 方法名上 |
| `quotes_batch` 两份 | kernel 版（kernel.py:124）与 Client 版（api.py:450）已分叉：auto 链、并发、normalize 容错位置全不同，互不调用 |
| 字符集探测两套 | `codec/primitive.py:558` GBK 优先 vs `charset/encoding.py:66` UTF-8 优先，优先级相反；charset 版生产零调用 |
| profile 探测两套 | `reader/profile.py` 与 `profile/detect.py` 六步管线互不调用，权重各一套，文档权重与实现不符 |
| 领域模型双轨 | `domain/models.py`（Bar/Quote/Tick）与 `domain/records.py`（9 个 v14 Record）并存，records 手写 9 份同构 `to_dict/from_dict` |
| codegen 断头 | 生成物只是 stub 骨架写进「草稿区不进包」，全部 L1 解析器实为手写，spec↔实现漂移只靠测试拦截 |

### 2.8 【中】文档与实现互相矛盾（会误导新用户与 AI 维护者）

- `README.md:27/107/498` 三处宣称「三级路由降级」，与 `README.md:43/86/484` 自己的
  「无聚合降级路由」及 `ARCHITECTURE.md:7-9`「五级降级路由已物理删除」三方矛盾，
  全仓代码 0 命中该说法（真实存在的是 RankingStore 测速排序）。
- 「零缓存」自述 vs 三处会改变决策的缓存状态：`ProviderHealthStore`（autoselect，进程级 TTL，
  决定 auto 排序）、`_OPTIONS_CACHE`（query.py:202，缓存明文 options 含 cookie 凭证，浅拷贝
  共享嵌套结构）、web 传输层主机 TTL 排序。零缓存原则指「结果级」，但文档没有把这条边界写清，
  健康缓存重启失忆、多进程不共享也未提。

---

## 3. 详细开发细节问题清单

按模块汇总六路审查的细节发现（架构性问题已在 §2 展开，此处收细节）。
完整证据与引文见对应分报告。

### 3.1 内核与契约（详见 [arch-review/runtime-contract.md](arch-review/runtime-contract.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| R-1 | 中 | query.py:202 | `_OPTIONS_CACHE` 缓存明文凭证、浅拷贝共享嵌套 | 存 JSON 串/deepcopy，或删缓存 |
| R-2 | 中 | orchestration.py:169 | `except Exception` 不分类，ValidationError 也触发跨源重试，最终 AllSourcesExhausted 掩盖真实错误 | 4xx 语义直接上抛 |
| R-3 | 中 | kernel.py:145 | quotes_batch 的 normalize_symbol 在 try 外，一个非法符号中断整批 | 移入 try，记 not_attempted |
| R-4 | 中 | composed.py:74 | composed 直读 DayBarReader 绕过 provider 面，路径拼接与 `_local_bars` 重复 | 统一走 local_vipdoc DirectBinding |
| R-5 | 中 | derived.py:143-1105 | 同一 6 行模板手抄 110 遍（约 950 行机械重复）；SPEC 与 BINDINGS 双处声明 | 能力名表 + 生成器 |
| R-6 | 低 | kernel.py:56 + executor.py:204 | `audit_runtime()` 每次 Client 初始化跑两遍 | 进程内 memo |
| R-7 | 低 | query.py:434 | `except Exception: markets = set()` 吞 parse_symbol 真实错误 | 只捕预期类型 |
| R-8 | 低 | typed_query.py:66 | futures_* 映射 BondRecord；空默认值字段全量下发 | 独立 Record；只发非默认字段 |
| R-9 | 低 | batch.py:137 | `partial` 只跟 errors 联动，missing 不进 errors，两个布尔讲两个故事 | 统一定义 |
| R-10 | 低 | freshness.py:44 | currentness 只防 channel.local，web 非 live 通道 business 口径不设防 | 文档写清或补闸 |

### 3.2 sources（详见 [arch-review/sources.md](arch-review/sources.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| S-1 | 高 | eastmoney/adapters.py:81 | `fetch` 只取 `symbols[0]`，多标的批量行情**静默丢数据** | 切批请求或显式抛「仅支持单标的」 |
| S-2 | 中 | _base_em.py:66 等 | 3 份手写 host failover 循环、4 份独立 `_get_json`、2 处自写退避 | 二次收口到 `_get_json`/`_request_http` |
| S-3 | 中 | fundflow.py:547 等 | 东财 `ut=` token 硬编码散落 3 处 | 集中常量并注明来源日期 |
| S-4 | 中 | _session_research.py:689 | 裸 `except Exception` 吞错继续，静默缺数（risk_scan/stock_base_info） | record_warning 计数并标注 |
| S-5 | 低 | _base_core.py:89 | `needs_referer` 一律注入 `finance.sina.com.cn`，对非新浪源是陷阱 | Referer 进 SourceSpec |
| S-6 | 低 | tencent/adapters.py:148 | 高/低缺失回退用现价冒充 | 缺失置 NaN 并告警 |
| S-7 | 低 | sina/adapters.py:68 | 单条字段不足抛异常使整批失败；空结果一律 SourceDeprecated | 逐条容错 + 区分「无此代码」 |
| S-8 | 低 | _session_market.py:148 | quotes() 把非 TdxError 统一改写 CompatibilityError，语义丢失 | 只包传输类 |
| S-9 | 低 | __init__.py:451 | klines 强制追加 KLINE 源，无视调用方 sources 配置 | 尊重配置 |
| S-10 | 低 | fundflow.py:522 | 单主机 push2ex 源无 failover，与同文件 `_EastmoneyJson` 家族不一致 | 改继承 `_EastmoneyJson` |

### 3.3 传输与客户端（详见 [arch-review/transport-client.md](arch-review/transport-client.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| T-1 | 高 | pool.py:664,678; async_.py:903,918,1337 | request() 失败路径传**读取时刻**的 `slot.generation` 而非租约代际，代际守卫恒真；update_hosts 后旧连接迟到失败记到新主站头上 | 改传租约 generation + 回归测试 |
| T-2 | 高 | client/core.py:192 | split_symbol 拒绝 market=2，BJ 在 tdx 路径不可用，且 ParseError 被归类为**可重试**协议错（E3040/建议换主站）；同文件 `_standard_market_id` 却接受 bj | 改抛 ValidationError，或 bars 路由把 bj 翻译到 Web 源 |
| T-3 | 中 | client/sync.py:430 | quotes_concurrent 多线程无锁增减 `_drive_depth`（告警归属错帧） | threading.local |
| T-4 | 中 | client/quick.py:40 | quick 每次调用新建 Client → 全量 audit_runtime | 进程级懒初始化 Client |
| T-5 | 中 | _mixin.py:810 | quotes_snapshot 每批先发已知 offline 的 0x054C 再回退，多付一次必败 RTT | 默认跳过，显式开启才试 |
| T-6 | 中 | _mixin.py（1091 行） | trampoline 驱动器 + 全部协议模板 + 四族子客户端堆叠；栈深靠魔法常数伪装 | 拆驱动器/标准族/扩展族三文件；`_t_bars` 状态机纯函数化 |
| T-7 | 低 | transport/base.py:143 | 心跳命令 0x0002 未登记账本，配置面无 heartbeat_cmd 键 | 纳入 pool_settings_from_config 或 schema 拒绝 |
| T-8 | 低 | api.py:912 | Client.quotes_batch 与 TdxClient.quotes_concurrent 两套并发批量 | 内核版复用 worker 形状 |

### 3.4 流式与服务面（详见 [arch-review/streaming-faces.md](arch-review/streaming-faces.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| F-1 | 中 | streaming/base.py:402,596 | 订阅 id 用 `len(self._subs)`，退订后再订静默覆盖在跑订阅 | 单调 seq |
| F-2 | 中 | streaming/engine.py:419 | 背压队列 put 与 drain 同轮询线程先后完成，`max_queue=1024` 永不触顶 | 分离生产/消费或改口契约 |
| F-3 | 中 | runtime_ws.py:132 | close() 在 loop 线程 finally 里 `run_coroutine_threadsafe(...).result(5)`，协程永不得调度，白等 5 秒 | 同 loop 直接 await |
| F-4 | 中 | runtime_ws.py:448 | 订阅表在 worker 线程与 loop 线程无锁共享读写 | 统一到 loop 或加锁 |
| F-5 | 中 | runtime_commands.py（1145 行） | 单文件混 8 个域；`_cmd_*` 一族裸 print 绕过错误信封 | 按域拆四模块；异常一律上抛 |
| F-6 | 中 | cli/parser.py:279 | `atst serve` 只拉起 HTTP，WS 面无 CLI 入口，第四面断链 | `atst serve --ws` |
| F-7 | 中 | sink/local_day.py:180 | sync() 断点判断在新增计入 `new` 之后，窗口内补洞 bar 追加到文件尾，`.day` 时间序被破坏 | 合并排序重写 |
| F-8 | 中 | runtime_ws.py:421 | 每条 subscribe 各起独立轮询，同标的重复订阅不合流 | 按 symbol+provider 合流 |
| F-9 | 低 | 四面各自 | query 形状校验四面各抄一遍（连错误文案都逐字相同）；流式入参三重校验 | 抽 `parse_query_call`；校验收口 StreamPlanner |
| F-10 | 低 | mcp/_tools_spec.py | MCP 9 工具无 subscribe，HTTP 面亦无流式入口 | 暴露工具或文档明示 |
| F-11 | 低 | output/__init__.py:464 | `Sink("csv")` 不支持而 `write(*.csv)` 支持 | 补 csv 分支 |
| F-12 | 低 | runtime_ws.py:195 | WS 业务失败顶层 message 是通用文案，MCP/HTTP 顶层即信封 message | 提到顶层 |

### 3.5 协议与本地读取（详见 [arch-review/protocol-reader.md](arch-review/protocol-reader.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| P-1 | 高 | protocol/registry.py:435 | ProtocolSniffer 挂在生产分派热路径，每个 L2/L3 响应同步落盘（全局锁 + 相对 cwd 写 PROTOCOL_SPEC/_sniffer） | 默认关闭或异步队列 + 配置注入绝对路径 |
| P-2 | 高 | reader/formats.py:160 | `_prepare` 捕获所有异常改用默认档案，违背 reader/profile.py:30「探测失败绝不静默猜测」；错 scale 静默产出 10 倍错价 | strict 时上抛 ProfileUndetectable |
| P-3 | 高 | codec/primitive.py:558 | 字符集探测两套实现优先级相反；printable 过滤对汉字无效，UTF-8 中文被 GBK 解成乱码 | 探测合一；多编码可解时要求显式 encoding |
| P-4 | 中 | protocol/commands.py:163 | tier 三处声明（账本/装饰器/BaseParser.TIER），confidence 由常量推导恒 1.0/0.9；0x053E/0x054C 账本 D 与解析器 L2 已漂移 | tier 单一事实源放账本 |
| P-5 | 中 | profile/detect.py:409 | 两套六步探测管线互不调用；文档权重与实现不符 | 抽公共打分内核 |
| P-6 | 中 | handshake.py:71 | 握手帧裸 hex 魔数绕过 FrameSpec；prober 独立硬编码剥头长度 | 改用 build_request / resp_header_size |
| P-7 | 中 | parsers/（40+ 处） | 定长记录循环样板逐字重复；`_check_echo` 复制两份 | BaseParser.iter_fixed_records |
| P-8 | 中 | reader/formats.py:219 | `_decode_records` 固定 32 字节，探测出的 record_size 无人消费 | 用 `p.record_size or 32` |
| P-9 | 中 | profile/detect.py:425 | 两分支同值死分支；evidence 只记最优候选；preset 按 market_id 粗匹配形同虚设 | 删死分支；证据全入链 |
| P-10 | 低 | reader/formats.py:549 | gpcw 字段数按整除取第一个，30 字段文件可被误判 28；不可整除静默当单条 | 不可判定即抛错 |
| P-11 | 低 | generic.py:88 | `_looks_like_uint16_date` 零调用死代码 | 删除或接入 |
| P-12 | 低 | prober.py:416 | `_total_probes` 在锁外自增，并发计数失真 | 移入锁内 |
| P-13 | 低 | codec/primitive.py | BinaryWriter 越界值掩码静默截断，与 Reader 越界抛错不对称 | 改校验 |
| P-14 | 低 | batch.py:105 | BatchResult 每次构造全量 deepcopy，热路径开销 | 浅拷贝 + 只深拷 meta |

### 3.6 工程化（详见 [arch-review/engineering.md](arch-review/engineering.md)）

| 编号 | 严重度 | 位置 | 问题 | 改法 |
|---|---|---|---|---|
| E-1 | 中 | README.md:27 | 「三级路由降级」三处陈旧卖点，与代码/自身/ARCHITECTURE 三方矛盾 | 改为 RankingStore 测速排序表述 |
| E-2 | 中 | pyproject.toml:180 | 77% 是全包均值门禁：19 文件 <70%、8 文件 <50%、两个 `__main__.py` 为 0，`_session_*` 族最低 0.19 | per-module 下限或 diff-cover |
| E-3 | 中 | Makefile:52 | `test-bridges` 依赖 fastapi 只在 CI 补装，只装 `.[dev]` 的贡献者 `make gates` 必红 | 前置 import 探测并提示 |
| E-4 | 中 | pyproject.toml:66 | `all` extra 手抄全部依赖，注释自述曾混入死依赖 | 改自引用 extras |
| E-5 | 中 | CHANGELOG.md | 343KB/3952 行手写账本与 git-cliff.toml 双工具并存无同步判据 | 二选一 |
| E-6 | 低 | config/loader.py:300 | `get_config()` 惰性单例无锁 check-then-act | 加 threading.Lock |
| E-7 | 低 | config/loader.py:168 | `ATST_RATE_LIMIT_STRICT=1` 被解析成 int，bool 字段 fail closed | bool 字段接受 1/0 |
| E-8 | 低 | ci.yml:57 | test 矩阵无 macOS（classifiers 宣称 OS Independent） | 补 1 格 macos+3.12 |
| E-9 | 低 | pyproject.toml:181 | `skip_covered=true` 藏 100% 文件；CI 7 格 coverage 不聚合 | skip_covered=false |

---

## 4. 分阶段重构方案

原则：**先正确性、再收敛、后重组**；每一步都有现成测试基建护航
（`tests/architecture/`、`tests/provider_isolation/`、`tests/streaming/`、golden 回放）；
不动协议冻结层（codec/commands 账本）的大结构；不引入结果级缓存、不恢复多级降级路由。

### P0 正确性止血（建议 1-2 周，全部小 diff，可并行开 PR）

| # | 事项 | 主要文件 | 验收判据 | 风险 |
|---|---|---|---|---|
| P0-1 | 东财批量丢数：`fetch` 支持多标的或显式报错 | sources/eastmoney/adapters.py | 多 symbol 调用不再静默丢；单标的行为不变 | 低 |
| P0-2 | 代际守卫修复：request() 失败路径改传租约 generation（sync+async+async 心跳） | transport/pool.py, async_.py | 新增「update_hosts 后旧代失败不记新 host」回归测试 | 低 |
| P0-3 | reader strict 语义：`ProfileUndetectable` 默认上抛；解码消费探测 record_size；gpcw 字段数不可判定即抛错 | reader/formats.py | scale=1000 样本不再静默 ×100；29 字节样本不再按 32 错位 | 中（行为变化，需 changelog） |
| P0-4 | BJ 市场错误归类：split_symbol 改抛 ValidationError（不可重试），query.py bars 路由把 bj 翻译到 Web 源（与 hk/us 同待遇） | client/core.py, query.py | `Client.bars("bj430047")` 不再 E3040/建议换主站 | 低 |
| P0-5 | WS 推送真丢帧：溢出 future 逐个 `cancel()` | server/runtime_ws.py | 慢客户端场景队列深度有界、帧数可数 | 低 |
| P0-6 | WS 共享 handler 串台：传入 handler 时 per-connection 克隆或绑定防重入 | server/runtime_ws_server.py, runtime_ws.py | 双连接互发不串台测试（现有测试全走 handler=None） | 低 |
| P0-7 | 流式 stop 超时孤儿 worker：超时分支不清 `_task` 不关 runtime，改走 `fail()`；`_get_runtime` 重建前检查 `_stop` | streaming/base.py | stop 超时后无存活 worker、无泄漏 runtime | 中 |
| P-8 | sniffer 摘出热路径：默认关闭/异步队列，归档根由配置注入 | protocol/registry.py, generic.py | 批量未知命令响应不再每响应两次文件 IO | 低 |
| P0-9 | 启动审计闭环：`audit_runtime()` 纳入 `audit_direct_bindings()` + executor 方法存在性校验；删除 5 个死声明 | runtime/audit.py, capability_audit.py, providers/tdx.py | 拼错 executor_name 启动即红；goods/quotes 等不再出现在能力面 | 低 |
| P0-10 | 编排不吞参数错：ValidationError/FreshnessViolation 直接上抛不进候选循环 | runtime/orchestration.py | 传错代码不再 AllSourcesExhausted | 低 |
| P0-11 | quotes_batch 单坏符号不中断整批；`_drive_depth` 改 threading.local | runtime/kernel.py, client/_mixin.py, sync.py | 10 只含 1 只非法符号 → 9 成功 1 not_attempted | 低 |
| P0-12 | `.day` sync 时间序：合并排序后重写 | sink/local_day.py | 补洞后文件严格升序 | 中 |

### P1 架构收敛（建议 3-6 周，需要设计评审）

| # | 事项 | 内容 | 验收 | 预估 |
|---|---|---|---|---|
| P1-1 | **常驻连接池** | `DirectProviderExecutor` 按 family 缓存 `TdxClient`，`close()` 统一收口；同步池心跳改惰性起跑（首连接才起） | `quotes_batch` 全程仅 1 次 Client 构造；M6 回收与熔断冷却实测生效；`Client.close()` docstring 重写 | 5-8 人日 |
| P1-2 | **async 战线二选一** | 方案 A（推荐）：内核提供 async 执行体直连 `AsyncTdxClient`，AsyncClient 不再 to_thread；方案 B：删除 AsyncConnectionPool/AsyncTdxClient，文档承认 AsyncClient=to_thread 并暴露线程池配置 | 全仓只剩一条 async 故事；池层行为矩阵测试（retry/compress/stats/测速/心跳租约逐项） | A: 15-25 人日 / B: 3-5 人日 |
| P1-3 | **composed 上移单内核** | composed 编排移入 UnifiedRuntime，内层走 `runtime.execute`，外层 budget 传入内层 deadline；raw-provider/event_source 提升为 QuerySpec 显式字段（不再只靠 Client.call 特判） | 内层成败进健康反馈；provenance 完整；一次 adjusted_bars 不超外层 deadline | 5-8 人日 |
| P1-4 | **provider 身份修复** | Provenance 增加 `upstream` 字段（meta.source 透传）；derived/builtin 改能力名表+生成器（删 ~950 行手抄）；README/ARCHITECTURE 写清聚合 Provider 边界 | `provider=derived` 的结果可读出真实出数源是 sina | 3-5 人日 |
| P1-5 | **WebQuoteClient 降级处置** | 降级链标 legacy 默认 fail-fast，跨源只走 FallbackPolicy；或 ARCHITECTURE 承认「门面层允许显式配置的有序降级」——二选一，消除两处矛盾 | `get_quotes(source=None)` 行为与文档一致 | 2-3 人日 |
| P1-6 | **auto 语义修复** | 删 execute_auto 重复记账；`_resolve_auto` 保留 `requested_provider='auto'`；local_vipdoc 排除逻辑下沉 `auto_candidates`；quotes_batch 收敛为一份实现 | auto 请求 provenance 与钉死 provider 的可区分 | 3-4 人日 |
| P1-7 | **字符集与 profile 探测合一** | 删 charset 独立探测（或降为 primitive 薄封装）；两套六步探测器抽公共打分内核 | 全仓一套探测优先级；文档权重从代码常量生成 | 5-8 人日 |
| P1-8 | **sources 三本账收敛** | 词表 quote/quotes 统一；默认顺序由 providers 声明派生；量纲系数只在 normalizer 一处（删测试例外清单）；host failover/`_get_json`/退避二次收口 | test_registry_consistency 覆盖 providers↔sources 两表 | 5-8 人日 |
| P1-9 | **流式 fail-closed 可达** | Stateful 流接 `max_attempts` 落 FAILED；订阅 key 换单调 seq；订阅表操作统一到 loop；WS close 同 loop 直 await | 永久失败流状态为 FAILED；退订重订不覆盖 | 4-6 人日 |
| P1-10 | **死 API 收口** | Goods/Ex/Mac 报价方法接 `_UNVERIFIED_STRUCTURED_BLOCK` 统一拦截，工厂不导出或标 experimental；quick.py 进程级 Client | 调用即炸的方法不再出现在公开面 | 1-2 人日 |

### P2 结构重组（建议 1-2 月，行为不变的结构调整）

| # | 事项 | 内容 | 预估 |
|---|---|---|---|
| P2-1 | query.py 拆分：`spec.py`（纯契约）/ `planning.py`（channel 选择与 auto）/ `fingerprint.py`；市场感知翻译写回 spec（`translated_from` 字段）；canonical channel 进 ChannelSpec；AUTO_PROVIDER 下沉断开 query↔runtime 环 | 8-12 人日 |
| P2-2 | `_mixin.py` 拆三文件（驱动器/标准族模板/扩展族模板）；`_t_bars` 分页状态机纯函数化；栈深常数改显式传入 | 5-8 人日 |
| P2-3 | `runtime_commands.py` 按域拆 cmds_kernel / cmds_hosts / cmds_web / cmds_market；`_cmd_*` 异常一律上抛交 `main()` 信封出口；补 `atst serve --ws` | 5-8 人日 |
| P2-4 | 会话面瘦身：`WebQuoteSession` 19 Mixin 按 Provider 域拆 3-4 个会话类；klines/history 路由改查 `MARKET_DEFAULT_CAPABILITIES`；fundflow 等域模块按 Provider 归组 | 10-15 人日 |
| P2-5 | 协议样板收敛：BaseParser `iter_fixed_records`；`_check_echo` 提公共；records.py 抽序列化基类；解析器→领域模型转换器集中 client 层一张表 | 5-8 人日 |
| P2-6 | 四面入参校验收口 `parse_query_call` 单一函数；流式校验收口 StreamPlanner；WS 顶层 message 与 MCP/HTTP 对齐；MCP 暴露 subscribe 或文档明示 | 3-5 人日 |
| P2-7 | Client 与 TdxClient 双 API 面：文档对照表 + `_call_core` 改显式映射（与 DIRECT_BINDINGS 同风格）；TdxClient 降级为「协议面」定位 | 2-3 人日 |
| P2-8 | codegen 定位二选一：让生成物成为 D→L2 落地路径，或明确降级为脚手架生成器并从架构叙事摘除 | 3-8 人日 |

### P3 工程化与文档（随时可做，与 P0 并行）

| # | 事项 | 预估 |
|---|---|---|
| P3-1 | README 三处「三级路由降级」改为 RankingStore 测速排序真实表述 | 0.5 人日 |
| P3-2 | 覆盖率门禁加 per-module 下限（<70% 的 19 文件优先 `_session_*` 与 observability 三导出器补离线单测）；`skip_covered=false` | ✅（2026-11-23 续批）：地板闸（逐文件防回归）+ 10 个最低文件补 121 用例（0.19–0.67 → 0.83–1.00），地板表按新实测重生成 |
| P3-3 | `all` extra 改自引用；`test-bridges` 加 fastapi 探测提示 | 0.5 人日 |
| P3-4 | CHANGELOG 与 git-cliff 二选一（建议 cliff 接管生成，`make gates` 加一致性检查） | 1-2 人日 |
| P3-5 | `get_config()` 加锁；bool 环境变量接受 1/0；CI 补 macOS 一格 | 1 人日 |
| P3-6 | 心跳命令纳入配置面；BinaryWriter 越界改校验；`Sink("csv")` 补分支；死代码清理（`_looks_like_uint16_date` 等） | 1-2 人日 |

### 里程碑与依赖关系

```
P0（止血，可全并行）──────────────────────────────► v2.1.x 补丁
 │
 ├─ P1-1 常驻池 ──► P1-2 async 二选一 ──► P2-2 _mixin 拆分
 ├─ P1-3 composed 上移 ──► P1-4 provider 身份 ──► P1-5 降级处置
 ├─ P1-6 auto 语义 ──► P2-1 query.py 拆分
 ├─ P1-7 探测合一 ──► P2-5 协议样板收敛
 └─ P1-8 sources 账本 ──► P2-4 会话面瘦身
P3 全程并行 ────────────────────────────────────► v2.2.0
```

### 风险控制

1. **行为变化必须过 golden**：P0-3（reader strict）、P0-4（BJ 路由）、P0-12（.day 重写）
   属外部可见行为变化，进 CHANGELOG 并在迁移文档标注。
2. **不变量测试不许降**：`tests/provider_isolation/`、`tests/architecture/` 的既有判据
   一律不删；新判据随 P0-9（审计闭环）、P1-2（池行为矩阵）增加。
3. **覆盖率门禁只升不降**：重构 PR 不得以 bypass `fail_under` 换绿（pyproject 注释已有此规）。
4. **协议冻结层不动**：codec/commands 账本结构不在本方案范围内重构，只做 P-4 tier 归一
   与 P-6 握手帧走 FrameSpec 这类局部修正。

---

## 5. 明确不做的事

- **不引入结果级缓存 / 负缓存 / 请求合并**：零缓存是公开承诺，健康反馈 TTL 已是例外，
  不再扩大。
- **不恢复多级降级路由**：v2.0 已物理删除，README 残留表述是修正对象而不是恢复对象。
- **不动协议冻结层大结构**：L1/L2/L3 三级解析接缝、85 条命令账本、61 个解析器的布局保持。
- **不追求 100% 覆盖率**：均值门禁改 per-module 下限即可，`tests/live/` 网络路径维持豁免。
- **不重写 trading 面**：`labs/trade/` 是独立实验面，不接入内核，维持现状。

---

## 7. 落地状态（2026-11-22 全量落地批次）

状态口径：✅ 已落地且回归绿；⚠️ 部分落地（差额如实注明）；⏸ 延后（设计与差距记录在案）。
总回归：全量 pytest（`-m "not network"`）+ ruff + mypy 通过；`docs/generated/` 已重生成；
CHANGELOG 已记 Unreleased 批次；coverage 地板表已按新实测重生成。

### P0 正确性止血（12/12 ✅）

| 项 | 状态 | 备注 |
|---|---|---|
| P0-1 东财批量丢数 | ✅ | 逐标的请求聚合；全败才抛 |
| P0-2 代际守卫 | ✅ | sync/async request + async 心跳四处同修；回落口径=租约未建立才用槽位代际 |
| P0-3 reader strict | ✅ | strict 上抛 ProfileUndetectable；record_size 不一致拒绝；gpcw 不可判定报错 |
| P0-4 BJ 路由与错误归类 | ✅ | 缺省 bars 转 Web 源；ValidationError(E1010)；边界测试同步更新 |
| P0-5 WS 真丢帧 | ✅ | 溢出 future 逐个 cancel |
| P0-6 WS 共享 handler 串台 | ✅ | 内置 handler 逐连接克隆；自定义 handler 构造不兼容退回共享（历史行为） |
| P0-7 流式 stop 超时 | ✅ | fail-closed（_hard_stop 闸）；resource_lifecycle 门禁合规 |
| P0-8 sniffer 热路径 | ✅ | 默认关 + 环境变量开关与归档根注入 |
| P0-9 审计闭环 | ✅ | audit_direct_bindings + executor 方法存在性入启动审计；5 死声明删除（绑定 304→299） |
| P0-10 编排不吞参数错 | ✅ | ValidationError/FreshnessViolation 上抛；模拟「源不可用」的测试改传输类异常 |
| P0-11 批量单坏符号 | ✅ | kernel/Client 双入口同修（P1-6 后 Client 非 auto 委托 kernel） |
| P0-12 `.day` 时序 | ✅ | 乱序补洞全文件合并重写（原子 tmp+replace） |

### P1 架构收敛（9/10 ✅，1 项部分）

| 项 | 状态 | 备注 |
|---|---|---|
| P1-1 常驻池 | ✅ | 按族常驻 + 惰性心跳 + close 统一收口；close_chain/family_transport 判据同步 |
| P1-2 async 二选一 | ⚠️ | 方案 B 的**文档与定位收口**已落地（唯一 async 故事 + legacy 横幅 + Deprecated）；物理删除延后：AsyncTdxClient/AsyncConnectionPool 是 wheel 发布契约钉住的公开 API（test_release_async_wheel_contract + 39 个测试文件引用），breaking 移除需独立废弃周期 → v2.3 候选 |
| P1-3 composed 上移 | ✅ | execute_subquery 单入口；预算继承；裸执行器注入退回直执行 |
| P1-4 provider 身份 | ✅ | Provenance.upstream + derived 生成器化（1105→~150 行，注册表产出零 diff）；builtin 本就表驱动未动 |
| P1-5 WebQuoteClient 降级 | ✅ | 缺省单源 fail-fast + WEB_FACADE_LEGACY_DEGRADE 警告；显式多源才降级 |
| P1-6 auto 语义 | ✅ | requested_provider='auto' 进 provenance；记账去重（kernel_records 判据）；local_vipdoc 排除下沉；quotes_batch 收敛 |
| P1-7 探测合一 | ✅ | charset 薄封装；profile/scoring.py 公共打分内核；死分支修复 |
| P1-8 sources 账本 | ✅ | 词表同义映射、默认序派生、Referer 进 SourceSpec、host failover ×3 / `_get_json` ×4 / 自写退避 ×2 全部收口（节奏均值不变）；**量纲系数单源化已于 2026-11-24 完成**（`atst/sources/_scale.py` 唯一事实源 + 三重守卫，逐位零行为变更）|
| P1-9 流式可达 | ✅ | Stateful 默认 max_attempts=30 落 FAILED；WS 订阅表收口 loop；close 同 loop await |
| P1-10 死 API 收口 | ✅ | 统一拦截 + experimental 标注；quick 进程级 Client |

### P2 结构重组（7/8 ✅，1 项延后）

| 项 | 状态 | 备注 |
|---|---|---|
| P2-1 query.py 拆分 | ✅（2026-11-23 续批）| 771 行拆为 `atst/query/` 包（spec/planning/fingerprint），对外导入面不变；4 处路径级 AST 门禁随包结构重设计（kernel_config_wiring ×2、official_runtime ×2、namespace 白名单）；canonical 表**留在 planning.py 单处**（tencent-bars 周期分支搬进 ChannelSpec 会把一个事实拆成两处，评估后不搬）；顺带落地 R1（options 明文复用表删除）。断环确认为函数级惰性 import，维持现状 |
| P2-2 _mixin 拆分 | ✅ | 1091→52 行组合点 + 三模块；`_t_bars` 状态对象 + 纯判定函数早已落地，2026-11-24 补直接单测后按「已验证落地」结案（原"未做"记号作废）|
| P2-3 cli 拆分 | ✅ | 四域模块 + 信封出口统一 + serve --ws |
| P2-4 会话面瘦身 | ✅ | 19 Mixin → 4 域类（dir() 184 项不变）；路由查表；sina 资金流归组；history 复权回退保留 provider 级决策（绑定层无 adjust 元数据，纯查表需改声明层） |
| P2-5 协议样板收敛 | ✅ | iter_fixed_records 35 处机械替换；_check_echo 提共享；records 序列化基类；变长 leb128 记录不属该样板保留 |
| P2-6 四面校验收口 | ✅ | parse_query_call 单一函数 + 流式校验收口 + WS message 对齐 + MCP 文档明示 |
| P2-7 双 API 面 | ✅ | _call_core 显式映射；对照表随文档更新 |
| P2-8 codegen 定位 | ✅ | 降级为脚手架生成器，与「协议全覆盖」叙事脱钩 |

### P3 工程化（6/6 ✅，P3-2 含一项未做）

README 三处降级表述 ✅；覆盖率地板闸 + skip_covered=false ✅（2026-11-23 修复键名空转并补测 121 用例；2026-11-24 再补 7 个 <70% 模块至 0.99–1.00，存量 <70% 文件只剩两个 `__main__.py`）；extras 自引用 + test-bridges 探测 ✅；CHANGELOG 双工具收敛 ✅；get_config 锁 + bool env + macOS 矩阵格 ✅；heartbeat_cmd 配置键 + BinaryWriter 校验 + Sink csv + 死代码清理 ✅。

### 本批次新增的公开契约

- `Provenance.upstream` 字段（wire 随之下发）
- `providers.preference_order()` 唯一偏好序读数
- `runtime/execute_capability.adapter_class_for()`（factory 字段唯一读取口）
- `UnifiedRuntime.quotes_batch(workers=)`（并发参数）
- `QueryPlan.requested_provider`（运行时事实，不入相等性）
- 环境变量：`ATST_PROTOCOL_SNIFF` / `ATST_PROTOCOL_SNIFF_DIR`
- `atst serve --ws`；配置键 `core.heartbeat_cmd`
- 新告警码 `WEB_FACADE_LEGACY_DEGRADE`

### 落地批次委派对账（9 路子任务，2026-11-22）

落地批次由 9 路并行子任务承担：**9 settled / 9 usable / 0 failed / 0 stopped / 0 empty**——
没有失败、中止或空返回的子任务，不存在需要重做的缺口范围。逐路对账如下（「未覆盖子范围」
一栏如实登记各路回执自报的未做项，均已并入 §7 的状态口径或延后清单）：

| 子任务 | 承载项 | 结果 | 回执自报的未覆盖子范围 |
|---|---|---|---|
| ① 探测合一 | P1-7 | 完成 | 无未覆盖项（charset 薄封装 + scoring.py 公共内核全落地）；import 置底 noqa 已注释说明 |
| ② _mixin 拆分 | P2-2 | 完成 | **`_t_bars` 分页状态机纯函数化未做**：模板产出 reader 而非 bytes 迭代器，纯函数化需重写解码路径、超出「无行为变化拆分」边界 → 并入延后清单 |
| ③ cli 拆分 | P2-3 | 完成 | 无未覆盖项（serve --ws、信封出口、四域拆分全落地）；文档命令表判据已退役故无需同步文档 |
| ④ derived/provenance | P1-4 | 完成 | **builtin 未改生成器**（builtin 本就表驱动、体量小，收益低）；**README/ARCH 聚合边界说明未做**（超出其文件授权）→ 由子任务⑨补齐（ARCHITECTURE §108 已核实在场） |
| ⑤ 会话面瘦身 | P2-4 | 完成 | **providers 声明层绑定未改指新路径**（`sources/__init__` re-export 兜底，声明层不在其文件授权）；**history() 复权回退保留 provider 级决策**（绑定层无 adjust 元数据，纯查表需改 providers 声明）→ 均并入延后清单 |
| ⑥ 协议样板收敛 | P2-5 | 完成 | **变长 leb128 记录循环保留**（无 remaining 守卫，不属定长样板，已有共享解码器）；client/_mixin 转换器调用点未动（该文件属子任务②） |
| ⑦ 常驻池+死API+双面 | P1-1/P1-10/P2-7 | 完成 | 无未覆盖项；回执报的两条全量 flake（half_open 并发探测、recv deadline）在负载下复现，隔离复跑绿，非本批引入 |
| ⑧ 流式+四面收口 | P1-9/P2-6 | 完成 | **MCP 流式走「文档明示」分支**（未新增 subscribe 工具——方案原文允许二选一，已在 `_tools_spec.py` 明示「流式仅 WS」）； prose 门禁 8 条 offender 属子任务⑦注释措辞，由主控修讫 |
| ⑨ 降级处置+文档 | P1-5/P2-8 | 完成 | 报备一项超范围最小改动：diagnostics.py 新增 `WEB_FACADE_LEGACY_DEGRADE` 枚举成员（实现「记 diagnostics 警告」验收所需）；ARCH 边界说明落在其范围内，已落地 |

### 落地批次委派对账（2026-11-23 续批，3 路子任务）

**3 settled / 3 usable / 0 failed / 0 stopped / 0 empty**——没有失败、中止或空返回的子任务，
不存在需要重做的缺口范围。逐路对账（「未覆盖子范围」为各路回执自报的未做项，均已并入
§7 状态口径或延后清单）：

| 子任务 | 承载项 | 结果 | 回执自报的未覆盖子范围与处置 |
|---|---|---|---|
| ① sources 传输底座二次收口 | P1-8 余项（F8/F9/F10） | 完成 | 无未覆盖项。过程中 9 个失败均位于并行编辑中的 `test_p3_2_*` 新文件（子任务②中间态），最终复跑全绿；新增结构断言 `test_transport_floor_secondary_consolidation` 锁定收口 |
| ② 低覆盖模块补测 | P3-2 余项 | 完成（0.19–0.67 → 0.83–1.00，121 用例） | **两条疑似真 bug 只报不修**（其授权即「报告不修」）：`macro_repo_rate` CSV 解析静默丢行/字段错位；prometheus handler 500 兜底经 `render()` 不可达（防御深度）→ 已登记延后清单待证 |
| ③ 声明层新路径 + 废弃周期 | P2-4 收尾 + P1-2 第一步 | 完成 | **eastmoney 5 处 fundflow 绑定未改**（类仍在 `atst/sources/fundflow.py`，改指会指向不存在的类——核实后正确维持）；遗留一条过时 docstring（sina/fundflow.py 称绑定仍指旧路径）→ 主控已修讫并复绿 |

主控自做项（P2-1 拆包 + 4 处门禁重设计 + R1、§7/CHANGELOG 更新、总回归）不在委派范围内；
其未覆盖子范围即 §7 已明示的项：量纲系数单源化、`_t_bars` 纯函数化、legacy async 物理删除
（v2.3）、待证项与上述两条新疑似 bug 的真机核验。

主控自做项（P0 全部、P1-3/P1-6/P1-8 部分、P1-2 文档收口、P3 全部）与总回归不在委派范围内，
其未覆盖子范围即上表之外的 §7 状态口径（P2-1 延后、P1-2 物理删除延后、P1-8/P3-2 部分项）。

### 2026-11-24 第三批：量纲单源化 + 覆盖率补测 + WS 订阅合流

收掉 2026-11-23 清单里剩下的三条结构性缺口：

- **P1-8 量纲系数单源化（原延后项，已完成）**：新增 `atst/sources/_scale.py` 作为
  价格 / 成交量 / 成交额换算系数的唯一事实源，三处维护点（`SourceSpec` 声明、
  `VolumeNormalizer` 应用、解析层内联）共 **110+ 个裸字面量**改为具名常量；
  刻意保留「除」而非改乘（`x / 100.0` 与 `x * 0.01` 可能差 1 ulp），故引入
  `CENT_INT_DIVISOR` / `MILLI_INT_DIVISOR` 两个派生除数，**逐位零行为变更**。
  守卫测试 `tests/sources/test_scale_single_source.py` 三重把关：常量值锚点、
  `atst/sources/**` 裸字面量扫描（AST 抹掉字符串，文档里的「×100」不误判）、
  normalizer/SourceSpec 系数闭集；`test_b8_scale_consistency.py` 的例外清单随之
  清空（机制保留，条目为空）。
- **P3-2 余项：7 个 <70% 模块补测**：`_session_baidu` 0.51、`_session_info` 0.54、
  `jsl/adapters` 0.59、`_session_boards` 0.59、`_session_market` 0.64、
  `_session_efinance` 0.64、`boc/adapters` 0.65 → 全部 **0.99–1.00**（90 个离线用例）。
  补测过程中发现并修复一条真 bug：`jsl/adapters.py` 对「合法 JSON 但顶层不是对象」
  （`[]` / `42` / `null`）走 `.get()` 抛 `AttributeError`，被基座改写成通用解析失败，
  丢掉「源已改版 / 需 cookie」的 E7030 信号——现显式 `Mapping` 守卫并归 `SourceDeprecated`。
- **F-8 WS 订阅合流（原未做项，已完成）**：`runtime_ws.py` 引入 `_SharedPoller`，
  键 = `(loop, client, 排序标的, provider, interval, diff_only, max_queue)`，等价订阅
  共享一条上游轮询、按连接逐帧 fan-out，引用计数归零才停流；后加入者回放最近快照，
  单订阅者首帧语义不变；某条连接投递失败只摘它自己。节奏参数进键是刻意的——把
  `interval=5` 挂到 `interval=1` 的轮询上属于行为变更而非去重。

### 2026-11-23 复审核查与二次修复（上一批）

对 §7 声称已落地的项做逐项代码复核，修复以下「声称已落地但实际缺失 / 漂移」的项：

- **覆盖率地板闸空转（真 bug）**：`FLOORS` 的键被写成 `atst/cli/cli/parser.py`、
  `atst/sources.sina/sources/sina/adapters.py` 这类与 coverage.xml（`source=["atst"]`，
  filename 为 `cli/parser.py`）永不相交的形状，回归判据对每个文件都 `continue`，
  闸门一条文件也没检查过。已按 coverage.xml 实测重写 236 条键，新增双向键集判据
  `test_floor_table_keys_match_coverage_manifest`，并补上文档引用的唯一写者
  `scripts/gen_coverage_floors.py`。
- **`macro_repo_rate` CSV 错位（真 bug，原延后项）**：空段过滤破坏位置映射——缺列
  静默丢行、字段左移错位，`parts[i] or None` 分支不可达。改为保留位置槽
  （`cells[i].strip() or None`），删死分支，补两条回归。
- **P0-11 语义校正**：坏符号状态由 `failed` 改为 `not_attempted`；`requested` 不再
  包含未发包的坏符号，与 Client 入口一致。
- **P0-10 余项**：调用方 `ValidationError`/`FreshnessViolation` 不再记 provider 失败，
  避免污染 auto 健康账本。
- **R-6/R-7/R-8/P-14**：`audit_runtime()` 记忆化；`query/spec` 收窄为 `SymbolError`；
  typed payload 保留全字段（「只发非默认字段」会让 capability contract 判
  `missing required argument`，该半项已回退并写明原因）；BatchResult 浅拷贝容器 +
  深拷贝 meta。
- **S-3/S-4/S-6/S-7/S-8/S-9**：`ut` 常量、`risk_scan` 收窄并告警、腾讯 high/low 缺失
  返回 NaN、新浪逐行容错、会话错误收窄、`klines()` 尊重 `sources` 配置。
- **T-4/T-5**：quick 入口复用进程级 Client；`quotes_snapshot` 默认跳过已知下线的
  0x054C，公开 `try_offline_snapshot` 显式 opt-in。
- **TDX 板块指数（新增可用能力）**：880xxx/881xxx 归沪市命名空间，裸码可直接
  `quotes`（2026-11-23 实测 `sh880001`/`sh881001` 有完整报价）；此前命中北交所旧段
  `"88"` 被 fail-closed。板块 K 线实测仍不可用（主站回错位记录），不伪造支持。
- **文档口径**：`docs/ARCHITECTURE.md` 绑定数 304→299。

复核确认**已真实落地、此前审计误报**的项：async stop 超时 fail-closed
（`streaming/base.py:737` 已置 `_hard_stop`，`test_async_stop_hard_stop.py` 覆盖）、
同步心跳代际锁内捕获（`transport/pool.py:1065-1075`）、WS 自定义 handler 逐连接克隆
（`runtime_ws_server._clone_for_connection`，鸭子类型按契约回退共享）、F-2 反压硬上界
（`streaming/engine.py:426-431`）。

### 2026-11-24 第四批：协议双源 + 待证线索收口

把 §6.4 与延后清单中**不需要 breaking 变更**的项一次性清掉：

- **P-4 tier 单一事实源**：`registry.py` 注册时从命令账本读取 `tier` 覆盖解析器类属性；
  账本内 49 行历史漂移的 `D` 更正为实际生效的 `L2`。注册前后逐解析器 dump 全量一致
  （61 个解析器零差异），新增 `test_p4_ledger_tier_authority.py` 锁定「账本是唯一权威」。
- **P-6 握手帧改由 FrameSpec 生成**：三帧不再手写裸 hex，改走 `build_request(..., spec=DEFAULT_7709_SPEC)`
  并用捕获的 seq；旧 magic 仅保留为 `_PINNED_*` 且在导入时断言逐字节相等——真实主站
  A/B 证据（帧 1+2 即足够、产品块全零才出 K 线）因此不会因重构被悄悄改掉。
- **async 序号竞态（待证）**：核查所有 `next_seq()` 调用点均在 `conn._lock` 内（含 ping/
  续帧/sweep），同一 loop 内无竞速；不加冗余锁，改以 spy 回归测试钉住「无锁调用即失败」
  与并发同 loop 序号唯一性。
- **`_t_bars` 纯函数化（原延后）**：核实状态对象 `_BarsPageCursor` 与
  `_absorb_bars_page` / `_bars_truncation_diagnosis` **早已落地**，缺的只是直接单测；
  补 `test_bars_pagination_pure.py`（翻页状态推进、短页/空页、截断诊断、零 I/O）
  后按「已验证落地」结案。
- **`to_dicts` 混合输入（待证）**：补 `test_to_dicts_mixed_inputs.py`，钉住 Quote/dict/
  其它 dataclass 混合序列的键值与惰性产出；实现本身无需修改。
- **README 规模数字（待证）**：现算 85 命令 / 61 解析器 / 217 capability / **299** 绑定，
  README 四处 304 已更正；新增上下文锚定的 `test_readme_scale_contract.py`。
- **docs/configuration.md 对账**：schema 6 段 21 字段全数在册，修掉两处文档漂移
  （不存在的测试文件名、"5 段"→"6 段"）；新增 `test_config_schema_doc_contract.py`。
- **docker-compose 真缺陷**：`ATST_LOG_LEVEL` / `ATST_CACHE_DIR` 是未注册的环境键
  （`config_from_env()` 会直接 `ConfigError`，容器起不来），已移除；同时修掉两个服务
  都占宿主 8000 端口、悬空卷与过时的 `version: '3.8'`，新增 `test_docker_compose_contract.py`。
- **如实保留的待证项**：OTLP JSON ↔ 官方 proto3 映射无法离线验证，已在文档中写明
  验证所需材料与步骤；`stream.max_attempts` 确认为构造器参数（默认 30）、**刻意不做成
  配置键**，已写入文档。BJ 0x052D / gpcw 文件头 / 29 字节旧布局仍是真机线索。

### 2026-11-24 第五批：本地真机接口 / 数据口径实测

用户要求「在本地真机测试详细接口和数据问题」，本批只动**真机可复现的缺陷**，不碰
v2.3 才做的 breaking 删除项。实测覆盖 TDX 行情主站连通性、公开 Client 面、
canonical 路由、Web 源量纲与 fail-closed 语义。

- **主站连通性**：可达 `119.97.185.59`、`159.75.29.111`、`124.71.187.122`、
  `122.51.120.217`；超时 `119.147.212.81`、`120.53.120.235`、`218.108.98.244`。
  可达主机上 0x0530 板块指数报价、`quotes` / `bars` / `quotes_batch` / `snapshot` /
  `finance` / `f10` / `security_count` / `stream` 及 capability 调用均能拿到非空信封。
- **真 bug ①：非沪深标的缺省 `quotes` 路由失败**。原先 `hk00700` / `usAAPL` 被翻译成
  `hk_quotes` / `us_quotes`，落进 `catalog`（`live=False`）通道，`currentness='live'`
  直接 E1010；北交所则钉死 TDX 未验证的 `market=2`。现在与 `bars` 一样保留 canonical
  `quotes` capability，只把缺省 provider 换成有 live quote 通道的 Web 源（腾讯优先）。
  显式 `provider=` 不介入，传 `tdx` 仍按原样 fail-closed。
  回归：`tests/query/test_non_shanghai_shenzhen_quote_routing.py`（离线路由）与
  `tests/live/test_non_mainland_quote_routing.py`（真机价格 / 量额 / 币种）。
- **真 bug ②：腾讯 canonical 报价把港美股当 A 股缩放**。经统一 `quotes()` 落到
  `TencentSource.parse()` 时，港股 `amount` 放大 10^4 倍、`volume` 放大 100 倍，
  隐含均价落在当日高低区间之外；`TencentExternalSource` 走的是另一套布局，两条路径
  曾不一致。新增 `_parse_external_quote()` 统一港美股字段布局（无 A 股 ×100 /
  ×10000），canonical 与迁移后的外部源共用，A 股缩放保持原样。
  真机复核：`hk00700` 341.6 HKD / 量 29,393,105 / 额 10,123,426,303.24；
  `usAAPL` 353.35 USD / 量 4,861,833 / 额 1,699,692,052，隐含均价均在高低区间内。
- **真机非缺陷、已如实记录**：北交所开盘前腾讯快照返回昨收价、量额 0、OHLC 0，
  这是上游口径，真机测试只钉价格与 live 通道，不拿港美股盘中量额口径去卡它；
  `security_list` 的离线命令号按设计 E3035 fail-fast；`f10()` 不给文件名属入参校验；
  板块指数 `880xxx`/`881xxx` 报价可用但 K 线记录布局不符预期，继续 fail-closed；
  东财 push2 各 host 在当前网络下均 `Server disconnected without sending a response`，
  属外部 WAF / 网络问题，仓库内无修复点。

校验：目标离线用例（query + sources）与网络标记用例全绿（live 套件 8 passed /
3 skipped，新增路由 4 例全过）；
全量确定性门禁 rc=0（ruff / format / mypy / 离线 pytest + coverage 90.32% /
bridges / golden / spec / adversarial / reachability / originality / benchmark /
interface 43 OK / docs-links 76 files / docs-check）。gpcw 文件头 / 29 字节布局 /
OTLP 映射仍需专门样本；BJ 0x052D 已于第六批真机证伪。

### 2026-11-25 第六批：L2 假阳性收口 + BJ 0x052D 真机结论

继续清延后清单里**不依赖新外部样本**的两项：

- **L2 启发式退化载荷假阳性（已修）**：对 `PROTOCOL_SPEC/_sniffer` 的 12 份自采集
  载荷逐份重跑 `infer_record_layout()`：`06b9` ×4、`000f` ×4（32B）、`0200` ×1
  共 9 份「全零 / 一个非零字段 + 大片零字段」此前全部拿 0.85 分被抬成 L2；
  `000f` 的 32B 载荷在加「单记录」特判后还会切成两条 16B 记录绕过特判，
  故规则按**抽样字段整体**判定而非只盯 `count == 1`。现在这类载荷统一降到
  0.10–0.34 并回落 L3，候选 notes 明示「记录字段近乎全零」。
  唯一保留 L2 的 `0530` 是 29B、含 `u32/f32/i32` 等多种字段的真实快照，
  不被误伤。实现改在 `atst/protocol/generic.py::_score_candidate`，
  回归 `tests/protocol/test_l2_degenerate_payloads.py`（合成样本 + 真机语料 + 不误伤）。
- **BJ 0x052D 真机已证伪（不再待证）**：在主机 `180.153.18.170:7709` 上先发三帧
  标准握手，再用 26B 请求体（category=4 / count=3）实测：同主机 `sh600519`
  的 `0x052D` 返回 **73B / 3 根日线**（请求帧格式正确），而 `bj833171` 在
  `market=2` 与 `market=0`、`bj430047` 在 `market=2` 三种组合下均只回
  **2B `00 00`（count=0）**。结论：主站对北交所代码不提供 7709 K 线，
  `bj` bars 继续走 Web 源是正确路由，`split_symbol` 的 `market=2` fail-closed
  保持不变；不为此改协议层。

校验：protocol 目标测试全绿；全量确定性门禁 rc=0（ruff / format / mypy / 离线
pytest + coverage 90.34% / bridges / golden / spec / adversarial / reachability /
originality / benchmark / interface 43 OK / docs-links 76 files / docs-check）。

### 2026-11-25 第七批：官方客户端本地数据实证

用户提供本机安装 `P:\stock\new_tdx64\TdxW.exe`，本批只读检查其数据目录，
不启动 GUI、不做客户端内存抓取：

- **`.day` 记录尺寸（原待证项，实证结案）**：`vipdoc/**/*.day` 共 **9507** 个，
  文件大小**全部是 32 的整数倍**；142 个同时被 29 整除只是 32 与 29 的公倍数
  （如 5568 = 32×174 = 29×192），不是 29 字节布局。抽样 `sh600519` /
  `sz000001` / `bj920036` 经 `detect_profile()` 均判 `record_size=32`、
  confidence=1.0，`DayBarReader` 解码正常。现版客户端无 29B 样本，
  strict 拒绝非 32B 的既有口径保持不变。
- **真 bug：日线末字段不是前收盘价**。此前 `DayBarReader` 把 28–31 字节
  uint32 直接 `/price_scale` 当 `extra.prev_close`。真机文件显示：个股
  `sh600519`/`sz000001`/`sh601318`/`sz300750`/`bj920036` 的 1284/1285 条
  恒定 `0x00010000`（按 ×100 读成 **655.36**），仅末条为 0；指数
  `sh000001` 尾字段出现 **25,167,389**（/100 = 251,673.89，而当日收盘约
  3464），与上一根收盘的匹配率 **0/1284**。因此该字段不是前收盘价，
  直接 flatten 会让 `adjust` 拿错误权威值。现在只在与当日 close 同量级
  （0.5–2.0 倍）且非哨兵时才保留，否则置 `None`，由 `adjust` 既有回退
  「上一根 bar 收盘」接管。回归补在 `tests/reader/test_formats.py`。
- **gpcw 文件头（仍缺样本）**：现版安装的 `vipdoc/cw/` 目录为空，
  全盘无 `gpcw*.dat`，头部假设继续待证，不据空目录推断。
- **板块 / 行业数据（可用于后续，不在本批接线）**：`T0002/hq_cache/tdxbk.cfg`
  是 GBK `类型|简称|全称|标记` 的 58 条板块名表（锂电池 / 特斯拉 / DeepSeek 等）；
  `tdxhy.cfg` 是 5670 条 `市场|代码|行业码|||细分码` 映射。说明本地客户端
  确实带板块/行业数据，且无需 GUI 抓取；`reader/formats.py` 文档提到的
  `block_gn.dat` / `block_zs.dat` 在本版未落盘。

校验：reader 目标测试全绿；全量确定性门禁 rc=0（ruff / format / mypy / 离线
pytest + coverage 90.33% / bridges / golden / spec / adversarial / reachability /
originality / benchmark / interface 43 OK / docs-links 76 files / docs-check）。

### 2026-11-25 第八批：本地板块/行业配置接线 + 复权文档收口

- **本地板块/行业配置只读接线**：新增 `atst/reader/tdx_config.py`，两个只读 reader：
  `BoardConfigReader` 解析 `T0002/hq_cache/tdxbk.cfg`（GBK `类型|简称|全称|标记`，
  真机 58 条；类型 1/2/3 = 概念/风格/指数），`IndustryConfigReader` 解析
  `tdxhy.cfg`（GBK `市场|代码|行业码|||细分码`，真机 5670 条；市场 0/1/2 =
  深/沪/北），代码经 `normalize_symbol` 转 canonical。两者只在 `atst.reader`
  模块面导出，不新增 Provider / capability，因此 217 capability / 299 binding
  的严格计数不变。字段数不符、未知类型/市场码、非法代码在 strict 下抛
  `FileFormatError`，非 strict 记告警跳过；不抓 GUI、不读内存、不含成分股或行情。
  回归 `tests/reader/test_tdx_config.py`（合成样本 + 真机安装 skipif 核对）。
- **复权文档实证收口**：真机复核 `Client.adjusted_bars("sh600519")` 的 qfq/hfq
  均返回 20 根 Bar，hfq 因子 `7.39106838` 与 FAQ 记录一致；`bars(adjust=...)`
  在 tencent 服务端复权可用，在 tdx 直接源 E1010 fail-closed。修正文档：
  `docs/api/interfaces.md` 补 `adjusted_bars` / `capital_changes` 一等公民方法行、
  `bars` 的 `adjust=` 别名、两条复权路径对照表（自算 vs Web 服务端），把「动态
  方法面已随 v2 删除」的过期说法纠正；能力发现面 216→217；`0x000F` 的 golden
  口径与 FAQ 对齐（请求体已修且单测钉住，账本仍 `verified=False`、无 golden 样本）。
  `docs/providers/derived.md` 删掉不存在的 `AdjustedBars`/`SyncReport`/
  `DailyEnriched` 类型推荐，示例改为可执行。`README.md` 补复权特性行与
  Client 快速开始示例。新增 `tests/compatibility/test_adjustment_doc_contract.py`
  钉住这些口径。
  校验：新增模块 97.96% line；全量确定性门禁见下方批次校验记录。

### 延后项清单（下一批候选）

1. **P1-2 物理删除 legacy async 栈**（DeprecationWarning 已启用；2026-11-24 已与用户确认
   **保留到 v2.3**——它是 wheel 发布契约钉住的公开 API，删除属 breaking 变更）。
2. 真机线索（仍缺样本）：gpcw 文件头（现版安装 `vipdoc/cw/` 为空）、
   OTLP JSON ↔ 官方 proto3 映射；L2 启发式在**更大未知命令样本**上的误报率
   （现有 12 份语料的退化假阳性已收口，见第六批）。`.day` 29 字节旧布局
   在现版客户端 9507 个文件中无样本，按 strict 拒绝结案（见第七批）。
3. prometheus handler 500 兜底经 `render()` 不可达——2026-11-23 复核确认是防御深度
   （provider 自身吞异常返回空串），非缺陷，无需修复。

（原「量纲系数单源化」「F-8 WS 订阅去重」「`_t_bars` 纯函数化」「P-4 tier」「P-6 握手」
均已结案，见上面两批；§6.4 的 README/配置键/docker-compose/to_dicts/序号竞态亦已收口。）

## 6. 附录

### 6.1 证据报告索引

| 分报告 | 范围 | 发现数 |
|---|---|---|
| [arch-review/runtime-contract.md](arch-review/runtime-contract.md) | runtime/ + query/result/batch + providers/ | 20 条（2 条实跑复现） |
| [arch-review/sources.md](arch-review/sources.md) | sources/ 69 文件 + providers 对账 | 22 条 |
| [arch-review/transport-client.md](arch-review/transport-client.md) | transport/ + client/ | 18 条 |
| [arch-review/streaming-faces.md](arch-review/streaming-faces.md) | streaming/ + server/ + cli/ + output/sink | 30 条 |
| [arch-review/protocol-reader.md](arch-review/protocol-reader.md) | protocol/ + codec/ + reader/ + profile/ + domain | 24 条 |
| [arch-review/engineering.md](arch-review/engineering.md) | config/observability/tools/CI/文档/卫生 | 11 条 |

### 6.2 高严重度问题速查（P0 全集）

1. 东财批量行情静默丢数 — sources/eastmoney/adapters.py:81
2. 连接池代际守卫被架空 — transport/pool.py:664（sync/async 同病）
3. reader 探测失败静默用默认档案，10 倍错价 — reader/formats.py:160
4. BJ 市场被拒且错误归类为可重试协议错 — client/core.py:192
5. WS 推送丢最旧帧不 cancel，反压不存在 — server/runtime_ws.py:593
6. WS 共享 handler 推送串台 — server/runtime_ws_server.py:62
7. 流式 stop 超时孤儿 worker / 泄漏 runtime — streaming/base.py:659
8. 协议 sniffer 挂分派热路径同步落盘 — protocol/registry.py:435
9. 字符集探测双实现优先级相反 — codec/primitive.py:558
10. 三方对账恒真式 + 死声明死 API — runtime/capability_audit.py:54
11. AsyncClient 假异步、真异步栈零调用 — client/api.py:823
12. 内核每请求现造池，池语义不成立 — runtime/executor.py:301
13. WebQuoteClient 降级链违背 provider-first — sources/__init__.py:419
14. composed 内层绕过单内核 — runtime/composed.py:92
15. derived 以自有身份代理 sina，信任边界失守 — providers/derived.py

### 6.3 规模数据备查

- 源码 61,766 行 / 223 文件；测试 54,477 行 / 321 文件；覆盖率 line 85.97% / branch 75.61%。
- 包行数 Top5：sources 18,436；transport 5,624；protocol 4,374；tools 4,234；providers 3,898。
- git 追踪 2,593 文件，其中 tests 占 84%；golden 1,847 个文件 1.9MB（样本即契约，刻意设计）。

### 6.4 委派对账与未覆盖范围

本次审查由 6 路并行子任务承担，**6 路全部完成且产出可用（6 settled / 6 usable / 0 failed / 0 stopped / 0 empty）**，无需要重做的空转范围。分工与产出对账如下：

| 子任务 | 产出报告 | 覆盖范围 | 汇入本文位置 |
|---|---|---|---|
| runtime-contract | [arch-review/runtime-contract.md](arch-review/runtime-contract.md) | runtime/ 全部 18 模块 + query/result/batch/typed_query + providers/ 声明层 | §2.1/2.3/2.6、§3.1、P0-9/10/11、P1-3/4/6 |
| sources | [arch-review/sources.md](arch-review/sources.md) | sources/ 69 文件（包根基座 + 九家 adapter + 域模块）+ providers 对账（282 条绑定全量核验） | §2.1/2.5、§3.2、P0-1、P1-5/8、P2-4 |
| transport-client | [arch-review/transport-client.md](arch-review/transport-client.md) | transport/ 11 文件 + client/ 8 文件 | §2.2/2.7、§3.3、P0-2/4、P1-1/2、P2-2/7 |
| streaming-faces | [arch-review/streaming-faces.md](arch-review/streaming-faces.md) | streaming/ 6 文件 + server/（HTTP/WS/MCP）+ cli/ + output/sink | §2.4、§3.4、P0-5/6/7、P1-9、P2-3/6 |
| protocol-reader | [arch-review/protocol-reader.md](arch-review/protocol-reader.md) | protocol/ + codec/ + charset/ + reader/ + profile/ + domain/ + universe/ + batch.py | §2.7、§3.5、P0-3、P0-8、P1-7、P2-5/8 |
| engineering | [arch-review/engineering.md](arch-review/engineering.md) | config/observability/feedback/tools + Makefile/CI/Docker/pyproject/契约测试/文档一致性/仓库卫生 | §2.8、§3.6、P3 全部 |

各分报告中的「待证项」是**子任务自己声明未验证的线索**，本文不将其当作已证实结论，也不纳入 P0 执行清单，仅登记如下供后续复核：

- composed 内层查询的实际耗时放大倍数（需真机 `adjusted_bars` 计时）；`create_source` 对四家 web provider id 的兼容面未逐家验证（runtime-contract §4）。
- `WebQuoteClient` 降级链在 CLI/HTTP 面是否仍被走到；东财 `_t` 缓存穿透参数是否有调用方传时间戳；`hk_quotes(provider=)` 与 `resolve_provider` 是否冲突（sources §4）。
- BJ 0x052D 是否真机可用（决定 P0-4 的两种修法二选一）；`to_dicts` 对混合 Quote/dict 输入的行为；异步池 `_ordered()` 无锁自增在多 loop 下的隐患（transport-client 待证项）。
- `DayBarReader` 是否假定 `.day` 严格升序（决定 F-7 是数据破坏还是仅乱序）；配置面是否有 `stream.max_attempts` 旋钮（streaming-faces §4）。
- gpcw\*.dat 是否带文件头；`.day` 29 字节旧布局是否在野；L2 启发式对真实未知命令的误报率；`Market` 三重定义的运行期影响（protocol-reader §3）。
- README 规模数字（85 命令 / 61 解析器 / 217 capability / 304 绑定）未逐一现算核对（有现成判据 `test_ledger_public_surface.py`）；`otel_exporter` OTLP JSON 与官方 proto3 映射兼容性；`docs/configuration.md` 全量键未逐格比对；`docker-compose.yml` 未深查（engineering §4）。

除上述待证线索外，六路子任务各自负责的范围均已产出带 `文件:行号` 证据的结论并被本文吸收；没有子任务失败、中止或空返回，不存在需要重做的缺口范围。
