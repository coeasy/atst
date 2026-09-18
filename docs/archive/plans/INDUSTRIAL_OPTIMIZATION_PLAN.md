# tstdx 工业级深度审计与优化改进计划（v2 · 终版）

> **审计对象**：tstdx v1.1.0 全主体（85 命令账本 / 68 解析器 / 双实现传输 / 三通路门面 / 14+ Web 源 / 三服务面 / 存储配置 / CLI / 工具链）
> **审计方法**：六域深审（协议编解码 / 连接与传输 / 流式与客户端 / 门面与 Web / 存储配置与域模型 / 服务面与工具，全部完整重跑）+ AST 可达性分析 + 对抗 payload 实测（9 畸形字节 × 85 命令）+ **高危发现逐条人工复验（实测/读码/逻辑证明三级）**
> **审计日期**：2026-09-02
> **证据等级说明**：`实测`=本会话运行时复现或对抗命中；`复核`=审计员直接读码确认；`报告`=域 agent 证据（动手前须二次验证）。本版已吸收全部复核修正——**初版报告中的 9 项误报已剔除并在附录 B 留痕**。

---

## 结论先行（回答：主体流程联通？孤儿？死循环？合理？）

**① 主体联通性：主干健康，但有两条"断头路"。**
- 主链路 client→pool→transport→codec→registry→parsers 与三通路门面、服务面消费链完整。
- **断头路 A（实测）**：`AsyncUnifiedQuoteAPI` 公开异步门面 **10 个方法全部运行时崩溃**——`asyncio.to_thread(self._SYNC.quotes, list(symbols))` 把**实例方法当静态函数桥接**，`list(symbols)` 绑定到 `self`，真实调用必抛 `TypeError: missing 1 required positional argument`；且 `aquery` 把它折叠成 `success=False` 的静默失败面。测试绿灯的原因是打桩恰好 `staticmethod(...)` 替换了被桥接方法——**测试桩掩盖了产品缺陷**。
- **断头路 B（实测）**：按推荐安装 `tstdx[web]`（httpx 可用）后 `build_client` 返回 `HttpxClient`，而它未实现 `post` → 人气榜全链（facade/session/cli/MCP）`NotImplementedError` 必崩。

**② 死循环：协议原语与传输主路径无死循环（明确排除项）；1 个条件性无限阻塞。**
三处 `while True`（varint/leb128 encode/decode）均有单调推进与长度/移位钳制；全部 count 驱动循环受 `remaining` 守卫或整除约束；对抗实测 3583 组合 0 挂死（最慢 3.4s 为畸形输入慢解析，非驻留循环）。例外：`RateLimiter/TokenBucket.acquire` 的 `while True` 在 `tokens > capacity/burst`（当前调用点未触发）或 **`rate≤0`** 时永无满足条件——条件性死等，且 web `fetch()` 调用不带 timeout。

**③ 孤儿逻辑：真孤儿 8 + 半孤儿/孤悬 8（详见 §4）**，集中模式为「Tier D 批次模块：已导出、未接线、未验证」（adjust/profile.detect/presets/native/telemetry/stats/feedback 三件套/observability 三 exporter/_async_bridge/requests.py/_generated.py）。其中 `_async_bridge` 属**已坏孤儿**（`run()` 对常驻 loop 再 `run_until_complete`，100% 抛 `RuntimeError`；正确姿势 `run_coroutine_threadsafe`）——修复接线或删除，修复前严禁文档引导。

**④ 主体合理性：分层、故障转移、fail-safe 边界、洁净室合规门禁（capture 双标志 AND 语义与时区计算经复核**正确无懈可击**）都是工业级水准。但存在四类系统性裂缝，构成本计划主线：**
1. **并发模型系统性缺口**（最高危）：同步池 `slot.lock` 只覆盖建连不覆盖请求期（`Slot.busy` 是未完成的 checkout 协议残迹）；异步连接锁被 `ping`/`request_multi` 续帧/`iter_frames` 三处绕过；心跳与在飞请求共享 socket 无协调。`quotes_concurrent`（1.1.0 主打并发）默认 8 线程打 4 槽 → 帧交织从理论变为可达路径，且 request_multi/`check_seq=False` 路径**无 seq 校验 → 静默数据污染**。
2. **符号/市场编号「单一事实源」名存实亡**：全库实测存在 **5 套**解析旁路（domain.symbol / requests.split_code_market+infer_market / sources._split_for_cache / reader._guess_market / profile.Market.CODES），北交所 0 vs 2 双轨、push 侧 bj→1 三义（复核确认）、`000300` 中证指数被推断为深市、`560530`/`900901`/`118000` 在两条链给出**相反市场号**——同一标的经不同入口发往不同市场，是数据正确性的最大公约数风险。
3. **可观测性「建好了没插上」**：metrics 主路径零埋点（`tstdx_request_total` 恒空）、config 的 observability/feedback 段是无消费者摆设、**Histogram 分桶非累积 + `le_+Inf`≠count（复核确认）**——即使接上，分位数三出口（Prometheus/OTLP/StatsD）全算错。监控不崩溃、只错一半，是最隐蔽的一类缺陷。
4. **文档承诺 > 实现接线**：指数 K 线 `index_bars()` 的 ctx 开关生产链永不传递（复核确认，静默脏数据管线）；「重试退避」不覆盖网络层异常；「背压/断线补数/StreamEngine 退避」未接入生产路径；`route` 参数在 11 个门面方法上形同虚设；ws 订阅登记后无人推送。

**⑤ 修复优先级共识**：门面 async 桥接 + httpx post + 并发三连 + config 环境解析（实测级证据）构成「第一批」——都是小改动、大收益、可当日回归。

---

## §1 P0 清单（按修复批次分组）

### F0 批次：1.1.0 已发布公共 API 的运行时缺陷（全部实测，最高优先）

