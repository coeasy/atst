# 错误体系与 RetryAdvice 使用指南

> 错误分类树定义于 `atst/errors.py`（E1-E9 九域，无语义重叠对——按运行期现读校验）。
> 类数不在本文抄录，也不给"当前是 N 个"的快照数字：一律以 `atst/errors.py` 现读
> （`tests/errors/test_taxonomy.py` 把 `atst.errors.__all__` 与分类表双向核对，
> `tests/architecture/test_error_promises.py` 把本文点名的类与错误树双向核对）。
> 本文是**使用侧**文档：异常怎么接、RetryAdvice 怎么消费、如何扩展。

## 一、错误树速查（按域）

| 域 | 基类 | code 段 | 子类 |
|---|---|---|---|
| 配置 | `ConfigError` | E1xxx | `ValidationError` E1010 / `DependencyMissingError` E1020 |
| 传输 | `TransportError` | E2xxx | `ConnectionFailed` E2010 / `ConnectionClosed` E2020 / `ReadTimeout` E2030（← `WriteTimeout` E2031）/ `AllHostsUnreachable` E2040 / `RateLimitedLocal` E2050 |
| 协议 | `ProtocolError` | E3xxx | `FramingError` E3010 / `DecompressError` E3020 / `CommandOffline` E3035 / `ParseError` E3040（← `LowConfidenceParse` E3041、`IntegrityViolation` E3042） |
| 数据 | `DataError` | E4xxx | `ProfileError` E4010（← `ProfileUndetectable` E4011）/ `AdjustError` E4020 / `CalendarError` E4030 / `SymbolError` E4040 / `TruncatedDataError` E4050 / `FreshnessViolation` E4060 |
| 文件 | `FileFormatError` | E5xxx | `DataFileNotFound` E5010 / `TruncatedRecordError` E5020 |
| 流式 | `StreamError` | E6xxx | `SubscriptionError` E6010 / `GapUnfilledError` E6020 / `BackpressureOverflow` E6030 |
| Web | `WebSourceError` | E7xxx | `AntiSpiderBlocked` E7010 / `WebRateLimited` E7020 / `SourceDeprecated` E7030；`AllSourcesExhausted` E7040（直继承 TdxError，语义是「整条降级链耗尽」而非单源故障） |
| 门面/兼容 | `CompatibilityError` | E8xxx | （facade / bridge shim 迁移兼容面） |
| 内部与依赖 | `TdxError` 直系 | E9xxx | `InternalError` E9000 / `NotImplementedFeature` E9010 |

易混对照（**不重叠**，按域区分）：

- `TruncatedDataError`（E4050，DataError）：**网络响应**数据被截断。抛出点在
  `atst/client/_mixin.py`：`bars(strict=True)` 的分页锚点漂移、`bars(strict=True)` 的
  首页空响应（告警里的数字取服务端当次声明数：声明 0 是该标的无此周期历史，声明 N 却回 0 个
  记录字节是空桩；两者都不是历史耗尽——耗尽只会表现为短页），
  以及 `file_download(strict=True)` 累计字节未达服务端报告的 `total_len`；
  另一处在内核 `atst/runtime/executor.py`——`options["strict"]` 为真且本次结果携带
  任何一条数据瑕疵（`ResultMeta.warnings`，发射点见 `atst/diagnostics.py` 的
  `WarningCode`）时，执行器在返回前拒绝该结果，因此 `Client.bars(strict=True)` 与
  六类 bars 后端共用同一个"不许带瑕疵返回"的判据。
  `TruncatedRecordError`（E5020，FileFormatError）：**本地 vipdoc 文件**记录不完整。
- `RateLimitedLocal`（E2050）：**本地**限流器主动拒绝（客户端节流）；
  `WebRateLimited`（E7020）：**远端 HTTP** 429/反爬限流。
- `CommandOffline`（E3035）：TDX 命令在主站已下线；
  `SourceDeprecated`（E7030）：Web 数据源接口下线。

## 一之二、退役登记：曾是错误树成员、如今已不存在的名字

