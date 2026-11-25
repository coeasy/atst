# 流式与服务面审查（slug=streaming-faces）

审查范围：`atst/streaming/`（base.py / engine.py / state.py / stateful.py / push.py）、`atst/stream_contract.py`、`atst/server/`（runtime_http.py / runtime_ws.py / runtime_ws_server.py / serialization.py / wire_fields.py / mcp/）、`atst/cli/`（parser.py / runtime_commands.py / _common.py）、`atst/output/`、`atst/sink/`。
方法：通读全部列名文件 + 交叉核对 `atst/client/api.py` 的 `Client.stream` / `AsyncClient.stream`、`atst/error_envelope.py`、`atst/cli/__init__.py` 出口、`docs/ARCHITECTURE.md` §5/§6 声明。所有行号以当前工作区文件为准。

## 1. 结论摘要

整体置信：**高**（每条发现都有 当前文件:行号 的一手代码证据；涉及并发时序的条目标注为推演）。

1. **流式状态机的「fail-closed」在生产流上基本不可达**：`StreamLifecycle.fail()` 的主要触发前提是 worker 异常穿出 `_run`，但两套基类 `_run` 都吞掉一切 `Exception` 继续转；`StatefulQuoteStream` 又没有接 `ReconnectPolicy.max_attempts`，永久失败的流永远停在 `RUNNING`。【架构问题】
2. **stop 超时路径存在与注释自称已修复的 F-91/F-92 同型的孤儿 worker 竞态**：同步侧超时后关掉 owned runtime 但保留线程；异步侧 30s deadline 到点直接清 `_task` 并关 runtime，而 worker 可能仍卡在不可取消的 `to_thread` 取数上，醒来后 `_get_runtime()` 重建 runtime 无人回收。【细节问题，高严重度】
3. **背压三处声明、两处不成立**：`BackpressureQueue` 的 put 与 drain 在同一轮询线程内先后完成，`max_queue` 只在单轮内有效；WS `_schedule_push` 的「丢最旧帧」只删 future 引用不 `cancel()`，帧照样发送。【架构问题 + 细节问题】
4. **「WS 订阅表每连接私有」只在 `serve_runtime_ws(handler=None)` 时成立**；调用方传入 handler 时所有连接共用同一实例，`bind_connection` 互相覆盖 `_send`，A 连接的推送会打到 B 的 socket。【架构问题】
5. **四面翻译基本无执行逻辑（成立），但入参校验有第四、第五处重复**：`args/kwargs` 形状 + `reject_reserved_kwargs` + 路由字段拆包在 CLI/HTTP/WS/MCP 各写一遍；流式入参另有 `validate_subscription` / `Subscription.__post_init__` / `StreamPlanner.compile` 三重校验。provider 缺省四面不一致。【架构问题】
6. **runtime_commands.py 约 1300 行混 8 个域，该拆**；且 `_cmd_*` 一族绕过了 `main()` 的错误信封出口，跨面错误一致性在这半边不成立。【架构问题】
7. 错误信封跨面一致性总体成立（四面临时错误都过 `to_error_envelope`），残差在 WS 业务失败的顶层 `message` 是通用文案、以及 CLI `_cmd_*` 的裸 print。

## 2. 发现清单

