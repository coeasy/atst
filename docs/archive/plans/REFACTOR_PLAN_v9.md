# atst 重构方案 v9（2026-09，决策已确认，✅ 已全部落地）

> 承接 [REFACTOR_PLAN_v8.md](REFACTOR_PLAN_v8.md)（已全部落地）与
> [ARCHITECTURE_AUDIT_v8.md](ARCHITECTURE_AUDIT_v8.md)（审计结论）。
> **状态（2026-09-06）**：Q1-a/Q1-b/Q2/Q3/Q4-1/Q4-2/Q4-3/Q4-4 全部完成，
> 全量 pytest / 对抗矩阵 / golden 门禁 / 严格可达性门禁通过。执行记录见 CHANGELOG。

## 已确认的决策记录

| 议题 | 决策 |
|---|---|
| 双路由链（facade vs DataSourceRouter） | **v9 直接合并**：facade 的路由/熔断改调 DataSourceRouter，消除双实现（合并前必须先完成口径对拍，作为 Q1 内部子步骤） |
| client 同步/异步镜像 | **一次性全量迁移**到共享骨架 `_ClientMixin`（约 25 组方法） |
| 异步统一门面 | **保持 10 核心方法 + `arun()` 泛化**，仅补文档明确能力边界 |
| v9 低优先级项 | **全部纳入**：web Source 层收尾、web 包惰性导入、半成品收敛决策、`sinks/` 包改名 |

---

## Q1 路由链合并（最高优先级）

### Q1-a 口径对拍（合并前置，不可跳过）
1. 新增 `tests/facade/test_route_parity.py`：对 (symbol, period, count) 三元组
   网格（A股/指数/停牌 × day/5min × count 0/1/50/320/start≠0），分别走
   `UnifiedQuoteAPI`（local/tdx/web）与 `DataSourceRouter`（tdx/web/reader/cache），
   在离线 golden/fake 通路下逐项对拍：返回类型、字段集、datetime 排序、
   条数截断口径、空结果语义、异常类型与 message 前缀。
2. 产出差异清单（写入本文件附录或 `docs/adr/`）；差异按「router 为准 / facade 为准」逐条裁定，先改两侧实现对齐。

### Q1-b 合并实现
3. `UnifiedQuoteAPI` 内部：`_iter_routes`/`_try_routes` 保留为**熔断壳**
   （W11 语义：连续失败冷却、route_errors 聚合是 facade 独有，必须保留），
   但每个路由的**取数函数**改为委托 `DataSourceRouter` 对应级别
   （tdx→router.tdx 通路、web→router.web、local→router.reader）。
4. `sources/DataSourceRouter` 的 5 级链、缓存接线、golden 回放逻辑不动，
   仅按需暴露更细粒度的单级调用入口（如 `kline_via("tdx", ...)`）。
5. sync_daily / adjusted_bars 等 facade 编排方法保持签名不变。
6. 验收：Q1-a 对拍测试全绿 + `tests/facade`、`tests/sources` 全量 + 现有
   W11 熔断语义测试（`test_w11_w12_w13.py`）不回归；`docs/adr/` 补 ADR 记录合并决策。

风险：中高。回滚点：Q1-a 完成后独立提交一次。

## Q2 client sync/async 共享骨架（一次性全量迁移）

1. 新建 `client/_mixin.py`：`_ClientMixin` 承载方法骨架——协议构造
   （复用 `client_core.py` 纯函数）+ 结果变换（`as_format` 分派、`_emit`）
   + 重试/last_errors 编排；传输 seam 抽象为 `_req(frame, **ctx)` /
   `_areq(frame, **ctx)` 两个钩子（子类各实现一次）。
2. 25 组方法**一次性**迁入：`sync.py`/`async_.py` 保留 `TdxClient._req` 与
   `AsyncTdxClient._areq` + 生命周期（connect/close/__aenter__）+ 各自特有
   方法（如 `quotes_concurrent` 线程池属 sync 特有，留 sync）。
3. 5 组 @overload 重载（bars 等）签名整体上移到 Mixin，`@overload` 保留在
   各子类或 Mixin（以 mypy 通过为准）。
4. `client/factory.py`、`client/__init__.py` re-export 面不变；37 个模块级
   符号对齐测试（v8 已建）继续作为门禁。
5. 验收：`test_sync_async_parity.py` 全绿 + `tests/client` 全量 +
   monkeypatch 语义测试（`test_compat_layer.py`、`test_client_f1.py::dispatch`）
   不回归；`client/sync.py`/`client/async_.py` 各降到 ~300 行级。

风险：中（一次性批次大）。回滚点：迁移前整包打 tag/提交。

## Q3 异步门面文档化（零代码）

- `facade/async_api.py` docstring 与 `docs/api/README.md` 明确写：
  「Async 门面为紧凑设计——10 核心方法镜像，长尾方法统一用
  `await api.arun(method, *args, **kwargs)`，不逐方法镜像」。
- `tests/seams/test_contracts.py` 若有 parity 断言，改为断言
  「10 核心 + arun 存在性」，防止未来误扩/误删。

## Q4 低优先级四项

| 项 | 内容 | 风险 |
|---|---|---|
| Q4-1 web Source 层收尾 | `MinuteKlineSource`/`KlineSource` 抽共用分页拉取器；`EastmoneyNoticeSource`/`EastmoneyResearchSource` 归入 `_EastmoneyJson`（复用主机池 failover） | 低（`source_name`/capability 常量与 `_ADAPTERS` 注册表不变） |
| Q4-2 web 包惰性导入 | `atst/web/__init__.py` 18 子模块全量 re-export 改 `__getattr__` + `_LAZY` 映射（对齐顶层 `atst/__init__` 模式）；`_ADAPTERS` 注册表改为惰性填充；注意 `tests/web/test_registry_consistency.py` 的注册表一致性门禁适配 | 低-中 |
| Q4-3 半成品收敛决策 | `streaming/engine.py` 与 `QuoteStream` 平行实现：评估合并或明确降级为「高级组件库」并文档定位；`streaming/push.py` 同理；`atst/native`（Rust 实验层）去留（批次 H）——产出 ADR 后同步清理/更新 `_reach_allow.txt` 注释 | 决策项 |
| Q4-4 `sinks/` 包改名 | `atst/sinks`（DataFrame/Parquet/DuckDB 输出）→ `atst/output`：新包 + `atst/sinks` 保留一个版本的兼容 shim（DeprecationWarning），README/docs/cookbook/tests 8 处 import 同步迁移；`_reach_allow.txt` 条目同步 | 低（有 shim 过渡） |

## 执行顺序与里程碑

```
Q3（零风险，半天） → Q4-1 → Q4-4 → Q1-a（对拍，独立提交） → Q1-b（合并）
→ Q2（大批次，迁移前打 tag） → Q4-2 → Q4-3（决策产出 ADR）
```

每步门禁：`pytest tests/ -q` + `python scripts/audit_reachability.py --strict` +
对抗矩阵 + golden 门禁。