**本节是全仓唯一允许点名"不存在的错误类"的地方**——它的职责就是登记退役。其它对外文档
（README、`docs/providers/*`、`docs/troubleshooting.md` 等）只许点名树里真实存在且有运行期
站点的类，两侧都由 `tests/architecture/test_error_promises.py` 把守：文档点名的树内类必须
有站点，文档点名的树外名字必须是本节列出的退役名，而本节点名的类必须确实**不在**树里
（否则这份登记表自己就在说谎）。

| 退役名 | code | 退役于 | 曾经为什么虚 |
|---|---|---|---|
| `UnknownCommand` | E3030 | v1.0.0 | 内核从不发送账本外的命令，未知/低置信样本走离线归档 |
| `ChecksumMismatch` | E3050 | v1.0.0 | 协议与传输层没有任何校验和读取点 |
| `SourceUnavailable` | E7050 | v1.0.0 | 唯一用武之地是已删除的 auto 兜底门面 |
| `CompatibilityWarning` | —（`UserWarning`） | v1.0.0 | 从未发射过；兼容性判定走 `CompatibilityError` |

`BackpressureOverflow`（E6030）留在树上、不进这张表，因为它确实有运行期站点：
`BackpressureQueue.put` 从不抛（丢最旧元素并计数），而"溢出"仍以
`BackpressureOverflow` 的形式**投递**给订阅方的 `on_error`（`atst/streaming/base.py`）。
"从不抛"不等于"信号不存在"——投递口就是它的兑现方式，行为证据见
`tests/streaming/test_backpressure_delivery.py`（真起一轮订阅、队列容量 1、断言回调拿到
`code=E6030` 且 `advice.retryable` 为真）。

## 一之三、同族里被"接线"而不是被删除的类

`FreshnessViolation`（E4060）：`currentness` 从只进 plan 的声明口径变成运行期判据，
判据与落点见 `docs/providers/tdx.md` §5 与 `atst/runtime/freshness.py`。

## 一之四、结果侧数据瑕疵：`WarningCode`

异常说的是"这次查询失败了"，`WarningCode` 说的是"这次查询成功了，但结果带着一条你
该知道的事实"。两者出口不同：瑕疵随 `QueryResult.meta.warnings` 上到 HTTP / WS / MCP /
CLI 每张面（序列化后是 `{"code", "message"}`），同时以 `UserWarning` 落在调用方进程里；
`strict=True` 时内核在返回前把**任意一条**瑕疵变成 `TruncatedDataError`（E4050）——所以
这张表同时也是"哪些结果会被 strict 拒收"的清单。空元组不是"没查"，是"干净"这一判断的证据。

类别集合是封闭的：全仓只有 `atst/diagnostics.py::record_warning` 一个发射口，新增类别
不在枚举里声明就在运行期 `TypeError`。下表与枚举由
`tests/architecture/test_caveat_channel_gates.py` 双向核对——文档少一行等于该类别在文档里
隐身，多一行等于文档在讲代码不会发生的类别，"发射口"一列指错文件等于把一条判断记在了
不产出它的模块头上。

