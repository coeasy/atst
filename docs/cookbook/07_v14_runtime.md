# Cookbook: v14 Runtime 实战

本文档展示 v14 Runtime 编排内核的实战用法，覆盖批量执行、语义缓存、
流式订阅、Typed Query 与 Domain Record。

## 1. 批量执行 + 语义缓存去重

批量获取多只股票的 K 线，自动利用语义缓存避免重复请求。

```python
from tstdx.runtime import create_runtime, QueryRequest
from tstdx.cache_semantic import SemanticResultCache

runtime = create_runtime(
    semantic_cache=SemanticResultCache(),
    default_cache_ttl=60.0,
)

# 3 只股票的 K 线请求
requests = [
    QueryRequest(
        operation="bars",
        args=("sh600000",),
        params={"count": 80, "period": "day"},
        metadata={"cache_ttl": 60.0},
    ),
    QueryRequest(
        operation="bars",
        args=("sh600519",),
        params={"count": 80, "period": "day"},
        metadata={"cache_ttl": 60.0},
    ),
    QueryRequest(
        operation="bars",
        args=("sh000001",),
        params={"count": 80, "period": "day"},
        metadata={"cache_ttl": 60.0},
    ),
]

# 首次批量执行（3 次 Provider 调用）
results = runtime.execute_batch(requests, max_concurrent=4)
for r in results:
    print(f"  {r.metadata.get('provider', '?')}: {r.success}")

# 再次执行（语义缓存全部命中，0 次 Provider 调用）
results = runtime.execute_batch(requests, max_concurrent=4)
for r in results:
    print(f"  {r.metadata.get('execution', '?')}: {r.success}")

# 缓存诊断
print(runtime.semantic_cache_stats())
# {'enabled': True, 'tier': 'l1', 'size': 3}
```

## 2. RuntimeGateway 网关适配

通过 RuntimeGateway 以统一接口访问 Runtime 能力。

```python
from tstdx.runtime import RuntimeGateway, create_runtime
from tstdx.cache_semantic import SemanticResultCache

gateway = RuntimeGateway(
    create_runtime(semantic_cache=SemanticResultCache())
)

# K 线
resp = gateway.bars("sh600519", count=30)
if resp.success:
    print(f"K线 {len(resp.data)} 条, provider={resp.metadata.get('provider')}")

# 实时行情
resp = gateway.quotes(("sh600000", "sh600519", "sz000001"))
if resp.success:
    print(f"行情 {len(resp.data)} 条")

# 指定 Provider 路由
resp = gateway.bars("sh600519", route="tdx")
resp = gateway.bars("sh600519", providers=("eastmoney", "tencent"))

# 财务信息
resp = gateway.finance_info("sh600519")

# 批量执行
from tstdx.runtime import QueryRequest
reqs = [
    QueryRequest(operation="quotes", args=("sh600000",)),
    QueryRequest(operation="quotes", args=("sh600519",)),
]
results = gateway.execute_batch(reqs, max_concurrent=2)

# 诊断
print(gateway.providers)              # ['tdx', 'eastmoney', ...]
print(gateway.semantic_cache_stats()) # {'enabled': True, 'tier': 'l1', ...}
```

## 3. 流式订阅

通过 Runtime 注册实时行情流订阅。

```python
from tstdx.runtime import create_runtime

runtime = create_runtime()

# 注册订阅
handle = runtime.subscribe(
    "quotes",
    ("sh600000", "sh600519", "sz000001"),
    provider="tdx",
    interval=1.0,       # 1 秒轮询间隔
    diff_only=True,     # 仅返回增量
    max_queue=1024,     # 最大队列长度
    subscription_id="main-watchlist",
)

# 生命周期管理
print(handle.id)                    # 'main-watchlist'
print(handle.state)                 # CREATED
handle.begin_start()
print(handle.state)                 # RUNNING
print(handle.snapshot())            # StreamLifecycleSnapshot

# 停止订阅
runtime.unsubscribe("main-watchlist")
print(handle.state)                 # CLOSED
```

## 4. Typed Query 契约

使用类型化查询契约替代手动构造 QueryRequest。

```python
from tstdx.runtime import create_runtime, request_from_typed
from tstdx.typed_query import CapabilityQuery

runtime = create_runtime()

# 从 CapabilityQuery 转换
req = request_from_typed(query, metadata={"cache_ttl": 60.0})
resp = runtime.execute(req)

# 直接执行
resp = runtime.execute_typed(query, metadata={"timeout": 5.0})
if resp.success:
    print(resp.data)
    print(resp.metadata.get("query_fingerprint"))
    print(resp.metadata.get("provenance"))
```

## 5. Domain Record 模型

使用类型化 Domain Record 替代 list[dict] 原始输出。

```python
from tstdx.domain.records import Bar, Quote, FinanceInfo

# Bar（K 线）
bar = Bar(
    date="2026-09-14",
    open=1700.0,
    high=1720.0,
    low=1690.0,
    close=1715.5,
    volume=12345678,
    amount=210000000.0,
)

# Quote（实时行情）
quote = Quote(
    symbol="sh600519",
    price=1715.5,
    change=15.5,
    change_pct=0.91,
    volume=12345678,
)

# FinanceInfo（财务信息）
info = FinanceInfo(
    symbol="sh600519",
    roe=20.5,
    gross_margin=90.0,
    net_margin=52.0,
)
```

## 6. 自定义 Provider 执行顺序

控制多 Provider 的回退顺序。

```python
from tstdx.runtime import create_runtime

# 优先东财，回退腾讯，最后通达信
runtime = create_runtime(
    provider_order=("eastmoney", "tencent", "tdx"),
    semantic_cache=SemanticResultCache(),
)

# 执行时按 order 依次尝试
resp = runtime.execute(request)
print(resp.metadata.get("provider"))       # 'eastmoney'（成功时）
print(resp.metadata.get("provider_attempts"))  # 回退诊断
```

## 7. 语义缓存 L2 持久化

使用持久化缓存跨进程复用结果。

```python
from tstdx.runtime import create_runtime
from tstdx.cache_persistent import PersistentSemanticCache

# L2 持久化缓存
cache = PersistentSemanticCache(directory="./.cache")

runtime = create_runtime(
    semantic_cache=cache,
    default_cache_ttl=300.0,  # 5 分钟
)

# 执行后结果写入磁盘
resp = runtime.execute(request)

# 下次启动自动命中缓存
resp = runtime.execute(request)
print(resp.metadata.get("execution"))  # 'semantic-cache'
```