| # | 严重度 | 类别 | 位置 | 问题 | 改法 |
|---|--------|------|------|------|------|
| H1 | 高 | 细节 | `atst/server/runtime_ws.py:566-596` | `_schedule_push` 注释称「超阈值丢最旧一帧」，实现只 `del pending[:overflow]` 删 future 引用，不 `cancel()`；协程已在 loop 队列里，帧照发、内存照涨，声明的慢客户端反压不存在 | 溢出时对被丢 future 调 `future.cancel()`，或改为有界 asyncio.Queue + 显式丢帧 |
| H2 | 高 | 架构 | `atst/server/runtime_ws_server.py:62` + `atst/server/runtime_ws.py:113-119` | 调用方传入 handler 时 `conn_handler = rpc` 所有连接共用；`bind_connection` 覆盖 `self._send`，后连连接抢走先前连接的推送出口，「订阅表每连接私有」（runtime_ws.py 模块 docstring 与 ARCHITECTURE.md §6 的声明）只在 `handler=None` 分支成立，且无任何「已绑定」防重入 | 传入 handler 时也 per-connection 克隆（共享 client），或 `bind_connection` 检测重复绑定即抛错 |
| H3 | 高 | 细节 | `atst/streaming/base.py:659-681` | `AsyncQuoteStream.stop` deadline 到点 `task.cancel()` 后 `break`，随后无条件 `self._task = None` + `_close_owned_runtime()`；worker 若卡在不可取消的 `to_thread` 取数上仍活着——正是同文件 docstring 声称已修复的 F-91/F-92（清句柄、关 runtime、可再 start 出第二 worker）。孤儿 worker 下一轮 `_get_runtime()`（base.py:573-583）会重建 runtime，无人回收 | 超时分支不清 `_task`、不关 runtime，改走 `StreamLifecycle.fail()`；`_get_runtime` 重建前检查 `_stop` |
| H4 | 高 | 细节 | `atst/streaming/base.py:441-460` + `base.py:377-386` | 同步 `stop` 超时分支保留线程引用（正确），但立刻 `_close_owned_runtime()`；孤儿线程下一轮 `_poll_once → _get_runtime()` 见 `_runtime is None` 又新建 UnifiedRuntime（`_owns_runtime` 仍 True），stop 已返回，这份池无人关闭 | 超时分支置 `_owns_runtime = False` 并 fail-closed，或给 `_get_runtime` 加「已停机」闸 |
| M1 | 中 | 细节 | `atst/streaming/base.py:402`、`base.py:596` | 订阅 id 用 `f"sub{len(self._subs)}"`：subscribe A、B → unsubscribe A → subscribe C 时 `len==1` 生成 `sub1`，静默覆盖仍在跑的 B。`StatefulQuoteStream` 已改用 `_subscription_seq`（stateful.py:82-86），两个公开基类仍是旧式 | 两个基类的 key 生成统一换成单调 seq |
| M2 | 中 | 架构 | `atst/streaming/base.py:313-318`、`atst/streaming/engine.py:419-423` | 背压队列 put 与 drain 在同一轮询线程同一轮内先后完成，队列深度≈单轮 symbol 数，`max_queue=1024` 实际永不触顶；跨轮背压不存在，慢回调只能靠阻塞轮询线程「反压」。与模块 docstring「bounded queues provide backpressure」的声明有落差 | 要么真分离生产/消费（独立 sender 任务），要么在契约里改口「max_queue 仅防单轮爆炸」 |
| M3 | 中 | 架构 | `atst/streaming/state.py:118-126` + `atst/streaming/base.py:493-503`、`stateful.py:160-175` | `fail()` 依赖 worker 异常穿出 `_run`，但两基类 `_run` 捕获一切 `Exception` 继续转；`StatefulQuoteStream` 也未接 `ReconnectPolicy.max_attempts`/`should_give_up`（`StreamEngine._run` 有，engine.py:558-575）。结果：永久失败的流状态恒为 RUNNING，状态机的 FAILED/CLOSED 语义在生产入口上近乎死码 | Stateful 流接 max_attempts，give up 时落 FAILED 并停 worker |
| M4 | 中 | 细节 | `atst/server/runtime_ws.py:132-146` + `runtime_ws_server.py:108-120` | `close()` 用 `run_coroutine_threadsafe(...).result(timeout=5)`；`__main__` 宿主在 loop 线程的 finally 里调它，协程永远得不到调度，白等 5 秒后 TimeoutError 被 suppress，残留流并未被停 | 检测 `self._loop is asyncio.get_running_loop()` 时直接 await，或把收尾改成纯同步旗标 |
| M5 | 中 | 细节 | `atst/server/runtime_ws.py:448-466`、`475-497` | `handle_message` 经 `asyncio.to_thread` 调度（runtime_ws_server.py:88），`_unsubscribe_from_params`/`_list_subscriptions` 在 worker 线程读写 `self._subs`/`_sub_providers`；`_subscribe`/`_on_quote` 在 loop 线程读写同一批 dict，无锁。`list` 迭代中遇并发变更可 RuntimeError（E9000） | 订阅表变更统一 `run_coroutine_threadsafe` 到 loop，或加锁 |
| M6 | 中 | 细节 | `atst/server/runtime_ws.py:408-411` | `interval` 用 `lo=0.0` 放行 0（深层 `validate_subscription` 再拒，错误面不一致）；`max_queue` 用 `int(... or 1024)` 把显式 `0`（本库语义=无限队列，base.py:389-395）静默换成 1024，违反 wire_fields.py 自家「不静默替换」口径 | 去掉 `or 1024`；interval 下界与契约统一为 `>0` |
| M7 | 中 | 架构 | `atst/cli/runtime_commands.py`（约 1300 行 / 1145 非空行） | 单文件混 8 个域：v13 数据命令（:56-268）、hosts 运维（:271-542）、serve（:544）、feedback（:568）、probe（:596）、web 迁移能力（:683-1038）、直连 market（:1040-1155）、universe（:1157-1272）。且 `cmd_*` 抛 TdxError 交 `main()` 出信封（cli/\_\_init\_\_.py:57-61），`_cmd_*` 一族自己 `print("错误：…")` + return 2，绕过信封与退出码契约 | 按域拆 cmds_kernel / cmds_hosts / cmds_web / cmds_market；`_cmd_*` 异常一律上抛交 `main()` 信封出口 |
| M8 | 中 | 架构 | `atst/cli/runtime_commands.py:544-566` + `atst/cli/parser.py:279-282` | `atst serve` 只拉起 HTTP 面；WS 服务面（四面之一）没有任何 CLI 入口，只能 `python -m` 或自写脚本调 `serve_runtime_ws`。交付面上第四面断链 | `atst serve --ws` / `atst serve --http --ws` 复用同一 Client |
| M9 | 中 | 细节 | `atst/sink/local_day.py:180-208` | `sync()` 断点判断在「本窗口新增已全部计入 `new`」之后才 break：窗口内早于本地末日期的补洞 bar 也被收进 `new`，`_append_bars` 直接 `open("ab")` 追加到文件尾，`.day` 文件时间序被破坏（reader 若假定升序即读错） | 合并排序后整体重写，或检测乱序时拒绝/重排 |
| M10 | 中 | 架构 | `atst/server/runtime_ws.py:421-447` | 每条 `subscribe` 各起一个 `AsyncStatefulQuoteStream` 独立轮询；同连接重复订阅同一标的（或跨订阅同标的）不合流、不复用，N 条订阅 = N 个轮询循环打同一个 runtime | 按symbol+provider 合流，或至少同 symbols 去重 |
| L1 | 低 | 架构 | `atst/server/runtime_http.py:128-137`、`runtime_ws.py:270-283`、`mcp/_tools_impl.py:88-106`、`cli/runtime_commands.py:89-106` | 「第四处重复入参校验」：`args` 必须是数组 / `kwargs` 必须是对象 / `reject_reserved_kwargs` / provider-channel-currentness 顶层拆包，四面各抄一遍，连错误文案都逐字相同 | 抽 `parse_query_call(params) -> (capability, args, kwargs, 路由字段)` 单一函数，四面调用 |
| L2 | 低 | 架构 | `atst/streaming/base.py:120-146`、`base.py:168-176`、`atst/stream_contract.py:69-100` | 流式入参三重校验：`validate_subscription`、`Subscription.__post_init__`、`StreamPlanner.compile` 各查一遍 interval/max_queue/symbols，错误类型还不一致（SubscriptionError vs ValidationError） | 校验收口到 StreamPlanner.compile 一处，Subscription 只保留防御断言 |
| L3 | 低 | 架构 | `atst/server/mcp/_tools_spec.py`（9 工具无 subscribe） | 流式只有 WS 面（和 CLI/Python）有；HTTP 与 MCP 面无任何流式入口。四面能力不对齐，MCP 客户端拿不到实时推送 | 至少在 MCP 暴露 subscribe/unsubscribe 工具，或文档明示「流式仅 WS」 |
| L4 | 低 | 细节 | `atst/server/runtime_ws.py:195` vs `mcp/_server.py:154-158` | 业务失败时 WS 顶层 `error.message` 是通用文案 "request failed"（细节在 `error.data.message`）；MCP/HTTP 顶层就是信封 message。同一失败三面人读位置不同 | WS 业务失败也把 `envelope.message` 提到顶层 |
| L5 | 低 | 细节 | `atst/output/__init__.py:464-481` vs `:483-530` | `Sink("csv").write()` 报「未知 sink 格式」，而 `write("x.csv")` 支持 csv——同一能力两个入口不同答案 | `Sink.write` 补 csv 分支 |
| L6 | 低 | 细节 | `atst/streaming/engine.py:249-260` | `BackpressureQueue.put` 用 `list.append` + `pop(0)`，溢出时 O(n)；容量即 max_queue | 换 `collections.deque(maxlen=...)` |
| L7 | 低 | 细节 | `atst/streaming/base.py:505`（`_poll_once` 末行） | 睡眠取「所有订阅的最快 interval」，慢订阅也按最快节拍收帧（interval 语义实为下限）；文档未写 | 契约里写明，或按订阅分档 |
| L8 | 低 | 细节 | `atst/streaming/stateful.py:106`、`:191` | `begin_start()` 返回值被忽略：并发 `start()` 时第二调用方拿到 False 仍走 `super().start()`，靠「线程/任务已存在」兜底；sync 侧线程未 `is_alive()` 前有窄窗可双 worker | 检查返回值，False 即直接 return self |