| # | 等级 | 位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| F0-1 | **实测** | `facade/async_api.py:57-85` | 异步门面把实例方法当静态桥接 → `quotes/bars/finance/minute/capital_changes/trades/query/arun/aquery` 运行时必 `TypeError`；`aquery` 静默折叠为 `success=False` | `__init__` 持 `self._sync = UnifiedQuoteAPI(**kwargs)` 桥接**实例方法**；`aquery` 经 `_sync` 实例属性查找；**补不打桩端到端回归**（假传输而非假门面方法） |
| F0-2 | **实测** | `web/base.py:196-226` + `web/hot_rank.py:79` | `HttpxClient` 缺 `post()` 覆写（基类 `NotImplementedError`），httpx 环境人气榜必崩（波及 facade/cli/MCP 全入口） | `HttpxClient` 补 `post()` 对齐 `UrllibClient.post` 签名；CI 加 httpx/urllib 双栈冒烟矩阵 |
| F0-3 | **实测×2** | `config/loader.py:155-163, 130-134` | ① `TSTDX_RATE_LIMIT_*` 段名被 `split("_",1)` 切成 `"rate"` → load_config 直接 ValidationError（唯一带下划线段名永远无法表达）；② `"1"/"0"` 先被解析为 bool → `TSTDX_CORE_MAX_RETRIES=1` 等数值字段崩溃「必须是数值收到 bool」；③ 任意未知 `TSTDX_*` 硬失败无逃生门 | 段名按 `_SUBCONFIGS` 最长前缀匹配；bool 只认 true/false/yes/no/on/off，纯数字先 int/float；未知段告警不崩溃（或白名单逃生门） |

### F1 批次：并发与流式（C 组）

| # | 等级 | 位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| C1 | 复核 | `streaming/__init__.py:193-199, 308-311` | QuoteStream/AsyncQuoteStream 以订阅原串查 qmap（键=响应回声裸码）→ **带前缀/后缀符号静默零数据**；docstring 示例与 CLI `tstdx stream` 踩雷；测试 fake 用订阅符号充当 code 掩盖失配 | `split_symbol` 归一化后作查找键与 `_last` 键；fake 改回声裸码 |
| C2 | 复核 | `transport/pool.py:220-241, 294-296` | `slot.lock` 只覆盖建连，`conn.request` **锁外**执行；`Slot.busy` 全仓从未使用（checkout 协议残迹）；`quotes_concurrent` 默认 8 workers > 4 slots → 同 socket 帧交织；心跳持锁 ping 与在飞请求交叉；`request_multi` 续帧/`check_seq=False` 无 seq 校验 → 静默污染 | 连接级租约锁覆盖 request 全生命周期（或落实 `Slot.busy` 借出/归还）；心跳纳入同锁「仅空闲才 ping」 |
| C3 | 复核 | `client.py:330,382,387`（+673/707/712） | `quotes()` 每次调用重置 `self.last_errors=[]` × 并发 worker 再调 → **错误记录确定性蒸发**；`__init__` 未声明该属性（首访 AttributeError，cli getattr 兜底是症状） | 错误收集调用局部化随结果返回；`__init__` 显式声明；异步镜像同修 |
| C4 | 复核 | `streaming/__init__.py:119-129 vs 174-176` | `subscribe()` 无锁改 `_subs`、`_run` 快照读；dict 迭代异常**杀死流线程且完全静默**（线程无顶层兜底）；异步版 `on_error` 无 suppress | subscribe 加锁 + `_run` 外层兜底捕获走 `_dispatch_error` |
| C5 | **复核** | `transport/async_.py:310-331 vs 259` | 异步 `ping()` **完全绕过 `conn._lock`**（request 持锁）：写读交错 + `next_seq()` 锁外撞号；异步心跳连 slot 锁都没有 | ping 全程 `async with self._lock`；心跳协程与请求路径统一锁 |
| C6 | 报告 | `transport/async_.py:537-546, 568-572` | async `request_multi` 续帧/`iter_frames` 不走连接锁；`except TdxError: break` **不 `_drop`**（同步版会弃连）→ 超时后 reader 缓冲残留半帧污染该槽下一请求（经典半包）；`iter_frames` 结束不弃连；且 async 版不检查 `_closed`（close 后新建 socket 无人关，fd 泄漏） | 续帧纳入锁 + seq 校验；TdxError 一律弃连；补 `_ensure_open` 对等检查 |
| C7 | 复核 | `web/base.py:265-272` + `transport/ratelimit.py:109-121` | `RateLimiter.acquire(block=True)` 无绝对 deadline（rate≤0 被 `max(rate,0.1)` 掩盖后永不满 token → **条件性无限阻塞**）；TokenBucket `tokens>burst` 同样永等（当前调用点未触发，公共 API 面） | deadline 兜底；入口校验 `tokens<=burst`；rate≤0 显式报错 |

### F2 批次：协议数据正确性（P 组）

| # | 等级 | 位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| P1a | **复核(实测接线缺失)** | `std7709.py:465,534` + `client.py:311` + `facade/market.py` | `index_mode` 靠 ctx `index=True`，但**全库 grep 证实生产链从不传**（仅 native 自测与 docstring）；`index_bars()` 原样转调 `bars()`。若指数布局确如解析器自述「尾部多 4 字节涨跌家数」，第 2 根起 datetime/差分链整体错位 → **静默脏数据管线**（布局声明本身需 golden 指数样本定标） | `index_bars()` 显式传 ctx；解析器内加涨跌家数哨兵自动探测；补一条真机指数 golden |
| P1b | 复核 | `registry.py` 边界 | 对抗实测：`goods.py`（×7 cmd TypeError）、`mac.py:61/92`（ValueError 负 count）、`std7709.py:647/657/682`（ValueError）等**原生异常逃逸**——非 `TdxError` 子类，client/流式 `except TdxError` 接不住 → 畸形主站响应杀死轮询线程（叠加 C4） | **dispatch 边界统一收口**（try/except 包 ParseError 保留 cause）+ 负 count/`count*rec>remaining` 钳制。一处改动覆盖全部逃逸面 |
| P1c | 报告 | `std7727.py:112-118`、`mac.py:95-99` | 0x0104/0x1300 差分还原**缺 running base**（每条独立），与自家「同构 0x052D」声明矛盾（0x052D/0x0202 均跨记录基准）——若同构成立第 2 根起价格数量级错误（两命令从未真机确认，属 ⚠️ 占位类） | 统一为 0x052D 基准或显式改注 per-record absolute；列入「待样本锁定」专项 |
| P1d | 报告 | `std7709.py:331-339` vs `:310-317` | `build_realtime_quote_body("600000")` doctest 与 `quote_request_market` 反转语义互斥（`infer_market=1`→应 `00` 字节，doctest 写 `01`）；仓库无 `--doctest-modules` → 错误 doctest 不在 CI 暴露，**污染「实测锁定」证据链** | 跑一次 doctest 裁决（000651 条目与反转一致，疑 600000 笔误）；CI 开 `--doctest-modules` |
| P1e | 报告 | `std7709_extra.py:157-174` | 0x0FC5/0x0FC6 记录长度**三处矛盾**：docstring 28 / `RECORD_SIZE=17` / 逐字段消费 15——守卫按 17 门槛、消费按 15 步进，第 2 条起 2 字节/条错位 | 对齐 golden 后三数一致；加「声明 vs 消费」一致性 lint |

