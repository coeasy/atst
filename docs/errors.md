# 错误体系与 RetryAdvice 使用指南

> 错误分类树定义于 `tstdx/errors.py`（44 个类，E1-E9 九域，无语义重叠对——
> v8 审计结论，见 [ARCHITECTURE_AUDIT_v8.md](archive/plans/ARCHITECTURE_AUDIT_v8.md) §二）。
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

- `TruncatedDataError`（E4050，DataError）：**网络响应**数据被截断，抛出点都在
  `tstdx/client/_mixin.py`：`bars(strict=True)` 的分页锚点漂移、`bars(strict=True)` 的
  首页空响应（服务端声明 0 条记录——空首页不是历史耗尽，耗尽只会表现为短页），
  以及 `file_download(strict=True)` 累计字节未达服务端报告的 `total_len`。
  `TruncatedRecordError`（E5020，FileFormatError）：**本地 vipdoc 文件**记录不完整。
- `RateLimitedLocal`（E2050）：**本地**限流器主动拒绝（客户端节流）；
  `WebRateLimited`（E7020）：**远端 HTTP** 429/反爬限流。
- `CommandOffline`（E3035）：TDX 命令在主站已下线；
  `SourceDeprecated`（E7030）：Web 数据源接口下线。

## 二、每个异常都带 RetryAdvice

`TdxError.advice` 返回 :class:`~tstdx.errors.RetryAdvice`，字段契约：

| 字段 | 含义 | 消费方 |
|---|---|---|
| `retryable` | 是否值得重试（确定性错误如 404 为 False） | `transport/pool.py` 故障转移循环 |
| `backoff` | 重试前退避秒数 | pool 重试 sleep |
| `max_retries` | 建议最大重试次数 | pool 重试上限 |
| `switch_host` | 换一台主站再试 | pool 故障转移 |
| `fallback_to_offline` | 可降级读本地 vipdoc | sources 路由 |
| `fallback_to_web` | 可降级走 HTTP Web 源 | sources 路由 |
| `note` | 人类可读建议（进日志/错误摘要） | 各层日志 |

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

- **门面层**（`facade/api.py`）永不抛异常边界：`query()`/`aquery()` 把任何
  异常转 `ApiResponse{success=False, error, code}`；路由链失败时最后一路由
  异常的 `context["route_errors"]` 聚合各路由失败摘要（W11）。
- **服务面**（`integration/http_server.py`）用 `http_status_for()` 把
  TdxError.code 映射为 HTTP 状态码。
- **CLI** 捕获 `TdxError` 打印 `str(exc)`（含 `[code]` 前缀）并以退出码 2 结束。