## 3. 专项核对结论（对应任务重点）

**StreamState 状态机竞态/泄漏**：状态机本身（state.py）加锁完整、终态不可逆（CLOSED/FAILED）、`fail()` 保留失败原因，这部分成立。落差在「外部前提」：worker 半死不会置 FAILED（M3）、stop 超时会造出孤儿 worker 与泄漏 runtime（H3/H4）、订阅 key 覆盖（M1）。重入方面 `begin_stop`/`close` 幂等成立；`begin_start` 返回值被忽略是唯一的重入窄窗（L8）。

**背压**：库内 `BackpressureQueue` 溢出丢最旧 + 锁外回调（engine.py:249-260，v1.2.0 死锁修复）本身正确；但 M2（同线程 put+drain 使 max_queue 失效）与 H1（WS 帧不真丢）使「背压」在两条真实链路上都不成立。

**WS 订阅表每连接私有**：`handler=None` 时成立（runtime_ws_server.py:62 三元分支）；传入 handler 时不成立且推送串台（H2）。测试 `tests/server/test_runtime_ws_streaming.py` 全部走 `handler=None` 路径，共享 handler 多连接场景无判据。

**四面只翻译不执行**：CLI `cmd_*` / HTTP 路由 / WS `_dispatch` / MCP handler 的主链路确实只翻译为 `Client.*`，成立。例外（均有意声明）：CLI 六支直连命令走 `get_client` 家族客户端（runtime_commands.py 模块 docstring）；`_cmd_universe` 直调 `atst.universe`（runtime_commands.py:1157 起）；`_cmd_hosts_*` 直调 transport 层。这些属于文档声明的豁免，不算暗执行，但与「四面全部只翻译」的绝对表述有出入。

