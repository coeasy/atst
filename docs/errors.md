# 错误体系与 RetryAdvice 使用指南

> 错误分类树定义于 `tstdx/errors.py`（E1-E9 九域，无语义重叠对——v8 审计结论，
> 见 [ARCHITECTURE_AUDIT_v8.md](archive/plans/ARCHITECTURE_AUDIT_v8.md) §二）。
> 类数不在本文抄录：v8 写的是 44 个类，v17 当前是 46 个类（`errors.py` 顶层类计数；
> `tstdx.errors.__all__` 另有 49 个名字，多出的 3 个是函数）——抄一次就过期的数字不如指向源头。
> 本文是**使用侧**文档：异常怎么接、RetryAdvice 怎么消费、如何扩展。

## 一、错误树速查（按域）

| 域 | 基类 | code 段 | 子类 |
|---|---|---|---|
| 配置 | `ConfigError` | E1xxx | `ValidationError` E1010 / `DependencyMissingError` E1020 |
| 传输 | `TransportError` | E2xxx | `ConnectionFailed` E2010 / `ConnectionClosed` E2020 / `ReadTimeout` E2030（← `WriteTimeout` E2031）/ `AllHostsUnreachable` E2040 / `RateLimitedLocal` E2050 |
| 协议 | `ProtocolError` | E3xxx | `FramingError` E3010 / `DecompressError` E3020 / `UnknownCommand` E3030 / `CommandOffline` E3035 / `ParseError` E3040（← `LowConfidenceParse` E3041、`IntegrityViolation` E3042）/ `ChecksumMismatch` E3050 |
| 数据 | `DataError` | E4xxx | `ProfileError` E4010（← `ProfileUndetectable` E4011）/ `AdjustError` E4020 / `CalendarError` E4030 / `SymbolError` E4040 / `TruncatedDataError` E4050 |
| 文件 | `FileFormatError` | E5xxx | `DataFileNotFound` E5010 / `TruncatedRecordError` E5020 |
| 流式 | `StreamError` | E6xxx | `SubscriptionError` E6010 / `GapUnfilledError` E6020 / `BackpressureOverflow` E6030 |
| Web | `WebSourceError` | E7xxx | `AntiSpiderBlocked` E7010 / `WebRateLimited` E7020 / `SourceDeprecated` E7030；`AllSourcesExhausted` E7040（直继承 TdxError，语义是「整条降级链耗尽」而非单源故障） |
| 门面/兼容 | `CompatibilityError` | E8xxx | （facade / bridge shim 迁移兼容面） |
| 内部与依赖 | `TdxError` 直系 | E9xxx | `NotImplementedFeature` E9010 |

易混对照（**不重叠**，按域区分）：

- `TruncatedDataError`（E4050，DataError）：**网络响应**数据被截断。抛出点在
  `tstdx/client/_mixin.py`：`bars(strict=True)` 的分页锚点漂移、`bars(strict=True)` 的
  首页空响应（告警里的数字取服务端当次声明数：声明 0 是该标的无此周期历史，声明 N 却回 0 个
  记录字节是空桩；两者都不是历史耗尽——耗尽只会表现为短页），
  以及 `file_download(strict=True)` 累计字节未达服务端报告的 `total_len`；
  另一处在内核 `tstdx/runtime/executor.py`——`options["strict"]` 为真且本次结果携带
  任何一条数据瑕疵（`ResultMeta.warnings`，发射点见 `tstdx/diagnostics.py` 的
  `WarningCode`）时，执行器在返回前拒绝该结果，因此 `Client.bars(strict=True)` 与
  六类 bars 后端共用同一个"不许带瑕疵返回"的判据。
  `TruncatedRecordError`（E5020，FileFormatError）：**本地 vipdoc 文件**记录不完整。
- `RateLimitedLocal`（E2050）：**本地**限流器主动拒绝（客户端节流）；
  `WebRateLimited`（E7020）：**远端 HTTP** 429/反爬限流。
- `CommandOffline`（E3035）：TDX 命令在主站已下线；
  `SourceDeprecated`（E7030）：Web 数据源接口下线。

## 一之二、树里存在但运行期永不发生的类

下面这些名字在错误树里，却在 `tstdx/` 全仓没有任何抛点、也没有作为投递口
（`on_error(...)`）的实参交出去——**不要为它们写 `except`，那段代码不会执行**：

| 类 | code | 为什么不会发生 |
|---|---|---|
| `UnknownCommand` | E3030 | 内核不发送未知命令；只有离线工具（`tools/capture`、`ProtocolSniffer`）在研究命令表 |
| `ChecksumMismatch` | E3050 | 协议与传输层没有任何校验和读取点 |
| `BackpressureOverflow` | E6030 | `BackpressureQueue.put` 的既定语义是丢最旧元素并计数，从不抛 |
| `SourceUnavailable` | E7050 | 唯一的用武之地是已删除的 `UnifiedQuoteAPI` auto 兜底门面；Provider 真实不可用现为传输层原异常 |
| `CompatibilityWarning` | — | 从未发射的 `UserWarning`；兼容性判定走 `CompatibilityError` |

抽象基类 `TransportError`/`StreamError`/`ProfileError` 自身也不被 `raise`，
但它们的子类全部有站点，属于正常的分类节点，不在上表。