### F3 批次：门面与 Web（W 组）

| # | 等级 | 位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| W1 | **实测** | `web/fundflow.py:323` | `po=1 if ascending else 1`——**两分支恒 1**，排序方向参数完全失效（静默数据顺序错误） | `po=0 if ascending else 1`（以真实接口语义为准）+ 单测锁方向 |
| W2 | **实测** | `tstdx/__init__.py:65-67` | `__all__` 声明 `facade/observability/streaming` 但既未导入也不在 `_LAZY` → `import tstdx; tstdx.facade` AttributeError（包属性访问承诺破裂） | 入 `_LAZY` 或 `__init__` 尾部惰性 import 注册 |
| W3 | 报告 | `web/base.py:379-422` | `fetch` 重试**不覆盖网络层故障**：`client.get` 抛的 `WebSourceError(URLError)`/`ReadTimeout` 直接穿透——断连/超时这类最典型瞬态故障零重试，与 docstring「重试退避」不符；`_request_text` 同 | 传输异常纳入可重试集合；区分「传输失败/解析失败/源下线」计数 |
| W4 | 报告 | `web/base.py:333,416-421` | `max_retries` 无钳制：负值 → `range(0)` 跳过 → `assert last_exc is not None` 裸 AssertionError；无上限 + 退避 `0.5*2**attempt` 无 cap；404/400 确定性失败也重试 | `max(0,min(x,5))` + `delay=min(delay,8)` + 可重试状态码集合 {403,429,5xx} |
| W5 | 报告 | `web/base.py:353-355` + `facade/api.py` 静态通路 | 限流桶**实例级**：门面每次调用新建源实例 → 令牌桶每次满血复活，防封 IP 形同虚设 | 按 source_name 进程级单例桶 |
| W6 | 报告 | `web/adapters.py:234-241` | MinuteSource `len(parts)<3 continue` 后取 `parts[3]` off-by-one → 恰好 3 列行 IndexError（非 TdxError 绕过全部降级包装，中断多源降级链 `web/__init__.py:322-329` 只捕 TdxError） | 守卫对齐消费宽度；降级链捕获集合扩至 `Exception` 分类记日志 |
| W7 | 报告 | `web/facade.py:1054` | `WebQuoteSession.longhu` 缺 `@staticmethod`：实例调用把 session 绑进 `date` 参数（当前仅类级调用侥幸未炸） | 补装饰器；同文件全扫 |
| W8 | 报告 | `web/hot_rank.py:118-123` | `int(r.get("rk",0))` 不防 JSON null（`int(None)` TypeError；rk/rc/hisRc 同） | `_i(r.get(...))` 式兜底 |
| W9 | 报告 | `web/fundflow.py:194-203` | `fetch_rows(market="bse")`：f13=0 一律标 `sz` 前缀 → 北交所标的错误市场归属（需复核东财 f13 对 BSE 的取值） | BSE 过滤结果单独判定前缀 |
| W10 | 报告 | `web/base.py:403` | `fetch()` 统一 GBK 解码：东财 UTF-8 JSON 经降级链末端触达时中文名字段 mojibake（结构侥幸不坏） | 按源声明编码；`text(enc)` 参数化 |
| W11 | 复核 | `facade/api.py:159-174` | `_try_routes` 只重抛**最后一路由**异常（tdx 真因丢失、无 chain）；且无 sticky/熔断：主站宕机每次付满全链路超时 | 聚合 `route_errors` 进 context；失败计数熔断 + 成功钉住 |
| W12 | 复核 | `facade/api.py:205-337` | `route` 参数在 snapshot/minute/trades/finance/capital_changes 等 **11 个方法形同虚设**（声明但直连 tdx）；`route="web"` 落入误导错误 | 统一走 `_try_routes` 或收缩签名承诺 |
| W13 | 报告 | `facade/api.py:245-249` | bars 双通路语义漂移：tdx 不复权、降级 web 强制 `qfq` 且丢 `start`——**同一调用价格口径随网络状态漂移** | web 兜底透传 adjust/start 或显式拒绝并报错 |
| W14 | 报告 | `web/market_stats.py:249-255`、`web/boards.py`（3 处 startswith/2 处 int 解析）、`web/longhu.py:171-178` sort、`web/corporate.py` secid 推断、`web/history.py` float32 NaN | 一批「上游 None/短行/非数字」崩溃点与错位（boards is_member/初报行号有误**已剔除**，以本表为准逐一复核） | 逐源防御批 + 四板斧罐头用例（缺字段/None/短行/非 JSON） |

### F4 批次：服务面（V 组）

| # | 等级 | 位置 | 问题 | 修复方案 |
|---|---|---|---|---|
| V1 | 复核 | `observability/prometheus_exporter.py:157` | `serve()` 默认绑 **0.0.0.0 无鉴权**（对比 HTTP/WS 服务面默认 127.0.0.1），指标含内部维度；单线程 HTTPServer 慢客户端阻塞 | 默认 127.0.0.1 + 显式 opt-in 绑公网 + ThreadingHTTPServer |
| V2 | 复核 | `integration/http_server.py:331-342,410-421,64-68` | `/query`+`/tasks` 无鉴权**任意客户端方法派发**（可 `close()` 打瘫共享池、`request()` 透传原始命令）；TaskStore 无任务数/结果体上限（提交风暴=线程爆炸+内存 DoS）；`_LazyClient.__getattr__` 返回任意属性闭包 → `fn is None` 永假，404 语义失效 | 方法白名单 + 任务上限 + 鉴权依赖前置 + `__getattr__` 存在性探测 |
| V3 | **复核** | `observability/metrics.py:180-186` | Histogram 分桶**非累积**（命中首个桶即 break）+ `le_+Inf` 只计超全部有限桶的值——Prometheus 分位数与 OTLP/StatsD 导出**全链路静默失真**（已亲读确认 break 语义） | 去 break 向更大桶累计；`le_+Inf == _count`；补「累积性」单测（分位数黄金值断言） |
| V4 | 报告 | `integration/ws_server.py:262-267` | asyncio 单循环内**同步阻塞** TdxClient 调用——一次 TDX 超时+换主站重试卡死全部连接（head-of-line） | `asyncio.to_thread` 包裹 handler |
| V5 | 报告 | `cli.py:176-183` | `quotes_snapshot` 全失败（空行+last_errors 非空）→ **exit 0 报「成功 0 条」**（与 C3 联动：错误还会丢） | exit 判定纳入 last_errors；stream/snapshot 同修 |
| V6 | 报告 | `integration/{http,ws,mcp}_server` 错误面 | 对外响应泄露 `{type(exc).__name__}: {exc}` 内部细节（含 struct.error/UnicodeEncodeError 等）；MCP/HTTP `count/size` 未钳制（>65535 触发 struct 系异常）；`render_prometheus_client` 多标签二次注册**必崩**；statsd 把累计 Counter 当 delta 推送 | 对外 code+安全 message，细节留日志；入口 `min(max(n,1),N)`；collector 循外创建；statsd 推增量 |
| V7 | 报告 | `integration/ws_server.py:249-268` + mcp `_write_loop` | `WsConfig.max_message/path` 两字段完全未接线（配置静默失效）；subscribe/unsubscribe 是**死存储**（无推送循环消费）；mcp `_write_loop` while 后不可达代码 | 接 `max_size=`/path 校验；订阅推送二选一（实现或砍）；死码清除 |