**第四处重复入参校验**：存在，且不止第四处——query 形状校验四面各一份（L1），流式入参三份（L2），`reject_reserved_kwargs` 四面各调一次（同一函数，尚算单一出口）。

**runtime_commands.py 拆分**：应当拆。8 个域、约 1300 行、两套错误约定（M7），`parser.py` 依赖它的每一个 handler，改动任何一支命令都要重读全文件。

**错误信封跨面一致性**：临时错误（协议层/业务层）四面临时出口都挂 `to_error_envelope`（HTTP runtime_http.py:76-95；WS runtime_ws.py:613-648；MCP _server.py:141-176；CLI \_\_init\_\_.py:44-64），主体成立。残差：WS 顶层 message 通用化（L4）；CLI `_cmd_*` 裸 print 不出信封（M7）；MCP 工具级错误走 `isError: true` 的 text 内容而非 JSON-RPC error（_server.py:196-203，符合 MCP 惯例但与 WS 方法不存在即 -32601 的做法不同）。

## 4. 待证项

- `tests/streaming/` 与 `tests/architecture/` 对 H1（推送积压）、H3/H4（stop 超时）、H2（共享 handler 多连接）是否有判据：本轮未逐条读测试，若已有判据仍出现上述实现，说明判据只测了「不抛异常」未测「帧真的被丢/句柄真的没清」。
- `DayBarReader` 是否假定 `.day` 记录严格升序（决定 M9 是数据破坏还是仅乱序冗余）：未读 `atst/reader/formats.py`，建议核对。
- `atst.toml` / 配置面是否有 `stream.max_attempts` 之类旋钮可被 Stateful 流读取（决定 M3 的修法落点）：未读 config 段定义。

## 5. 建议的下一步（按投入产出排序）

1. 修 H1/H2/H3/H4 四个正确性问题（都是小 diff：cancel future、per-connection 克隆或绑定防重入、超时分支不清句柄不关池）。
2. 订阅表操作统一到 loop + key 生成换单调 seq（M1/M5）。
3. Stateful 流接 `max_attempts` 落 FAILED（M3），让状态机在生产入口真的可达终态。
4. 抽 `parse_query_call` 收四面 query 校验（L1），流式校验收口 StreamPlanner（L2）。
5. 拆 runtime_commands.py 为四个域模块并统一 `_cmd_*` 错误出口（M7），补 `atst serve --ws`（M8）。