| code | 什么时候会出现 | 发射口 |
|---|---|---|
| `bars_anchor_drift` | `bars` 分页中途锚点漂移而提前终止：实取根数少于请求根数，且不是"更早的历史已取完"（历史耗尽只表现为短页） | `atst/client/_mixin.py` |
| `bars_empty_first_page` | `bars` 首页即空响应。判据取服务端当次声明数：声明 0 是该标的无此周期历史，声明 N 却回 0 个记录字节是空桩；两者都不是历史耗尽 | `atst/client/_mixin.py` |
| `bars_range_uncovered` | 请求了 `start_date` 日期区间，但取回的那一页没有触及区间起点之前（协议只支持"从最新往回数 `count` 根"）：结果是区间的一段而不是全部。**区间完全落在取回窗口之前时同样触发**（此时结果是空列表——必须是这一条告警，否则调用方无从分辨"这些日期没有行情"与"这一段根本没取回来"）；区间起点落在休市日、取回窗口已触及起点之前则不算截断。加大 `count` 或用 `start` 继续往回翻页 | `atst/runtime/executor.py` |
| `bars_range_interior_empty` | `bars` 的日期区间落在取回 K 线**跨度的内部**（区间两侧都有 bar）却一根都没命中，且周期是粗周期（`week`/`month`/`season`/`year`）。这些周期的 bar 以「周期末交易日」为 `datetime`（未走完的周期取最新交易日），窗口落在周期内部时按日期裁剪必然为空——与 `bars_range_uncovered` 的分工是：那条管"页没取到区间之前"，这条管"取到了、区间被夹在两段之间却命中不到"。`day` 周期不走这条（窗口内部为空只可能是休市日）。修法是把区间对齐到周期边界或用 `day` 周期 | `atst/runtime/executor.py` |
| `index_minute_volume_nominal` | 指数**分钟线**的成交量是服务端名义值（按名义口径等于成交额/100），不是真实成交股数。指数日线族的成交量按「手」下发、已由解析器折算成「股」；分钟族这个字段换算不出真实股数，于是原样返回并发这条告警——不伪造数值。指数要量请改用日线或换 Web 源 | `atst/protocol/parsers/_std7709_bars.py` |
| `index_minute_volume_inconsistent` | 指数**分钟线**的成交量与服务端名义口径（= 成交额/100）差了一个数量级以上。2026-10 实测唯一偏离的形状是指数 `60min` 的当日首根（10:30）：服务端下发的 `volume` 比名义口径低约 97 倍，而同一时段由 `15min`/`30min` 聚合出的 `amount` 完全吻合；原始字节手工核对确认记录边界精确耗尽（解析器无错位），即这是服务端自己下发的不一致值。与 `index_minute_volume_nominal` 同时出发：那条说明"这个字段不是真实股数"，这条报告"这一页的值连服务端自己的名义口径都对不上" | `atst/protocol/parsers/_std7709_bars.py` |
| `quotes_partial_failure` | 批量 `quotes` 只取回部分标的的行情：逐只失败被隔离，结果不完整。全部失败不走这条，而是抛 `AllHostsUnreachable` | `atst/runtime/executor.py` |
| `decode_caveat` | 解码层对自己解出的这一页的判断：实收记录数少于声明数、字段布局哨兵异常、精确解析失败后降级为启发式 | `atst/client/_mixin.py` |
| `field_out_of_domain` | 解出来的行里有字段落在库自己声明的取值域之外——`market` 既不是 TDX 二进制市场编号也不是 canonical token、`code` 不是非空可见 ASCII、日期字段不是 ISO 形状。与上一条的区别在发射者：`decode_caveat` 是解码层自认的瑕疵，这一条是出口处拿 `atst/domain/integrity.py` 的尺子重新量出来的。记录布局尚未经真机 golden 锁定的命令页内字节数对得上，解码层因此一个字都不记，值却已经错位 | `atst/client/_mixin.py` |
| `security_list_empty_first_page` | `export_security_list` 首页即空响应，导出为空。空首页不代表该市场没有证券 | `atst/client/_mixin.py` |
| `security_list_page_limit` | `export_security_list` 在 `max_pages` 内未取尽（最后一页仍是满页），结果可能截断 | `atst/client/_mixin.py` |
| `file_download_short` | 分块文件下载累计字节数小于服务端报告的 `total_len` | `atst/client/_mixin.py` |
| `adjust_prev_close_missing` | 复权事件缺前收盘价：每股现金红利被忽略，价格因子是按 1+S+R 算的近似值 | `atst/domain/adjust.py` |
| `adjust_window_extend_failed` | 复权窗口向后延伸取数失败（已退回原窗口）：若最早事件早于窗口起点，早期因子的前收盘价取不到，后复权因子会随请求的 `count` 漂移 | `atst/runtime/executor.py` |
| `auto_provider_fallback` | `provider='auto'` 自动选源跨源出数：候选里排在前面的 Provider 失败后由后续候选接住（候选排序 = 能力面 + `routing.auto_providers` 偏好 + 健康反馈沉底）。结果带 `requested_provider='auto'` + `fallback` 的 Provenance——不是静默换源 | `atst/client/api.py` |
| `calendar_year_uncovered` | 交易日历未覆盖所请求的年份：该年节假日按"无节假日"处理 | `atst/domain/calendar.py` |
| `web_sina_pages_missing` | 新浪全市场分页在重试与补拉之后仍缺页：拿到的是缺页结果，不是全市场 | `atst/web/sina/adapters.py` |
| `web_tencent_pages_missing` | 腾讯全市场**代码枚举**阶段某页失败即停：返回的是被截断的代码表，全市场不完整（与 `web_tencent_batch_failed` 分属枚举 / 批量两个阶段） | `atst/web/tencent/adapters.py` |
| `web_tencent_batch_failed` | 腾讯全市场单批重试后仍失败：结果不完整，缺的那批不会以空行占位 | `atst/web/tencent/adapters.py` |
| `web_tencent_amount_all_zero` | 腾讯 K 线整批 `amount` 恒为 0（该源本周期不返回成交额字段），该字段不可用于计算 | `atst/web/_paginate.py` |
| `web_eastmoney_page_limit` | 东财报表在 `max_pages` 内未取尽（最后一页仍满页），结果可能截断——含复权事件分页触顶 | `atst/web/corporate.py`、`atst/runtime/executor.py`、`atst/web/_session_gaps.py` |
| `web_fund_sort_column_undeclared` | 基金排行请求的 `sort_column` 不在本包声明的常用列词表 `SORT_COLUMNS` 里：请求原样发出，服务端可能按自己的默认列返回 | `atst/web/fund_rank.py` |
| `currentness_unproven` | 声明的 `currentness` 要求当期数据，而本次 channel 给不出可判据的证据（本地文件）；`strict=True` 时它不是告警而是失败 | `atst/runtime/freshness.py` |

