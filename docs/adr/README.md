# 架构决策记录（ADR）索引

本目录是**决策记录**：一份文件记下一次不可逆的取舍、它当时的理由，以及后来兑现或作废的经过。
正文一律"只加修订不抹史"——决定被推翻时不删原文，而是在状态行写明被谁、为什么推翻。

> 本索引本身是第 20 轮补上的。在此之前 `docs/adr/README.md` 的正文其实是 ADR-001~005 五条决策
> 自己（134 行），目录页没有索引可读；现在那五条搬到了
> [`ADR-001-005-baseline.md`](ADR-001-005-baseline.md)。这条缺陷不是没人看见过：
> `docs/archive/plans/OPTIMIZATION_PLAN_v4.md:173` 在 2026-09-03 就写下过同样的判断，之后没人行动。

## 编号规则

一个编号只属于一份文件里的一条决策。目录里有两类冲突：**同号重复**（两份 `ADR-013`）与
**复合编号覆盖**（`ADR-007-010` 占住 007~010 四个号，与 `ADR-006-010.md` 同号的四条逐条撞号）。
受影响的行共 7 行，下表末列逐行写明。
**本轮的处置是把冲突登记在索引里，而不是改号**——因为这两个号被其他在写的台账按文件名整段引用，
改号会把活的引用变成死指针。规则由 `tests/architecture/test_adr_index.py` 把守：
同一编号的声明处超过 2 个、索引漏掉任何一份文件、或索引指向不存在的文件，都当场红。

## 索引

| 编号 | 标题 | 所在文件 | 状态（文件自己的状态行） | 备注 |
|------|------|----------|--------------------------|------|
| ADR-001 | 零硬依赖架构 | [`ADR-001-005-baseline.md`](ADR-001-005-baseline.md) | Accepted | 核心包至今零第三方硬依赖 |
| ADR-002 | 同步/异步双 API 镜像 | 同上 | Accepted | `TdxClient` / `AsyncTdxClient` |
| ADR-003 | L1/L2/L3 三级解析分派 | 同上 | Accepted | L1 精确解析器 / L2 `generic.py` / L3 原字节 |
| ADR-004 | HTTP Web 源降级策略 | 同上 | Superseded | 5 级降级链与 `DataSourceRouter` 已随 v16 物理删除 |
| ADR-005 | PROTOCOL_SPEC YAML 规范 | 同上 | Accepted | `spec_audit` 验证 spec ↔ 实现 |
| ADR-006 | 流式背压与零丢失语义 | [`ADR-006-010.md`](ADR-006-010.md) | Accepted | `BackpressureOverflow` 有真实投递站点 |
| ADR-007 | Golden 自采集 + 合成衍生双轨 | [`ADR-006-010.md`](ADR-006-010.md) | Accepted | **与下面那份复合编号 `ADR-007-010` 撞号** |
| ADR-008 | 反馈与遥测默认关闭 | [`ADR-006-010.md`](ADR-006-010.md) | Accepted | `atst/feedback/`、`atst/observability/`；**另与复合编号 `ADR-007-010` 撞号** |
| ADR-009 | Rust 内核可选化 | [`ADR-006-010.md`](ADR-006-010.md) | Superseded | 状态行本轮修正：`atst_native/` 已不在仓库，第 20 轮实测全仓 `git ls-files` 里含 `native` 的路径只有 ADR-011 的文件名本身；移除由 ADR-011 设定的条件触发。另与复合编号 `ADR-007-010` 撞号 |
| ADR-010 | Spec 驱动的协议开发流程 | [`ADR-006-010.md`](ADR-006-010.md) | Accepted | draft → candidate → stable；**另与复合编号 `ADR-007-010` 撞号** |
| ADR-007-010 | CredentialStore 处置决议（deprecate 而非接线） | [`ADR-007-010.md`](ADR-007-010.md) | 已接受，对象已消失 | 标题里的 `007-010` 是**一个复合编号**，不四条决策；它与上一行的 ADR-007 撞号。`CredentialStore` 在 `atst/` 里已无实现（第 20 轮实测），本文件的引用见 `README.md`/`SECURITY.md` 的口径段 |
| ADR-011 | streaming 平行实现与 native 实验层处置决议 | [`ADR-011-streaming-native-处置决议.md`](ADR-011-streaming-native-%E5%A4%84%E7%BD%AE%E5%86%B3%E8%AE%AE.md) | 已接受 | native 那一半的移除条件已兑现（见 ADR-009 行） |
| ADR-012 | facade 路由链 vs DataSourceRouter 五级链口径对拍 | [`ADR-012-路由链合并口径对拍.md`](ADR-012-%E8%B7%AF%E7%94%B1%E9%93%BE%E5%90%88%E5%B9%B6%E5%8F%A3%E5%BE%84%E5%AF%B9%E6%8B%8D.md) | 已取代 | 对拍的两个对象均已删除 |
| ADR-013 | Provider-first Runtime Contract | [`ADR-013-provider-first-runtime-contract.md`](ADR-013-provider-first-runtime-contract.md) | Accepted | **与下面那份撞号**。章节不带编号；引用它的人通常写"契约""§Decision" |
| ADR-013 | Provider / source / Channel 术语与模型统一 | [`ADR-013-provider-source-terminology.md`](ADR-013-provider-source-terminology.md) | Accepted | **与上面那份撞号**。它有 `## 11. Error 术语`，所以 `docs/REFACTOR_PLAN_V17_CLOSURE.md` 里"ADR-013 §11 那句稳定公共资产"指的是**这一份**；按整文件路径引用的（`docs/archive/plans/OPTIMIZATION_PLAN_v1.md:6` 等）也是这一份 |
| ADR-014 | Semantic cache identity and provenance | [`ADR-014-semantic-cache-provenance.md`](ADR-014-semantic-cache-provenance.md) | Partially superseded | 语义缓存层已随 v16 Phase 2 删除，数据请求零缓存 |
| ADR-015 | Unified Runtime Execution Cutover | [`ADR-015-unified-runtime-execution.md`](ADR-015-unified-runtime-execution.md) | Superseded in part | 文中链路仍画着缓存节点，那一节已作废 |
| ADR-016 | 配置面只覆盖执行参数 | [`ADR-016-config-surface-covers-execution-only.md`](ADR-016-config-surface-covers-execution-only.md) | Accepted | 配置面不承诺缓存/降级等不存在的旋钮 |

## 怎么读这个目录

* 想知道**现在的事实**：别从 ADR 开始，从 [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) 与
  [`docs/api/interfaces.md`](../api/interfaces.md) 开始；ADR 只回答"为什么长这样"和"当年否决了什么"。
* 状态为 Superseded / 已取代 的行，正文按历史保留；现行口径一律在状态行指向的替代决策里。
* 新增决策时用下一个未占用的号（当前最大为 016，下一个是 017），并在本索引加一行——
  漏加索引会被判据当场拦下，因为索引就是分母。