### F5 批次：传输异常面（T 组，承自 v1 版，等级不变）

- T1（实测）`transport/base.py:291-296` TimeoutError 跨版本分支 → WriteTimeout/ReadTimeout advice 死配置；显式 `except (TimeoutError, OSError)`。
- T2（实测）解析器原生异常逃逸 → 并入 P1b dispatch 收口。
- T3（实测）mac 系畸形输入 2.0-3.4s 慢解析 → count 钳制 + max_bytes。
- T4（报告）`pool.py:403 vs 423` 注释「部分数据不丢弃+告警」与实现「`got<need` 整体清空 payload」矛盾，且 **transport 全域零日志** → 实现回归注释或注释回归实现；接 `logging`（模块级 logger）。
- T5（报告）`base.py:382-383` connect TOCTOU 双建连泄漏；`async_.py:577-595` 心跳在被弃 conn 上重建 socket（孤儿活跃连接）；`async_.py:451-455` **异步限流"试两次即放行"形同虚设**；`async_.py:459` `get_event_loop()` 弃用 + async 心跳默认不启动与 sync 不对齐。

---

## §2 P1 清单（隐患收敛，批次 G）

### 符号/市场编号统一专项（本审计最大结构性发现）
1. **五套解析旁路收编**：`domain.symbol` 为唯一事实源；`requests.split_code_market`+`infer_market`、`sources._split_for_cache`、`reader._guess_market`（`lstrip("sz")` 字符集误用 + "9" 前缀把 920001 北交所误判沪市）、`profile.Market.CODES` 全部改委托或建立映射表。
2. **北交所 0 vs 2 双轨裁决**（协议链 0、文件/profile 链 2、push.py 1——三义实测确认）：以 0x0547/0x0FB4/文件目录三场景分别定标入 PROTOCOL_SPEC；真机无 golden 前禁止混用。
3. `infer_market` 缺段（501/502/506/516/517/560-563/9xx B股/118 转债）与 `000300/000905` 中证指数误判深市（`_SH_INDEX_MAX_TAIL=100`）——两链对 560530/900901/118000 给出相反市场号，「帧合法但内容错误」场景。
4. `client.py:891-893` `_quote_body` 反转逻辑第二份内联拷贝且外推到 0x0203/0x0105/0x1301 未验证族（`1-market` 对 market≥2 产负数字节）；`quotes_snapshot` 内联版无 80 只上限且 >255 抛 struct.error 绕过回退设计。

### 连接与传输
5. 心跳持 `slot.lock` 做网络 IO（锁内 ping 尾延迟分钟级放大；挪锁外+租约锁配套）。
6. `pool.close()` 与在飞 request 竞态（循环内复查 `_closed`）；close 不 join 心跳/不 await cancel。
7. `tried_hosts` 只记录不规避、失败主机无跨调用冷却窗口、score 封顶并列退化轮转——故障主机被全客户端反复再选。
8. `ping()` 无 magic/max_frame 校验（错位流下垃圾 zip_size 长读）；`iter_frames` 裸 write+drain 无超时。
9. `ratelimit` SessionRateLimiter 等待期间时段切换不重选档；`HostEntry.failures` 非原子；`speedtest.connect_ms` 把握手往返计为 TCP 建连（口径失真）；`RankingStore.save` 固定 `.tmp` 多进程互踩。
10. `sniff.py`：families 三段覆盖式死代码（含自我承认 bug）、锁外读 `_rings` 竞态、`attach` 挂 async 对象静默失效；`_async_bridge.close()` 必白等 5s（无哨兵）。