## 二、每个异常都带 RetryAdvice

`TdxError.advice` 返回 :class:`~atst.errors.RetryAdvice`，字段契约：

| 字段 | 含义 | 消费方 |
|---|---|---|
| `retryable` | 是否值得重试（确定性错误如 404 为 False） | `transport/pool.py` 故障转移循环 |
| `backoff` | 重试前退避的**基准**秒数：实际等待 = `min(基准 × 2^已试轮数, 8 秒) × 抖动` | pool 重试 sleep（`pool.py::retry_backoff_delay`，同步与异步池共用同一处声明） |
| `switch_host` | 换一台主站再试 | pool 故障转移 |
| `fallback_to_offline` | 历史兼容字段 | **无消费方**（见下） |
| `fallback_to_web` | 历史兼容字段 | **无消费方**（见下） |
| `note` | 人类可读建议 | `TdxError.to_dict()` 的 advice 字典（见下） |

`backoff` 是"回到刚失败过的那台主站"的退避基准：换到一台没试过的机器时，上一轮的
退避不再花调用方时间。上限是 `atst/transport/pool.py::MAX_RETRY_BACKOFF_SECONDS`，与
web 面的 `atst/web/_base_retry.py::MAX_BACKOFF_SECONDS` 同值（8 秒）。

表里没有 `max_retries` 字段：连接池的尝试次数来自 `[core] max_retries`
（`ConnectionPool.max_retries`）与主站数量。`RetryAdvice` 六个字段、`to_dict()` 六个
键、反馈载荷两格。它只能收紧一个本来就权威的上界，接进池里等于给"尝试几次"造第二个
真相源。要加回这个字段，得先说清哪一类错误该比 `[core] max_retries` 更早收口，并让
连接池真的按每类错误的建议值行动——判据 `tests/architecture/test_advice_field_actors.py`
会先要求执行方出现，再允许文档写它有消费方（§二 的表与字段名单两向相等，删一格必须
同时删一行）。

`note` 没有运行期消费方：包内没有任何 logger 读它。它的唯一出口是序列化——
`TdxError.to_dict()` 六个字段全量输出；反馈上报载荷（`atst/feedback/reporter.py`）
仍只带 `retryable` / `backoff` 两格；HTTP / MCP 的错误信封按设计只暴露 `retryable`，
不带 advice 字典。判据 `tests/architecture/test_advice_field_actors.py` 钉住这条口径：
每个 advice 字段要么有按它行动的执行方，要么在本文表格里显式写着无执行方，且
`to_dict()` 的键必须与字段名单逐一相等。

