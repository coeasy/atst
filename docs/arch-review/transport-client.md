# 传输与客户端审查（slug=transport-client）

范围：`atst/transport/`（async_.py / pool.py / hosts.py / ratelimit.py / policy.py / sniff.py / speedtest.py / discover.py / base.py）与 `atst/client/`（api.py / _mixin.py / core.py / sync.py / async_.py / factory.py / quick.py）。
方法：一手代码逐文件通读 + 调用方反查（runtime/executor、streaming/base）+ parity 测试保证范围比对。置信等级标注在每条发现。

## 结论摘要

1. **sync/async 双轨已经漂移成两套系统**：`AsyncClient` 是 `asyncio.to_thread` 包装的同步内核（假异步），`AsyncTdxClient`/`AsyncConnectionPool` 是真异步但在生产代码零调用；两套连接池手抄同构，parity 测试只锁方法名与签名。【架构问题，确定】
2. **内核每请求现造同步池 + 心跳线程**：连接池的"池"语义在主链路上不成立，退化为一次性连接工厂。【架构问题，确定】
3. **代际守卫在 request() 失败路径被架空**（sync/async 同病），BJ 市场在 tdx 客户端层被拒且错误归类为可重试协议错。【细节问题，确定】
4. `_mixin.py` 1091 行是 trampoline 机制 + 全部协议模板的堆叠，`_drive_depth` 在同步并发路径无锁竞态；该拆，且 trampoline 本身值得重估。【架构+细节，高置信】

---

## 发现清单

| # | 类别 | 严重度 | 位置 | 一句话 |
|---|---|---|---|---|
| A1 | 架构 | 高 | atst/client/api.py:823 | AsyncClient 全部 to_thread 转调同步 Client，是假异步门面 |
| A2 | 架构 | 高 | atst/transport/async_.py:1 | AsyncConnectionPool 与 ConnectionPool 手抄同构 ~700 行，policy.py 只抽走熔断纯函数 |
| A3 | 架构 | 高 | atst/runtime/executor.py:301 | 每次内核查询现造 TdxClient+ConnectionPool+心跳线程，用完即弃 |
| A4 | 架构 | 中 | atst/client/_mixin.py:1 | 1091 行混合 trampoline 驱动器、标准族模板、四族子客户端模板 |
| A5 | 架构 | 中 | atst/client/api.py:1 | Client 与 TdxClient 是互不相通的两套同名 API 面 |
| B1 | 细节 | 高 | atst/transport/pool.py:664 | request() 失败路径传 `generation=slot.generation`，代际守卫恒真 |
| B2 | 细节 | 高 | atst/client/core.py:192 | split_symbol 拒绝 market=2，BJ 在 tdx 路径不可用且报可重试 ParseError |
| B3 | 细节 | 中 | atst/transport/pool.py:604 | sync/async 池语义漂移：retry/compress/PoolStats/后台测速/心跳租约 |
| B4 | 细节 | 中 | atst/client/sync.py:430 | quotes_concurrent 多线程无锁增减 `_drive_depth` |
| B5 | 细节 | 中 | atst/client/core.py:494 | `_bars_body`/`_quote_body` 无条件抛错，Goods/Ex/Mac 报价方法是必败死 API |
| B6 | 细节 | 中 | atst/client/quick.py:40 | quick 每次调用新建 Client→UnifiedRuntime→audit_runtime 全量对账 |
| B7 | 细节 | 中 | atst/client/_mixin.py:810 | quotes_snapshot 先发已知 offline 的 0x054C 再回退，每批多一次必败 RTT |
| B8 | 细节 | 低 | atst/transport/base.py:143 | 心跳命令 0x0002 未登记账本，配置面无 heartbeat_cmd 键 |
| B9 | 细节 | 低 | atst/client/api.py:912 | Client.quotes_batch 与 TdxClient.quotes_concurrent 两套并发批量重复 |

---

## 发现详述

### A1【架构|高】AsyncClient 是 to_thread 假异步；真异步传输是无人使用的平行宇宙