### 门面与 Web
11. `_failures` 无按类分桶（解析 bug 与源下线混淆累积）；`SourceDeprecated` 对 Eastmoney 系自定义取数路径（5 份 `_get_json` 拷贝绕开 fetch）**永不触发**——「下线检测」承诺对这半边实现名存实亡。
12. `_get_json` failover 逻辑 5 份近似拷贝各自漂移 → 上收 `_EastmoneyJson` 基类。
13. 周期/参数静默回退群：`SCALES.get(period,240)`（week/month→day 数据）、`PERIODS.get(...,"day")`、`_MKLINE_PERIODS.get(...,"m5")`、Eastmoney 未知 adjust→qfq——未知值应显式报错。
14. 资源泄漏：`web/__init__.py:386/403`、`facade.py:1143` 源实例不 close（httpx 下每次泄漏连接池）；`corporate.py:647` 运行时临时替换 `self.report` 非线程安全。
15. `sources` 降级链三缺陷：kline 空结果短路（`is not None` vs quotes 的 `if data:` 不一致）；cache 分支绕 `_safe_run`（损坏 golden 样本炸穿链）；`build_router` 吞一切异常静默默认路由。
16. `registry` L1→L2 降级不传 ctx、L2 行字段与模型映射错配 → **降级静默产出全 0 Bar**；`count or _HEURISTIC_MAX_BARS` 请求 0 根被改写 1000；跨模块同名解析器幂等顶替失效。
17. 解析器截断静默丢行无 warning（对照 SecurityListParser 有自洽校验——守卫纪律不一致）；`CapitalChangesParser` 尾记录裸 struct.error；`QuotesSnapshot/ExBatchQuote` 内层 `except Exception: break` **吞掉 fatal IntegrityViolation**（违反「致命不降级」契约旁路）。
18. `prober.py` 契约矛盾：docstring 称不抛异常但 `_guard_offline()` 盘中 raise；`rate_limit=0` → sleep ≈31 年；`probe_range` 区间无上限（36h）；`reader/profile "net_bars"` 死档案与 golden 矛盾（10 倍价差点）。
19. `adjust.py` 缺 prev_close 静默忽略现金红利（纯派息因子恒 1 → 除息日虚假跳空）+ 全链无测试无 golden 对拍；`volume` 截断应 round；`to_adjusted` 未透传 round_price。
20. `calendar` 未覆盖年份静默「无节假日」（2027 元旦=交易日；ratelimit 限流档已被错档污染）；`set_holidays` 覆盖后 `is_estimated` 仍 True。
21. `detect.py` top 候选重复插入致 top-2 探测实为同一候选测两次（第二候选永不评估）；无 price_scale 探测步骤（×1000→×100 高置信误判）；presets 先验加权是死代码（`hint_market: pass`）；`match_preset` 市场不符仍返回首候选 + doctest 必失败。
22. `native.py` 双契约破洞：`output=` 参数原生路径不生效、`lot_factor` Python 回退丢弃——原生可用与否改变同一调用语义。
23. `i18n` vs `primitive` 两套字符集探测优先级相反（生产走 primitive）；`_SCALARS` 初报误案**已剔除**（实测数字解析正常，真缺陷=F0-3 两项）。
24. 服务面长尾：`metrics.render` 标签值未转义；`otel _time_nanos_to_str` docstring 19 位写 10 位；`_parse_labels` 恒返 None（statsd 丢全部标签维度）；`snapshot()` 直读 `_series` 泄锁；`UserStats` 环形缓冲死分支 → `_latency_samples` 无界增长（**逻辑证明**：`idx % max` 恒 `< max` → else 覆盖分支不可达）；`telemetry.events()` 内层 dict 共享引用假拷贝；`reporter` 脱敏漏 key、`sorted` 混合类型外溢。
25. 工具链：`golden_expand` sha256 校验被 `_mini_yaml` 跳嵌套行**结构性旁路**（手改 payload+删 meta 即过 verify——「样本即契约」防篡改破口）；`check_originality --fix` 不校验原许可即补盖 MIT 头（方向性风险：GPL 文件会被误标 MIT）；`capture` 模块级 `ZoneInfo` 在无 tzdata 的 Windows 纯 pip 环境**导入即崩**（崩点先于法律自检；pyproject 无 `tzdata; sys_platform=="win32"` 标记依赖）；capture 违反 TdxError→exit 2 契约（裸 traceback）；`codegen` 死校验+非法 spec_id 产坏码；`spec_audit` 一次运行全量解析 3 遍；`cli _cmd_changes --types abc` 裸 ValueError。
26. `credentials.py` docstring 与实现矛盾（损坏时声称返回空 dict 实抛错）；文件损坏时以空 dict 覆盖重建（可抢救数据销毁面）；keyring+弱 XOR 文件同写（弱文件恒持全部密钥等价物，与「优先 keyring」叙事冲突）。
27. `push.py`：`_build_sub_body` 后缀式符号产出 `"519.SH"`（复核）+ bj→1；`PushChannel` 文档引导直用池内连接（绕过一切池级串行化，与 C2 冲突）；read_frame 签名与真实 Connection 失配（注入真实连接被 `except Exception` 吞成永远无帧）。

---

## §3 P2 清单（打磨，批次 H——摘要，全量见六域终版报告）

1. `fetch_all`/`ticks` 翻页依赖服务端空页终止 → 硬上限（1000 页）+ rows 非 list 守卫。
2. TokenBucket `try/except import threading` 死代码；`pool.py:66 Slot.busy` 死字段（随 C2 落实或删除）；`engine.py` diff_only 死分支；`mac._count_records`/`MacHeartbeatParser 恒真`/`_client_or_new`/`max_queue` 死参数/`ResponseFrame.ok` 硬编码 magic/`iter_frames` 回放不校验帧长/`get_datetime_from_lc` 死参数。
3. `errors.py` 流式异常四类零使用；`record_reconnect` 零调用；`schema web_facade/market_facade` 死配置；`output.default_format="model"` 无消费者；`WebConfig.enabled=False` vs `SourcesConfig.web=True` 默认矛盾；`cli list --start` 死参数；`serve --port 0 → 8000` 短路；`binary.py:208` 死代码；`presets.py:181` 注释自认漂移；sources csv/ws 口径漂移。
4. 三套 YAML 实现并存（capture `_yaml_dump` / golden_expand `_mini_yaml` / `tools._yaml_min`）→ 收敛一处；`_yaml_min` 孤儿缩进行静默丢、`1_000`/`nan` 接受偏离 YAML。
5. `i18n` 裸 except 与缓存无锁；`symbol.py:208` 裸 except（吞 SystemExit）；`\d{5,6}` 对债券/期权段不支持（业务需复核）；`resolve_vipdoc_path` 对后缀式符号必拼错路径。
6. 文档口径漂移集：`sources.py`「140 笔/页」vs `ticks.py`「70 条」；http docstring 32 端点 vs 实 40；`prober`/`reporter`/`telemetry` 承诺与接线（随批次 G 处置同步）；FAQ 称 web 源 6 个 vs 实 14。
7. 正面确认（保持）：KeyboardInterrupt/SystemExit 在 `query/aquery/wrap/_try_routes` 正确穿透未吞；capture 法律 AND 门禁+时区无懈可击；calendar 内置 2024/2025 与官方逐条吻合；golden 三旗标与 Makefile 门禁真实生效；0x0530 回声+哨兵+量额交叉校验是同类库罕见亮点；协议原语层「畸形输入不可驻留」经对抗实测成立。

---

## §4 孤儿判定表（终版，含三方报告交叉裁决）