最后两行是历史兼容字段：全仓对这两个字段的唯一读点就是 `TdxError.to_dict` 自己把它们
写进序列化字典（`atst/errors.py` 里 `advice` 字典那两行），没有组件按它们行动——
`atst/errors.py` 的模块 docstring 也声明"仅为序列化兼容保留，新内核不消费"。

**核心设计**：故障转移策略由异常自带、不在传输层硬编码——新增一种错误
只需在 `errors.py` 声明 `default_advice`，传输层自动获得正确行为
（`transport/__init__.py` docstring、`ConnectionPool.request` 的失败分支消费
`advice.retryable`——这里按符号引用而不是写行号，行号会随任何一次上方编辑腐坏）。

解析优先级：实例 `advice=` 参数 → `RETRY_ADVICE[type]`（`_register_all()`
模块加载时全量注册）→ 类属性 `default_advice` → 全局默认（全 False）。

## 三、扩展新异常

```python
from atst.errors import TdxError, RetryAdvice

class MyDomainError(TdxError):
    code = "E9500"
    default_advice = RetryAdvice(
        retryable=True, backoff=0.5, switch_host=True,
        note="主站抖动，建议换主机重试",
    )
```

规则：`code` 全库唯一（E<域><序号>）；`default_advice` 只声明与父类不同的
字段以外尽量显式；不新增与现有类重叠语义的类（先查上表易混对照）。

## 四、上层边界约定

**没有"永不抛异常"的门面层**：本库不提供把领域异常转成 `success=False` 响应的封装层。
今天的边界事实：

- **业务入口**（`atst/client/api.py` 的 `Client`/`AsyncClient`）不吞异常：整个模块没有一处
  `except`，失败以 `TdxError` 家族原样抛给调用方，`code`/`advice`/`context` 随异常走。
- **越过信任边界的错误只有一个形状**：`atst/error_envelope.py::to_error_envelope` 产出的
  :class:`~atst.error_envelope.ErrorEnvelope`。它带 `code`/`type`/`message`/`http_status`/
  `retryable`/`partial` 与 `provider`/`channel`/`capability`/`phase` 诊断；按**关键字**脱敏
  （`token`/`cookie`/`secret` 一类键不进正文，新增诊断字段默认可见）；并且 fail-closed：
  `fallback_allowed` 与 `provider_switch_allowed` 对外恒为 `false`。`error` 键是 `type` 的
  序列化别名。
- **HTTP 面**（`atst/integration/runtime_http.py`）响应状态取 `envelope.http_status`
  （数值由异常类自带的 `http_status` 声明，经 `http_status_for()` 读出——注意它吃的是
  **异常实例**，喂 code 字符串会静默落到 500 默认值），响应体固定为 `{"error": <envelope>}`；
  框架自身的 4xx 也换成本信封、只保留框架状态码。
- **WS 与 MCP 两面的人读位置不对称（实测）**：两面都把信封挂在 JSON-RPC `error.data`，但
  `atst/integration/runtime_ws.py` 的 `error.message` 是通用 RPC 文案（理由句在
  `data.message`），而 `atst/integration/mcp/_server.py` 把 `envelope.message` 直接平铺进
  `error.message`。写客户端时别照抄另一面的读法。WS 面**连协议层失败也挂同一个信封**：
  解析失败 / 非法请求 / 未知方法这三条（裸 RPC code `-32700`/`-32600`/`-32601`）都由
  `RuntimeJsonRpcHandler._protocol_error` 统一补上信封，
  `context.phase == "ws_protocol"`、`fallback_allowed` 与 `provider_switch_allowed` 同样恒为
  `false`；解析失败那一格没有 `request_id` 可填（报文本身没解出来）。
- **CLI**（`atst/cli/__init__.py::main`）捕获 `TdxError` 时向 stderr 打印一行
  `{"error": <envelope>}` JSON 并以退出码 **2** 结束；非领域异常同样走信封但退出码 **1**，
  `KeyboardInterrupt` 保留原生语义返回 **130**。