`AsyncClient` 的每个方法都是 `await asyncio.to_thread(self.client.<sync method>, ...)`（[api.py:863](../../atst/client/api.py#L863)、[api.py:889](../../atst/client/api.py#L889)），连 `close()` 也是 `await asyncio.to_thread(self.client.close)`（[api.py:833-837](../../atst/client/api.py#L833)）。它包装的是同步 `Client` → 同步 `UnifiedRuntime` → 同步 `TdxClient` → 同步 `ConnectionPool`。

真异步栈 `AsyncTdxClient` + `AsyncConnectionPool`（[async_.py:398](../../atst/transport/async_.py#L398)）在 `atst/` 生产代码中**零引用**：`grep AsyncClient|AsyncTdxClient` 在 `atst/`（排除 tests）无命中；内核唯一构造点是同步 `_tdx_client`（[executor.py:319-322](../../atst/runtime/executor.py#L319)）。流式异步面同样假异步：`AsyncQuoteStream._poll_once` 用 `asyncio.to_thread(self._get_runtime().quotes, ...)`（[streaming/base.py:717](../../atst/streaming/base.py#L717)）。

后果：
- 异步用户拿到的是"线程池 + 同步锁 + 每请求心跳线程"的组合，事件循环并发度受 `asyncio` 默认线程池（min(32, cpu+4)）封顶；
- `AsyncConnectionPool` 1329 行成为只被测试供养的第二实现，与同步池必然继续漂移（见 B3）；
- 两套"async"故事并存，维护者无法判断哪条是主链。

**改法**：二选一收敛。要么让 `DirectProviderExecutor` 提供 async 执行体直连 `AsyncTdxClient`（内核双轨），要么删掉 `AsyncConnectionPool`/`AsyncTdxClient`，公开承认 AsyncClient=to_thread 并把线程池尺寸/命名暴露成配置。当前"既写真异步又全走 to_thread"是最差组合。

### A2【架构|高】async_.py 与 pool.py 职责边界：同构靠手抄，共享件被当私有库

policy.py 自述"两池的差别只在 I/O 与并发外壳……判据必须是同一份实现"（[policy.py:1-16](../../atst/transport/policy.py#L1)），但实际只抽走了 `circuit_allows`/`record_failure`/`rank_slots`/`select_allowed_slot`。以下仍是两份手抄：

- 请求重试循环（tried 列表、pending_backoff 只对已试主机兑现、`max_attempts = max(max_retries+1, distinct_hosts)`）：[pool.py:604-712](../../atst/transport/pool.py#L604) vs [async_.py:864-960](../../atst/transport/async_.py#L864)；
- `update_hosts` 代际发布（复用/退役/`next_generation_host`）：[pool.py:780-830](../../atst/transport/pool.py#L780) vs [async_.py:1006-1090](../../atst/transport/async_.py#L1006)；
- `request_multi` 续帧合并与截断判定：[pool.py:846-940](../../atst/transport/pool.py#L846) vs [async_.py:1093-1200](../../atst/transport/async_.py#L1093)；
- `iter_frames` 脏 socket 弃连：[pool.py:947-1000](../../atst/transport/pool.py#L947) vs [async_.py:1203-1290](../../atst/transport/async_.py#L1203)；
- 心跳循环 + M6 空闲回收：[pool.py:1005-1100](../../atst/transport/pool.py#L1005) vs [async_.py:1296-1342](../../atst/transport/async_.py#L1296)；
- close 三段式：[pool.py:1145-1200](../../atst/transport/pool.py#L1145) vs [async_.py:1375-1420](../../atst/transport/async_.py#L1375)。

更直接的越界：异步池手写 `_select_allowed_slot`（[async_.py:755-770](../../atst/transport/async_.py#L755)），把 `exclude_hosts` 优先/回落逻辑又抄了一遍，而同步池走 `policy.select_allowed_slot`（[pool.py:597-599](../../atst/transport/pool.py#L597)）——正是 policy.py 注释里警告的"两份手抄的同一判据"事故模式。异步池还反向导入同步池的私有校验函数（[async_.py:73-77](../../atst/transport/async_.py#L73) `from .pool import (_require_bool_option, _require_positive_frame_limit, _require_request_timeout, retry_backoff_delay)`），说明共享层位置放错了。

**改法**：把 retry 循环、update_hosts 事务、multi/iter 帧、心跳、close 的**控制流**抽成与 I/O 无关的调度器（回调注入 sleep/lock/connect），或至少把 `_select_allowed_slot` 改回 `policy.select_allowed_slot`、把 `_require_*` 挪进 `_validation.py`。当前 async_.py 1329 行里真正 async 特有的只有 open_connection/wait_for/asyncio.Lock。

### A3【架构|高】连接池生命周期：内核主链上"池"不复存在

执行器每一跳都 `executor._client_session(executor._tdx_client(executor._hop_timeout(plan)))`（[execute_channel.py:57-63](../../atst/runtime/execute_channel.py#L57)），`_pool_client` 每次 `ConnectionPool(self.hosts, **settings)`（[executor.py:301-317](../../atst/runtime/executor.py#L301)），而同步池构造函数**立即起心跳线程**：

```python
if self.heartbeat_interval or self.idle_timeout > 0:
    self._start_heartbeat()          # pool.py:__init__ 尾部
```

（[pool.py:365-367 附近](../../atst/transport/pool.py#L365)，`_start_heartbeat` 定义于 [pool.py:1005](../../atst/transport/pool.py#L1005)）。`_client_session` 的 `finally: client.close()` 随即关池、join 心跳线程（[pool.py:1183-1191](../../atst/transport/pool.py#L1183)）。

即：**一次 `Client.quotes(["600000","000001"])` = 2 次 Client() 构造**（quotes_batch 逐 symbol）**×（resolve_hosts + 8主机×4槽 Slot 对象 + 心跳线程 start/join）**。`Client.close()` 的 docstring 自己承认"它关不掉连接池，因为默认内核根本不跨调用持有连接（每次取数现场构造家族客户端、finally 释放）"（[api.py:127-135](../../atst/client/api.py#L127)）。零缓存不变量并不要求零连接复用——TCP 连接不是缓存。空闲回收（M6, idle_timeout=300s）与熔断冷却（30s）在"用完即关"的池上永远不生效，等于整套机制只对直连 `TdxClient` 的用户存在。

**改法**：让 `DirectProviderExecutor` 持有按 family 缓存的 `TdxClient`（带 generation 热更新已有 `update_hosts`），`close()` 统一收口；`pool_settings_from_config` 已是唯一翻译点，不阻碍这件事。心跳线程改为惰性起跑（首连接才起，异步池已在 [async_.py:690](../../atst/transport/async_.py#L690) 这么做，同步池没有）。

### A4【架构|中】_mixin.py 1091 行该拆，trampoline 本身是复杂度源

文件同时承载：(1) trampoline op 定义与双驱动器 [(_mixin.py:106-270](../../atst/client/_mixin.py#L106))；(2) 标准族全部协议模板 `_t_bars`/`_t_quotes`/`_t_file_download`…（约 700 行）；(3) Goods/ExMarket/Mac/F10 族模板（约 200 行）；(4) 告警栈深换算（魔法常数 `_TPL_WARN_STACKLEVEL = 4`、`_SINGLE_HOP_STACKDEPTH = 6`，[_mixin.py:95](../../atst/client/_mixin.py#L95)、[_mixin.py:174](../../atst/client/_mixin.py#L174)）。

trampoline 用同步生成器 + `gen.throw` 在挂起点重入异常，代价是：栈深要靠手算常数伪装（注释自认"只对单跳模板成立"）；`warnings.warn` 的 stacklevel 与真实调用栈脱钩，重构一帧就全错；每个公开方法变成"壳(sync/async) + 模板(mixin) + 驱动器"三段跳。112 行的 `_t_bars` 里分页/漂移/空桩/越域四套判据揉在 while 循环 + 三个 flag 上（[_mixin.py:295-470](../../atst/client/_mixin.py#L295)），是全文件最难测的单元。

**改法**：按"驱动器 / 标准族模板 / 扩展族模板"拆三文件；`_t_bars` 的分页状态机提为纯函数（输入页迭代器，输出 bars+告警），sync/async 壳只做 I/O。栈深常数改为显式传入而非推算。

### A5【架构|中】Client 门面与 TdxClient 双 API 面

`atst.client` 同时导出 `TdxClient`（传输语义：`bars()` 返回 `list[dict]`，无 provider/currentness）与 `Client`（内核语义：`bars()` 返回 `QueryResult`，provider 路由 + provenance）。同名方法语义不同：`Client.quotes` 默认 `currentness="live"` 且走 planner；`TdxClient.quotes` 是逐只 0x0530。`Client._call_core` 甚至用 `getattr(type(self), capability)` 把核心能力表绑定在 Client 自己的方法名上（[api.py:341-380](../../atst/client/api.py#L341)）——新增核心能力必须同时在 Client 上长出同名方法，否则闭合点抛 ValidationError。这不是"只翻译不执行"，是门面与传输客户端互相渗透。

**改法**：公开文档与 `__init__` 导出里把 `TdxClient` 降级为"高级/协议面"，或在 Client 便捷方法 docstring 之外提供一张两面对照表；`_call_core` 的方法名即表应改为显式映射（与 DIRECT_BINDINGS 同一风格）。

### B1【细节|高】代际守卫在 request() 失败路径被架空（sync/async 同病）

`_mark_failure` 的契约是"仅当代际仍是当代时才推进运行期健康"（[async_.py:825-830](../../atst/transport/async_.py#L825) 注释），判据是 `_slot_is_current(slot, generation)` 比较 `slot.generation == generation`（[pool.py:543-551](../../atst/transport/pool.py#L543)）。但 `request()` 的失败路径传的是**读取时刻的** `slot.generation` 而不是租约捕获的 `generation`：

- 同步：[pool.py:664](../../atst/transport/pool.py#L664) `self._mark_failure(slot, exc, generation=slot.generation, conn=leased_conn)`（TdxError 分支）与 [pool.py:678](../../atst/transport/pool.py#L678)（Exception 分支）——而 `with self._lease(slot) as (conn, generation)` 明明拿到了租约代际；
- 异步：[async_.py:903](../../atst/transport/async_.py#L903) 与 [async_.py:918](../../atst/transport/async_.py#L918) 同样写法；
- 异步心跳同样：[async_.py:1337](../../atst/transport/async_.py#L1337)。

对照 `request_multi`/`iter_frames` 用的是租约 `generation`（[pool.py:963](../../atst/transport/pool.py#L963)）。读当前 `slot.generation` 使 `_slot_is_current` 在槽位未退役时**恒真**：`update_hosts` 把 `slot.host` 换成新 HostEntry 后，旧连接的迟到失败会记到新主站头上（failures++ / 熔断推进 / `live_rtt` 污染）。同步心跳反而正确（先捕获 `generation = slot.generation` 再 ping，[pool.py:1043](../../atst/transport/pool.py#L1043) 附近）——同一文件内两种口径。

**改法**：request() 两个 except 分支与异步心跳改传租约 generation；补一条"update_hosts 后旧代失败不记新 host"的回归测试（tests/transport/test_pool_generation_provenance.py 已有基建）。

### B2【细节|高】BJ 市场在客户端层被拒，且错误归类为可重试协议错

领域层宣称 BJ 可进二进制协议（[symbol.py:37](../../atst/domain/symbol.py#L37) "只有 SZ/SH/BJ 可通过 Symbol.tdx_market 进入二进制协议"；`tdx_market` 对 BJ 返回 2）。`QuerySpec.normalized` 的 bars 市场路由把 `bj` 留在 tdx（[query/spec.py:375](../../atst/query/spec.py#L375) 注释"TDX 二进制只认 SZ/SH/BJ"）。但客户端 split_symbol：

```python
def split_symbol(symbol: str) -> tuple[int, str]:
    market, code = to_tdx_market(symbol)
    if market not in (0, 1):
        raise ParseError(
            f"symbol-based 7709 request 尚未验证 market={market}；当前只允许 SZ/SH",
```

（[core.py:192-200](../../atst/client/core.py#L192)）。`ParseError` 继承 `ProtocolError`，后者 `default_advice = RetryAdvice(retryable=True, ...)`（[errors.py:266-270](../../atst/errors.py#L266)），于是 `Client.bars("bj430047")` 的结局是：planner 选 tdx → `_tdx_bars` → `client.bars` → E3040/HTTP 502 + 建议换主站重试。这是第 25 轮 G34 明确修过的同类错（"入参错被说成服务器故障"），split_symbol 漏改。同文件 `_standard_market_id` 却接受 `"bj"`/`2`（[core.py:54](../../atst/client/core.py#L54) `_PREFIX_MARKET = {"sh": 1, "sz": 0, "bj": 2}`）——同一模块内两个入口对 market=2 表态相反。

**改法**：若 BJ 0x052D 确未验证，改抛 `ValidationError`（E1010/422/不可重试），并把 `query/spec.py` 的 bars 市场路由把 `bj` 也翻译到 Web 源（与 hk/us 同待遇）；若已验证，删掉这道闸。二者必居其一，现在是"宣称支持、实现拒绝、错误归类再错一次"。

### B3【细节|中】parity 测试保证范围外的池语义漂移

`tests/client/test_sync_async_parity.py` 只锁公共方法名/签名/fake pool 数据形状；`tests/client/test_sync_async_surface_contract.py` 只锁参数契约。范围外的不对称（客户端层与池层各列）：

客户端层：
1. 同步 `_set_last_errors` 持 `self._errors_lock`（[sync.py:200](../../atst/client/sync.py#L200)、[_mixin.py:281-284](../../atst/client/_mixin.py#L281)）；异步覆写为无锁直赋（[async_.py:120-122](../../atst/client/async_.py#L120)），且 `quotes_concurrent` 收尾 `self.last_errors = collected` 绕过钩子（[async_.py:214](../../atst/client/async_.py#L214)）。
2. 同步 `quotes_concurrent` 用 ThreadPoolExecutor + `executor.map`（[sync.py:430-476](../../atst/client/sync.py#L430)）；异步用 `asyncio.Semaphore` + `gather`（[async_.py:160-216](../../atst/client/async_.py#L160)）。语义等价，但同步版踩中 B4 的 `_drive_depth` 竞态。
3. `open()` 语义：同步池在 `__init__` 起心跳；异步池在首次 `_get_conn_locked` 才 `start_heartbeat()`（[async_.py:688-690](../../atst/transport/async_.py#L688)）。`AsyncTdxClient.open()` docstring 承认这一点（[async_.py:127-133](../../atst/client/async_.py#L127)）。

池层（更实质）：
4. `ConnectionPool.request` 有 `retry: bool | None` 与 `compress: bool`（[pool.py:610-611](../../atst/transport/pool.py#L610)）；`AsyncConnectionPool.request` 两者皆无（[async_.py:378-382](../../atst/transport/async_.py#L378)）——异步面无法关闭重试、无法请求压缩。
5. 同步池有 `PoolStats`（requests/failures/retries/host_switches/circuit_skips，[pool.py:243-258](../../atst/transport/pool.py#L243)）且 `health()` 带 `stats` 键；异步池**没有池级 stats 对象**，`health()` 无 stats（[async_.py:1437-1466](../../atst/transport/async_.py#L1437)）。
6. 同步池有 R1 后台测速（连接失败≥阈值触发，[pool.py:520-570](../../atst/transport/pool.py#L520)）、`on_host_down` 回调、`keepalive`；`validate_sync_pool_only_options` 把三者钉成同步独有（[pool.py:203-207](../../atst/transport/pool.py#L203)）。异步池 bestip 只能靠用户手动调。
7. 同步心跳持 `slot.lock` 但**不加 lease**（[pool.py:1046-1060](../../atst/transport/pool.py#L1046) `with slot.lock: ... conn.ping(...)`）；异步心跳走 `_acquire_lease`（[async_.py:1318-1327](../../atst/transport/async_.py#L1318)）。退役/回收判定（`slot.leases`）在两边的可见性不同。

**改法**：把池层契约测试从"方法存在"升级为"行为矩阵"（retry/compress/stats/后台测速/心跳租约逐项对齐或在文档声明为有意差异）。当前 test_pool_circuit_parity.py 只对齐熔断，是不够的。

### B4【细节|中】`_drive_depth` 实例态在同步并发路径无锁竞态

`_drive`/`_adrive` 对 `self._drive_depth` 做 `getattr(...)+1` / `-1`，无锁（[_mixin.py:223](../../atst/client/_mixin.py#L223)、[_mixin.py:244](../../atst/client/_mixin.py#L244)）。`TdxClient.quotes_concurrent` 的 worker 线程各自调 `self.quotes(...)` → `_drive`（[sync.py:446-452](../../atst/client/sync.py#L446)）。后果限于 `_warning_stackdepth()` 算错 → 告警归属错帧，不损数据；但这是 C3"last_errors 加锁"同款问题在另一个实例态上的重演，且 `quotes_concurrent` 恰恰是唯一多线程入口。

**改法**：`_drive_depth` 改 `threading.local`，或并入 `_errors_lock` 保护区；异步侧本就单线程无需改。

### B5【细节|中】必败死 API 仍作为公开面导出

`_bars_body` / `_quote_body` 无条件抛 `NotImplementedFeature`（[core.py:494-519](../../atst/client/core.py#L494)）。因此 `GoodsClient.goods_bars/goods_quote`、`ExMarketClient.ex_bars/ex_quote`、`MacClient.mac_quote` 全部是"调用即抛"的方法，却仍在 `atst.client.__all__` 与工厂 `get_client("goods"|"ex"|"mac")` 里作为正常能力暴露（[factory.py:14-21](../../atst/client/factory.py#L14)）。`_guard_offline` 对 offline 命令 fail-fast 的设计（[core.py:436-460](../../atst/client/core.py#L436)）没有覆盖这组"layout 未锁定"的方法——它们在方法体第一行才炸，而不是在发包前统一闸。

**改法**：给这组方法接 `_guard_offline` 同款的统一拦截（`_UNVERIFIED_STRUCTURED_BLOCK` 机制已预留、现为空集，[core.py:428-431](../../atst/client/core.py#L428)），或在工厂/`__init__` 层就不导出，docstring 标 experimental。

### B6【细节|中】quick.py 每次调用重建 UnifiedRuntime 并全量审计

`quick.history/quotes/minute/trades` 每次 `with _client() as client:` 新建 `Client()`（[quick.py:40-42](../../atst/client/quick.py#L40)）→ `UnifiedRuntime.__init__` → `audit_runtime()`（provider 注册表 + 304 条绑定三方对账，[kernel.py:53](../../atst/runtime/kernel.py#L53)、[audit.py:18-36](../../atst/runtime/audit.py#L18)）+ `get_config().validate()`。`atst.quotes([...])` 一次 = 一次启动审计。`audit_runtime` 是启动期不变量检查，不是每请求开销。

**改法**：quick 模块持有一个懒初始化的进程级 Client（与 `_client()` 注释"唯一构造点：测试可替换"兼容，用模块级 `None` + 锁），或至少 `audit_runtime` 结果按进程 memo。

### B7【细节|中】quotes_snapshot 每批先吃一次已知必败的 0x054C

`_t_quotes_snapshot` 先发 `CMD["quotes_snapshot"]`（0x054C，账本 STATUS_OFFLINE），仅靠 `_OFFLINE_FALLBACK_OK` 放行（[core.py:426](../../atst/client/core.py#L426)、[_mixin.py:810-850](../../atst/client/_mixin.py#L810)）。注释承认"每次批量尝试都按已知会失败处理，真实数据来自逐只 0x0530 回退"。在生产默认配置下这是**每 80 只标的一次**的确定超时/失败 RTT，然后才进 `quotes` 逐只。0x0530 本身又是一次只能查一只（[client/__init__.py:78-80](../../atst/client/__init__.py#L78) 注释）。

**改法**：默认跳过 0x054C（配置/参数显式开启才试），或把"账本 offline"直接编译进客户端分支而不是运行时撞墙再回退。

### B8【细节|低】心跳命令绕过配置面契约

`DEFAULT_HEARTBEAT_CMD = 0x0002`，注释承认 0x0002 未登记在 7709 账本、配置面没有对应键（[base.py:143-155](../../atst/transport/base.py#L143)）。ARCHITECTURE.md 的"配置面即执行面契约：Config 里每个键都被内核读取"在传输层破缺：`heartbeat_cmd` 只能构造参数传入，`atst.toml` 写了不生效。

**改法**：`pool_settings_from_config` 增加该键，或在 config schema 显式拒绝。

### B9【细节|低】两套并发批量行情

`Client.quotes_batch`（逐 symbol QuerySpec、支持 auto 换源、ThreadPoolExecutor，[api.py:246-320](../../atst/client/api.py#L246)）与 `TdxClient.quotes_concurrent`（纯 TDX 逐只 0x0530、线程池，[sync.py:430](../../atst/client/sync.py#L430)）解决同一需求的两半。内核路径用户拿不到后者，直连 TdxClient 用户拿不到前者。

**改法**：合并为内核 quotes_batch 的 tdx 通道实现复用 `quotes_concurrent` 的 worker 形状。

### 心跳与断线重连（专题小结）

- 重连是**隐式**的：`_get_conn_locked` 发现 `not connected` 才 `connect()`（[pool.py:395-400](../../atst/transport/pool.py#L395)）；请求中死亡 → 异常 → `_drop` 弃连 → 下一请求重建。没有独立重连 backoff/抖动，重试语义完全寄生在 `RetryAdvice` + 池级换主机上。
- 心跳只做探活与熔断推进，不做"半开连接 repair"：ping 失败 → `_mark_failure` → 弃连。同步心跳与 M6 回收共用一条线程（[pool.py:1005-1013](../../atst/transport/pool.py#L1005)），`heartbeat_interval=0` 时回收仍跑（F-88 修复已在）。
- 异步池心跳起跑点在首次建连（[async_.py:688-690](../../atst/transport/async_.py#L688)），同步池在构造——见 B3#3。若采纳 A3 的常驻池方案，同步池也应改为惰性起跑，否则常驻后 8×4 槽每 30s 全表 ping 是新增负载。

---

## 待证项

1. **BJ 0x052D 是否真机可用**：B2 的两种修法取决于协议事实。查 `PROTOCOL_SPEC/7709/0x052D*` 与 golden 样本是否含 bj 市场；查 `tests/client/test_bj_symbol_protocol_boundary.py`（存在，31 行）的实际断言。
2. **`to_dicts` 对混合 Quote/dict 输入的行为**：`_t_quotes_snapshot` 的 `out` 混装 Quote 与 dict（[_mixin.py:816](../../atst/client/_mixin.py#L816) 注释自认），`_emit(out, "dict")` 依赖 `to_dicts` 对两者都产 dict——未读 `domain/models.py` 实现确认。
3. **`Client.quotes_batch` 在 `provider="auto"` 时每 symbol 走 `_execute_auto`** 的实际开销（每个候选一次完整 TdxClient 构造），未实测。
4. **异步池 `_ordered()` 无锁自增 `_rr`**（[async_.py:656-663](../../atst/transport/async_.py#L656)）：asyncio 单线程内无 await，理论原子；但与同步版 `_ordered_slots` 持池锁（[pool.py:377-386](../../atst/transport/pool.py#L377)）不对称，是否在未来多 loop/线程化下成隐患，未验证。

## 建议的下一步

1. **先做 A3（常驻池）**：收益最大（去掉每请求线程 + 让 M6/熔断/连接复用真正生效），且 `pool_settings_from_config`/`update_hosts` 代际机制现成。
2. **同步修 B1 + B2**：都是几行的改动，但一个是正确性（代际）、一个是宣称与实现的落差（BJ）+ 错误归类。
3. **A1/A2 二选一立项**：要么内核双轨化（async executor），要么删除 AsyncConnectionPool。拖着两套实现是持续的漂移税（B3 列的 7 项差异只会变多）。
4. **_mixin 拆分（A4）**与 trampoline 重估可放到池化改造之后；先落"驱动器/模板分文件 + `_t_bars` 状态机纯函数化"即可止血。
