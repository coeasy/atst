# tstdx Industry Benchmark Refactor Plan v11

> 目标：对标主流行情数据/交易 API 项目的工程实践，进一步收敛 tstdx 为统一数据服务内核。
>
> 基线：v1.4.0 主线。当前已有协议层、连接池、Facade、Web、Streaming、HTTP/WS/MCP 等能力，但需要解决多入口规则漂移问题。

## 1. 行业对标结论

参考方向：

- pytdx / mootdx：底层 TDX 协议、vipdoc、本地解析与便捷 API 分层。
- CCXT 类项目：统一 capability 描述、统一异常、rate limit、exchange/source metadata。
- AKShare 类项目：接口 registry、schema、文档和测试契约。
- 工业数据服务系统：Query Plan、Source Manager、缓存策略、可观测性。

核心结论：

`tstdx` 当前低层能力已经接近工业库形态，下一阶段重点不是增加更多接口，而是建立统一执行内核。

## 2. 当前主要架构问题

### 2.1 多套路由规则

现状：

- UnifiedQuoteAPI 有 route/circuit。
- DataSourceRouter 有 source order。
- HTTP、WS、MCP 又自行选择 client/facade。

问题：

- 同一接口不同入口可能选择不同数据源。
- explicit route 语义不稳定。
- fallback 规则无法统一审计。

目标：

建立唯一 Canonical Router。

```
API
 |
QuerySpec
 |
Capability Registry
 |
Canonical Router
 |
Source Manager
 |
TDX/Web/Vipdoc/Replay
```

## 3. 新核心模型

## 3.1 Capability Registry

所有能力由声明驱动：

```python
CapabilitySpec(
 name="bars",
 sources={
   "tdx": {"periods": [...], "adjust": ["raw"]},
   "web": {"adjust": ["raw","qfq","hfq"]},
   "local": {"periods": ["day","1min","5min"]}
 }
)
```

解决：

- route 判断散落。
- adjust/start/source 兼容逻辑重复。

## 3.2 QuerySpec

所有入口统一转换：

```python
QuerySpec(
 capability="bars",
 symbol="sh600519",
 period="day",
 count=320,
 adjustment="raw",
 route_policy="auto"
)
```

HTTP、WS、MCP、Python API 全部共享。

## 4. 统一错误体系 v2

目标：类似 CCXT 风格的稳定错误树。

## 基础错误

```
TdxError
 |
 +-- ConfigError
 +-- ValidationError
 +-- TransportError
 +-- ProtocolError
 +-- DataError
 +-- SourceError
 +-- CacheError
 +-- ServiceError
```

## SourceError

统一替代重复 SourceUnavailable：

```
SourceUnavailable
SourceTimeout
SourceRateLimited
SourceDeprecated
SourceCapabilityMissing
```

每个错误包含：

```json
{
 "code":"SRC_TIMEOUT",
 "source":"tdx",
 "retryable":true,
 "fallback":true,
 "context":{}
}
```

要求：

- HTTP 映射统一。
- WS/MCP 保留 code。
- 日志携带 request_id/source/query。

## 5. API 设计优化

统一：

```python
service.query(QuerySpec)
```

高级 API：

```python
service.quotes()
service.bars()
service.snapshot()
```

内部全部进入 query engine。

避免：

- HTTP 直接调用 TdxClient。
- MCP 自己决定 facade。
- WS 自己决定 fallback。

## 6. Source Manager

Router 不负责创建客户端。

新增：

```
SourceManager
 |
 +-- TdxSource
 |     +-- ConnectionPool
 |
 +-- WebSource
 |     +-- HttpSession
 |
 +-- VipdocSource
 |
 +-- GoldenReplaySource
```

收益：

- TCP 连接复用。
- HTTP keep-alive。
- host health 保留。
- timeout 配置统一。

## 7. Cache v2

当前风险：cache 改变数据语义。

新设计：

```
Query
 |
CachePolicy
 |
Router
```

CacheKey：

```python
CacheKey(
 capability,
 symbol,
 period,
 adjustment,
 source_scope
)
```

原则：

- cache hit == cache miss。
- 不缓存不同 provenance 数据。
- raw series 存储，start/count 后处理。

## 8. 性能优化方案

### 8.1 TDX

- ConnectionPool 租约级锁。
- 批量请求自动合并。
- symbol 去重。
- host ranking 持久化。

### 8.2 Web

- persistent http client。
- connection reuse。
- source health score。
- adaptive retry。

### 8.3 数据处理

- zero-copy payload。
- parser registry 热路径优化。
- dataframe 延迟转换。
- batch API 优先。

## 9. Streaming v2

改为 Scheduler 模型：

```
Subscription
 |
StreamScheduler
 |
Fetch Backend
 |
DeltaMerger
 |
Subscriber
```

支持：

- 不同 interval。
- symbol 去重。
- polling/push backend。
- 真正 backpressure。

## 10. 服务面统一

目标：

```
HTTP
WS
MCP
 |
MarketDataService
 |
Query Engine
```

禁止：

- HTTP 直接 new TdxClient。
- WS 绕过 router。
- MCP 自行选择 source。

## 11. 任务系统优化

替换 thread-per-task：

```
BoundedExecutor
Future
CancellationToken
```

状态：

```
pending
running
cancel_requested
cancelled
done
failed
```

## 12. 可观测性

新增统一指标：

```
request_total
request_latency
source_success_total
source_failure_total
cache_hit_ratio
route_fallback_total
```

日志统一字段：

```
request_id
query_id
source
symbol
capability
error_code
```

## 13. 测试门禁

新增 Architecture Contract Tests：

1. route invariant。
2. cache equivalence。
3. service parity。
4. source capability coverage。
5. concurrency safety。
6. HTTP/WS/MCP parity。

## 14. 实施阶段

### Phase 0

- 合并 SourceUnavailable。
- 修复 M5 reader。
- 修复 cache provenance。
- 修复 explicit route。

### Phase 1

- Capability Registry。
- QuerySpec。
- Canonical Router。

### Phase 2

- Source Manager。
- Cache v2。
- Service layer。

### Phase 3

- Async architecture。
- Streaming Scheduler。
- Task Manager。

### Phase 4

- 删除重复 facade。
- 清理 orphan config。
- 完善性能基准。

## 15. 验收目标

最终达到：

- 单一数据执行内核。
- 单一错误体系。
- 单一路由策略。
- 所有服务入口行为一致。
- cache 不改变数据语义。
- 性能可量化回归。
- 新增能力只需注册，不需要修改多层代码。