| 模块 | 判定 | 关键证据 | 处置建议（批次 G） |
|---|---|---|---|
| `protocol/requests.py` | **真孤儿** | 全库（含 tests）零 import；client 16 处内联 struct.pack；**已现漂移**（80 只上限 vs 内联版无）；docstring 反向自称「集中于此」 | client 改调它（推荐，消除双源）或删除 |
| `protocol/parsers/_generated.py` | **真孤儿（注册链断裂）** | `parsers/__init__.py` 不导入它（三方证实）；0x0004/0x000D 运行时走 L2；且产物带 `tier="TIER_L1"` 字符串 bug（confidence 恒错） | 接线前先修 tier+骨架守卫，或与 codegen 一起决策去留 |
| `protocol/prober.py` | 真孤儿（生产） | 零代码消费零测试（docs 推荐）；缺陷（31 年 sleep/契约矛盾）无人守护 | `tstdx probe` CLI 化 + 安全约束回归测试，或降级文档化手动工具 |
| `transport/sniff.py` | **真孤儿** | 全库零 import 零测试（含 __init__ 未导出，与 protocol/generic 的 ProtocolSniffer 无关） | client 可选挂钩接线或删 |
| `_async_bridge.py` | **真孤儿且已坏** | 零消费零测试；`run()` 100% RuntimeError（常驻 loop 再 run_until_complete）；单异常杀桥；close 白等 5s | **删除**（-390 行）；需要时以 `run_coroutine_threadsafe` 重写并测试 |
| `domain/adjust.py` | 真孤儿（价值型） | 零生产消费、**零测试零 golden**；E4 复权因子库前置件 | 接 facade `bars(adjust=)` + capital_changes 喂数 + golden 对拍 |
| `profile/detect.py` + `presets.py` | 真孤儿（双管线并存） | 生产走 reader.profile 另一套探测；先验加权死代码 | 与 reader/profile 二选一收敛；留者补 scale 探测 |
| `native.py` | 真孤儿（仅自测） | 未接入 reader/协议热路径；对拍门控良好 | 决策性能路线：接线热路径或明确「实验性」标注 |
| `feedback/{telemetry,stats}.py` | **真孤儿** | 零生产消费者零测试；config `FeedbackConfig` 无人读 | 接线（CLI feedback submit/stats + client 埋点）或删 |
| `feedback/reporter.py` | 运行时孤悬（公共 API） | _LAZY 导出、面向手动调用；无自动接线零测试 | 保留 + 补单测锁定脱敏语义 + 文档定位 |
| `observability/{otel,prometheus,statsd}_exporter.py` | 半孤儿 | 导出 API 存活但仓库内零接线；config `exporter` 段校验后无消费者（「配了不用、用了不配」）；statsd delta bug 侧面印证从未真实使用 | config→exporter 路由表 + 默认参数修（interval=0 陷阱）+ 装配文档 |
| `facade/{binary,bridge,market}.py` | 库内孤儿（对外 API） | 仅 `facade/__init__` re-export；**cli/integration/tests 零消费**（本会话实测 grep：cli.py 无 facade 门面 import——早前「cli:638 接线」说法为误报）；三套互相重复的 TdxClient 惰性包装 | 定位为「竞品吸收兼容层」：显式文档化 + 每类 ≥3 冒烟测试；否则合并入统一门面 |
| metrics 埋点 / ws 订阅 / i18n / cli / tools/* | 见 §2/§3 | 埋点零调用=「建好未插」；ws 订阅死存储；i18n=有意公共 API；cli/tools=进程入口/Makefile 真实接线（`make audit` 三工具） | 批次 G 决策矩阵统一处置 |

**汇总**：真孤儿 8（requests、_generated、sniff、_async_bridge、telemetry、stats、adjust、profile 双管线）+ 半孤儿/孤悬 8。~3000 行「有代码无流量」，逐项二选一闭环，禁止第三态。

---

## §5 单位与口径一致性专项（协议库生命线）

| 口径 | 现状 | 证据等级 | 处置 |
|---|---|---|---|
| bj 市场码 | 协议链 0 / 文件 profile 链 2 / push 1 ——三义并存；presets 注释自认「老主站把 BJ 归 SZ(0)」 | 复核（本会话独立确认） | 分场景定标表入 PROTOCOL_SPEC；0x0547 帧抓包裁决；push/requests/profile 三处统一 |
| 0x0530 market 反转 | 唯一实现 `quote_request_market` ✓，但 `_quote_body` 第二份内联拷贝外推到 3 个未验证族；0x054C body 用标准口径 | 实测+报告 | 反转语义按族建表；代码收口单一助手；`_quote_body` 注释纠错 |
| 指数 K 线 4 字节尾 | 解析器自述 +4 字节，**ctx 生产链永不传递** | 复核（接线缺失）+报告（布局需 golden） | index_bars 显式 ctx + 真机指数 golden 定标 |
| 差分基准 | 0x052D/0x0202 跨记录 base ✓；0x0104/0x1300 缺 base 却自称同构 | 报告 | 待样本锁定；「同构」声明加 CI lint |
| 价格 scale | `net_bars` 死档案 scale=100 与 golden 矛盾（10 倍价差点） | 报告 | 删除或对齐；profile 一致性校验 |
| 成交量单位 | 快照系 ×100 折股 ✓；QuotesLegacy/BlockQuotes/Minute*/Trade* 直接 int 无单位标注 | 报告 | 「同名 volume 字段跨命令」单位标注入 spec + 归一化 |
| 日期 | `bar_date` 关键字强制 ✓；doctest 000600 条目与反转互斥（P1d）；1970 哨兵 | 报告 | doctest 裁决 + `--doctest-modules` 入门禁 |

---

## §6 批次实施计划

### 批次 F（立即，~2 周）——「第一批」全实测级优先
- **F0（1-2 天）**：async 门面实例化重构 + HttpxClient.post + config env 两项解析修复——全部小改动、公共 API 面、当日可回归。**教训条款：回归测试必须打桩在传输层（假 Connection），禁止打桩被测方法本身。**
- **F1（并发组）**：C1-C7 一揽子（streaming 归一化 + 连接租约锁 + 错误收集局部化 + async ping/续帧收口 + limiter deadline），带真实形态 fake 与 16 workers 压测回归。
- **F2（协议组）**：dispatch 边界异常收口（P1b，一处改动覆盖逃逸面）+ index ctx（P1a）+ doctest 裁决（P1d）。
- **F3（Web 组）**：W1-W13；W14 逐源防御批（每源四板斧罐头用例=对抗实测 CI 化）。
- **F4（服务组）**：V1-V7；V3 Histogram 累积性必须带黄金值断言。
- **F5（门禁固化）**：本审计三件工具收编 `tstdx/tools/`——对抗矩阵（原生逃逸=0）、可达性扫描（真孤儿=0 有决议）、并发串线压测（10k 请求 0 帧错）。

### 批次 G（1-2 月）——结构性收敛
- G1 **符号/市场编号统一**（§2 专项 1-3）：收编五套旁路入 domain.symbol + BJ 三场景定标 + 链间一致性属性测试（random codes 五链比对恒等）。
- G2 流式收敛：QuoteStream 吸收 engine 组件（兑现背压/补数/退避三承诺）或砍 engine 全文档同步——二选一，禁止并存平行实现。
- G3 可观测性决策：metrics 主路径埋点 + config→exporter 路由 + Histogram 修复联动，或整体降级「手动装配」文档化——当前中间态最差（双注册表恒空+分位数算错）。
- G4 孤儿处置周：§4 表逐项「接线 or 删除」闭环（预计净 -1500 行或等量接线测试）。
- G5 Eastmoney `_get_json` 五份拷贝上收基类 + 下线检测覆盖自定义路径 + `_failures` 分桶。
- G6 门面路由语义统一：route 参数全方法生效 + route_errors 聚合 + 熔断/sticky + bars 双通路口径固定。