本表由 `tests/architecture/test_error_promises.py` 把守：对外文档点名的错误类
必须有真实站点，否则要么接线、要么登记裁决后进豁免表；一旦某类被接线，
它的豁免必须同步撤销（门禁会主动报"豁免已过期"）。

同一步里被**接上**的是 `FreshnessViolation`（E4060）：`currentness` 从只进 plan 的
声明口径变成运行期判据，判据与落点见 `docs/providers/tdx.md` §5 与
`tstdx/runtime/freshness.py`。

## 二、每个异常都带 RetryAdvice

`TdxError.advice` 返回 :class:`~tstdx.errors.RetryAdvice`，字段契约：

| 字段 | 含义 | 消费方 |
|---|---|---|
| `retryable` | 是否值得重试（确定性错误如 404 为 False） | `transport/pool.py` 故障转移循环 |
| `backoff` | 重试前退避秒数 | pool 重试 sleep |
| `max_retries` | 建议最大重试次数 | pool 重试上限 |
| `switch_host` | 换一台主站再试 | pool 故障转移 |
| `fallback_to_offline` | 历史兼容字段 | **无消费方**（见下） |
| `fallback_to_web` | 历史兼容字段 | **无消费方**（见下） |
| `note` | 人类可读建议（进日志/错误摘要） | 各层日志 |

最后两行是 v17 的实况：`sources` 路由已随单内核删除，全仓对这两个字段的唯一
读点就是 `TdxError.to_dict` 自己把它们写进序列化字典（`errors.py:154`）。
`errors.py:20` 早已声明"仅为序列化兼容保留，新内核不消费"——本文此前把它们
写成有消费方的路由开关，属于幻影字段（F-68 登记，与 F-43 同族）。

**核心设计**：故障转移策略由异常自带、不在传输层硬编码——新增一种错误
只需在 `errors.py` 声明 `default_advice`，传输层自动获得正确行为
（`transport/__init__.py` docstring、`pool.py:423` 消费 `advice.retryable`）。

解析优先级：实例 `advice=` 参数 → `RETRY_ADVICE[type]`（`_register_all()`
模块加载时全量注册）→ 类属性 `default_advice` → 全局默认（全 False）。

## 三、扩展新异常

```python
from tstdx.errors import TdxError, RetryAdvice

class MyDomainError(TdxError):
    code = "E9500"
    default_advice = RetryAdvice(
        retryable=True, backoff=0.5, max_retries=2, switch_host=True,
        note="主站抖动，建议换主机重试",
    )
```

规则：`code` 全库唯一（E<域><序号>）；`default_advice` 只声明与父类不同的
字段以外尽量显式；不新增与现有类重叠语义的类（先查上表易混对照）。

## 四、上层边界约定

**没有"永不抛异常"的门面层**：`facade/api.py` 与「`query()` 把任何异常转
`ApiResponse{success=False, ...}`、路由链失败时聚合 `context["route_errors"]`」是 v12 门面
（`tstdx/facade/`）的形状，该目录已随单内核物理删除，`ApiResponse` 与 `route_errors` 在
`tstdx/` 里都不存在——本节此前把它们写成了现行边界（F-67）。今天的边界事实：

- **业务入口**（`tstdx/client/api.py` 的 `Client`/`AsyncClient`）不吞异常：整个模块没有一处
  `except`，失败以 `TdxError` 家族原样抛给调用方，`code`/`advice`/`context` 随异常走。
- **越过信任边界的错误只有一个形状**：`tstdx/error_envelope.py::to_error_envelope` 产出的
  :class:`~tstdx.error_envelope.ErrorEnvelope`。它带 `code`/`type`/`message`/`http_status`/
  `retryable`/`partial` 与 `provider`/`channel`/`capability`/`phase` 诊断；按**关键字**脱敏
  （`token`/`cookie`/`secret` 一类键不进正文，新增诊断字段默认可见）；并且 fail-closed：
  `fallback_allowed` 与 `provider_switch_allowed` 对外恒为 `false`。`error` 键是 `type` 的
  序列化别名。
- **HTTP 面**（`tstdx/integration/runtime_http.py`）响应状态取 `envelope.http_status`
  （数值由异常类自带的 `http_status` 声明，经 `http_status_for()` 读出——注意它吃的是
  **异常实例**，喂 code 字符串会静默落到 500 默认值），响应体固定为 `{"error": <envelope>}`；
  框架自身的 4xx 也换成本信封、只保留框架状态码。
- **WS 与 MCP 两面的人读位置不对称（实测）**：两面都把信封挂在 JSON-RPC `error.data`，但
  `tstdx/integration/runtime_ws.py` 的 `error.message` 是通用 RPC 文案（理由句在
  `data.message`），而 `tstdx/integration/mcp/_server.py` 把 `envelope.message` 直接平铺进
  `error.message`。写客户端时别照抄另一面的读法。
- **CLI**（`tstdx/cli/__init__.py::main`）捕获 `TdxError` 时向 stderr 打印一行
  `{"error": <envelope>}` JSON 并以退出码 **2** 结束；非领域异常同样走信封但退出码 **1**，
  `KeyboardInterrupt` 保留原生语义返回 **130**。本节此前写的是"打印 `str(exc)`（含 `[code]`
  前缀）"，那是已删除的旧 CLI 形状。