### 批次 H（季度）——质量债
- H1 mypy 清 `type: ignore`（web 域 100/124 优先）；H2 服务面集成冒烟（socket 级）+ WS 并发写压测；H3 存储原子写统一 + stale 元数据；H4 check_originality tokenize 化 + `--fix` 许可校验；H5 静默降级全面 warnings 化（declared vs parsed count lint、单位标注 lint、同构差分 lint）；H6 P2 长尾。

### 批次 E 遗留队列（不变，并入 G/H 排期）
E2（复权因子库）/E4/E6/E9——其中 E2 与 adjust 接线（G4）天然合并。

---

## §7 验证计划（批次出口标准）

| 门禁 | F 批出口 | G 批出口 |
|---|---|---|
| pytest 全量 | 绿 + 新增回归 ≥45（真实形态 fake；async 门面端到端不打桩；httpx/urllib 双栈） | 绿 + 属性测试（五链符号一致性） |
| 对抗矩阵（工具固化） | 原生异常逃逸 **0**（现 7 类）、>5s 慢解析 0、最慢耗时趋势跟踪 | 同左，纳入 CI `--strict` |
| 可达性扫描 | 真孤儿存量 ≤8 且全部有处置决议 | 真孤儿 **0**（接线带测试或删除） |
| golden 三旗标 | 持续绿；P1a/P1d/P1e 裁决后更新样本注记 | 指数/0FC5/1300 补录 golden（非交易时段合规采集） |
| 并发正确性 | `quotes_concurrent(workers=16)` 罐头 server 10k 请求 0 串线；心跳/在飞交叉压测 | WS/HTTP/MCP 集成冒烟绿 |
| 配置面 | `TSTDX_RATE_LIMIT_*`/数值 env 生效回归 | config→exporter/limiter 端到端装配测试 |
| 合规红线 | capture AND 门禁回归不破（正面确认保持）；tzdata 依赖标记 | 同左 |

---

## 附录 A：审计过程资产与方法记录
- **对抗 payload 矩阵**（9 畸形字节 × 85 命令 × 5 family）：本会话实际执行，产出 T2/T3 全部实测锚点；建议固化（F5）。
- **AST 可达性扫描**：识别 _LAZY 字符串边 + 相对导入归一 + tests 交叉；已知盲区三（`python -m` 入口、函数内惰性 import、`__init__` 边判定）已修脚本复跑。
- **六域报告全部完整重跑**：初版报告存在行号漂移与代码不存在引用（见附录 B），重跑版逐条给出可定位证据；其中 2 项 P0 由 agent 在只读沙箱**运行时复现**、13 项由本会话独立实测/复验。
- **运行时复核命令样例**（本会话执行）：async 门面 TypeError 复现、httpx post 缺失探测、config 双 env 崩溃复现、`po=1 if ascending else 1` 提取、`tstdx.__all__` 属性访问、Histogram break 语义读码、async ping 无锁读码、`index` ctx 全库 grep、Makefile 门禁接线确认。

## 附录 B：误报剔除清单（防复发，全部有反证）
1. `cli._cmd_changes` JSON 分支 UnboundLocalError —— rows 在 except 路径前已绑定，复核不崩。
2. `web/base.fetch` while True 不递增 attempt 无限重试 —— 实为 `for range(max_retries+1)`，有限。
3. `async_._get_or_create` 建连不持锁 —— request 259 行持锁，行号漂移致幻。
4. `pool._get_conn` 覆盖 slot.conn 丢连接 —— 仅 None 时建连，无覆盖。
5. `facade/bridge.py:215 _PREFIX_MARKET NameError` + 「binary/bridge/market 有 cli 接线」双向误报 —— 文件仅 214 行且 cli 无该 import（实测）；三门面按「库内孤儿（对外 API）」重新定性。
6. `config._SCALARS` 缺 int/float —— `parse_env_value` 实有数值分支（实测）；真缺陷为 F0-3 两项（实测崩溃）。
7. `adapters._kline_frame_to_bars` 无进展死循环 —— 该函数全库不存在（grep=0）；实际 `fetch_all` 有页进退出。
8. `wencai.py:300-310/126-147` 行号引用 —— 文件仅 159 行；wencai 问题按重跑版重新定性（P2 行类型守卫）。
9. `boards.is_member` 首元素即返回死代码 —— 所引行号实为 parse JSON 逻辑，内容不符。
10. `TdxClient.batch()` 官方多线程入口实证 —— client.py 无 `def batch`（实测）；并发触发面如实修正为 `quotes_concurrent`（F1 特性本身）。
11. 协议域初版 13 条 P0（`*= scale`/`reader.int64()`/`build_bars_body` 等不存在引用）；`transport/pool 心跳不持 slot.lock` 初报 —— 终版确认持锁（问题定性已相应调整为「锁只覆盖建连+锁内 IO」）。
12. `streaming` 域 P2 澄清采纳：engine `time.sleep(5)` 与 push `while True` 均为 docstring 示例文本，非生产代码。

## 附录 C：与 docs/archive/OPTIMIZATION_PLAN.md（v1.1.0 A-E 批次）的关系
A-E 批次已全部完成并回归；本文档是其续篇（F/G/H 批次编号顺延），E 队列遗留项（E2/E4/E6/E9）并入 §6 G/H 排期。两份文档共同构成 v1.1.0→v1.2.0 的完整改进路线。

---

## 附录 D：修复状态回填（v1.2.0 实施中，2026-09-03）

> 图例：✅ 已修复并回归 ｜ ⚠️ 修正后按「文档化待定标」处置（不可盲改数值行为）｜ ↩ 复核反驳（原审计声明不实，附证据）｜ 🔒 门禁固化

### 编排者完成项（F0 / 孤儿裁决 / 无主缝隙 / 门禁）

| 项 | 状态 | 证据 |
|---|---|---|
| F0-1 async 门面实例桥接 | ✅ | `tests/unit/test_fix_f0.py` 13 用例（传输边界打桩纪律入测） |
| F0-2 HttpxClient.post | ✅ | 同上（post 覆写 + 假 httpx 端到端） |
| F0-3 config env 三修 | ✅ | 同上（`_split_env_section` 最长前缀 / `"1"`→int / 未知段 warn）；`test_boolean_coercion` 按新契约更新 |
| 删除 `_async_bridge` | ✅ | §4 决议执行；全量 pytest 无回退 |
| 删除 `protocol/requests.py` | ✅ | grep 证实零消费者后删；文档引用已清 |
| 删除 `parsers/_generated.py` | ✅ | 注册链断裂幻影；cookbook 示例改「生成骨架→本地注册」 |
| §2-26 credentials 三缺陷 | ✅ | `tests/security/`（隔离重建/互斥写/契约 docstring）10 用例 |
| §3-3 E6xxx 四类零使用 | ✅ | 决议=保留预留公共分类，errors.py 注释钉死（禁止另造第五类） |
| §3-6 文档口径 | ✅ | adr/Web 源计数修正（grep 实测 45 类/14 模块）；「FAQ 6 个」声明已过时（文档无此句，↩ 本条审计声明部分失准） |
| F5 对抗矩阵固化 | 🔒 | `tests/adversarial/test_full_matrix.py`（strict-L1/降级/canary 三测）+ CI job |
| F5 可达性扫描固化 | 🔒 | `scripts/audit_reachability.py --strict` + 18 项处置白名单 + CI job；孤儿存量=prober（接线中） |
| F5 统一门禁序列 | 🔒 | `make gates` 六步（lint→format→全量→对抗→golden 三旗标→可达性）+ CI 升级（三旗标/两新 job/lint 纳 scripts） |
| §7 golden 三旗标基线 | ✅ | 实跑通过：L1 verified 全有 real 样本、cat 覆盖 0/4/9、市场 0&1 |

### 域修复批次（代理并行实施中，回报后逐项落状态）

| 批次 | 范围 | 状态 |
|---|---|---|
| F1 | C1-C7（T/S 域）+ 增补 T4/§2-9/sniff/record_reconnect | ✅ 双域落地（S 90 项/T 含 10.4k 压测 40+ 项，编排者复验绿；附带修复审计未载 async Event.wait 缺陷；T 域另发现 3.10/3.11 asyncio.TimeoutError 别名跨版本缺陷） |
| F2 | P1a-P1e + T1/T2/T3 + 增补 P1c/截断告警/死代码三项（P/S 域） | ✅ 落地（610 组合 0 逃逸复验；**实测修正**：审计锚点 7 类为旧形态，真实收口面上提至 dispatch 边界双层防线；P1d golden 逐字节裁决 600000 doctest 笔误；上报的「0x0530 真缺陷」经编排者独立实测**不成立**=在途快照误归属，见运行手册裁决档） |
| F3 | W1-W14 + V5/G2 门面项（W/A 域）+ 增补 140→70 口径 | ✅ 双域落地（W tests/web 327 绿 + 62 新增 + 复核不成立 6 条含行号证据；A 83 绿 + 53 新增 + `_option_symbol` 幻影拒绝虚构；编排者 canary 适配 F2 新契约后对抗矩阵 12 项全绿） |
| F4 | V1-V7 + 反馈面 + 端点计数（V 域） | ✅ 落地（276 复验绿 + 66 新增 + 真回环 socket 端到端收推送测试；**复核不成立 3 条**：mcp route/force 与 _write_loop 不存在、ws SHA-1 无调用点；Histogram 累积/statsd 增量/环形缓冲死分支均修；42 端点程序化清点入 docstring） |
| §2 存储配置域 | 符号五链收编/calendar/sinks/sources/detect/native/presets/CLI/工具链（G 域）+ 增补 YAML 收敛/csv 口径 | 🔄 **唯一在途**（symbol 白名单裁决已落地并三方交叉验证；G 已接线 probe/feedback/stats/submit CLI 子命令——可达性 prober 孤儿已闭合在望） |
| §3 P2 长尾 | 随各域清剿（TokenBucket 死 import/翻页上限/死参数群） | 🔄 随 G 收口 |

### 集成收口运行手册（编排者·第 10 轮固化，代理回报后按序执行）

1. **收集七域汇报**（T/S/P/W/V/G/A，运行时自动送达）：逐项状态、复核反驳（↩）、
   跨域联动点、契约变更记录。反驳成立者入附录 B 追加条目，**不**改代码。
2. **红钉翻转对账**（`tests/seams/test_contracts.py`，当前 6 绿 2 红）：
   - 🔴 `observability/__init__.py` `Any` NameError（V 在途）
   - 🔴 `Slot.busy` 死字段（T C2 未落地）
   任一在收口时仍红 → 直接修复该域文件（所有权随代理结束而释放）。
3. **12:10 全仓 format 事件回归**：该秒 mtime 覆盖 tstdx/transport 全部 +
   几十个旧测试文件（疑似 T 违规跑仓库级 `ruff format`）。格式不丢内容、
   他人编辑只会在 old_string 失配处干净失败——但最终全量 pytest +
   `ruff check` 必须完整跑一遍兜底。
4. **跨域接缝行为验证**（缝钉只查形态，以下查行为）：
   ① `client.bars(index=True)` → dispatch ctx → std7709 `index_mode` 消费链
   端到端（罐头 server 断言 4 字节尾被吞）；② pool 重连 →
   `metrics.record_reconnect` 计数 +1；③ cli `quotes_snapshot` 消费
   `client.last_errors`（S 修复形态 × G 退出码联动）；④ 异步门面 × A 的
   route 拒绝（ValueError 不被 aquery 折叠吞掉）；⑤ 中证指数白名单改
   `to_tdx_market` 后 000xxx 个股回归。
5. **孤儿终态**：`python scripts/audit_reachability.py --strict` exit 0
   （prober 经 `tstdx probe` 接线后须自动归零；`_reach_allow.txt` 增删须核理由）。
6. **门禁六步**：`make gates` 语义（本机直接 PowerShell 逐条跑）：
   `ruff check tstdx/ tests/ scripts/` → `ruff format tstdx/ tests/`（此时
   **一次性**全仓格式化，所有权已释放）→ `pytest tests -q` 全量 →
   对抗矩阵 → golden 三旗标 → 可达性。T 的 10.4k 压测若标 slow，以
   `$env:TSTDX_STRESS="1"` 单独跑一次并记录耗时。
7. **版本与文档原子落笔**：`pyproject.toml` + `tstdx/__init__.__version__`
   → `1.2.0.0`→`1.2.0`；CHANGELOG 1.2.0 节补录 F1-F4 条目（各域汇报转化，
   契约变更集中「Changed」段）；附录 D 各行翻转 ✅/⚠️/↩；FAQ 能力表若
   受新 CLI（probe/feedback）影响则同步。
8. **不可验证项终检**：⚠️ 标注族（0FC5 记录长、std7727/mac 差分基准、bj
   市场码定标、0x0547、index 4 字节尾布局）必须全部在代码注释 +
   PROTOCOL_SPEC + 本文档三处口径一致，禁止任何一处残留「已锁定」假声明。
