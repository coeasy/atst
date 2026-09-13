# tstdx 分支合并优化方案

> 日期: 2026-09-13
> 仓库: coeasy/tstdx
> 基线: main @ `a125e3f` (docs: add v14 deep upgrade progress tracking)
> 范围: PR#1 / PR#6 / PR#7 及全部历史分支

---

## 一、总览

### 1.1 全部 PR 状态

| PR# | 分支 | 标题 | 状态 | 领先 main | 落后 main | 冲突文件 | 合并可行性 |
|-----|------|------|------|-----------|-----------|----------|------------|
| #2 | (已合并) | — | ✅ 已入 main | 0 | — | — | — |
| #3 | (已合并) | — | ✅ 已入 main | 0 | — | — | — |
| #4 | (已合并) | — | ✅ 已入 main | 0 | — | — | — |
| #5 | (已合并) | — | ✅ 已入 main | 0 | — | — | — |
| #7 | `v14-runtime-phase1` | v14 运行时内核骨架 | 🟢 open | 111 | 0 | 0 | **可立即合并** |
| #6 | `refactor/runtime-integration-v12` | v13 clean-break 统一运行时 | 🟡 open | 162 | 2 | 1 | 快速解冲突后可合并 |
| #1 | `refactor/industry-benchmark-v12` | v12 行业基准重构 | 🔴 open | 737 | 42 | 5 | 需重做或拆分 |

### 1.2 全部远程分支状态

| 分支 | 状态 | 建议 |
|------|------|------|
| `main` | 当前基线 | — |
| `v14-runtime-phase1` | PR#7 head | ✅ 合并后保留 |
| `refactor/runtime-integration-v12` | PR#6 head | 合并后删除 |
| `refactor/industry-benchmark-v12` | PR#1 head | 冻结，拆分后删除 |
| `docs/refactor-plan-v11-20260912` | 已在 main | 🗑 删除 |
| `refactor/cache-provenance-v11` | 已在 main | 🗑 删除 |
| `refactor/industry-benchmark-v11` | 3 commits ahead | 🗑 已被 v12 超越，删除 |
| `refactor/runtime-contracts-v11` | 已在 main | 🗑 删除 |
| `refactor/stream-state-v11` | 已在 main | 🗑 删除 |
| `refactor/v14-runtime-kernel` | 已在 main | 🗑 删除 |
| `refactor/v14-runtime-kernel-impl` | 已在 main | 🗑 删除 |
| `sync-main-into-v12-20260909` (-b/-c/-d) | 已在 main | 🗑 删除 |
| `tmp-v14-runtime-start` | 已在 main | 🗑 删除 |

---

## 二、各分支功能详解

### 2.1 PR#7 `v14-runtime-phase1` — v14 运行时内核

**定位**: v14 第一阶段，为 tstdx 建立完整的执行编排运行时骨架。

**规模**: 78 文件 / +3,231 / −637，111 个 commit

**commit 类型分布**:
- feat: 49 (核心功能建设)
- test: 22 (契约测试)
- refactor: 20 (架构调整)
- fix: 14 (修正)
- docs: 3 / style: 2 / perf: 1

**新增模块** (26 个新文件):

| 包 | 文件 | 职责 |
|----|------|------|
| `tstdx/runtime/` | `__init__.py`, `bootstrap.py`, `context.py`, `request.py`, `response.py`, `runtime.py`, `typed.py` | 运行时内核：引导工厂、执行上下文、请求/响应模型、Runtime 入口、Typed 适配器 |
| `tstdx/execution/` | `__init__.py`, `graph.py`, `node.py`, `plan.py`, `planner.py`, `semantic.py` | 执行编排：DAG 图、节点原语、执行计划、ExecutionPlanner、语义桥接 |
| `tstdx/provider/` | `__init__.py`, `base.py`, `local.py`, `router.py`, `tdx.py`, `web.py` | Provider 运行时适配器：基础契约、本地/Web/TDX 适配器、路由 |
| `tstdx/facade/` | `runtime_adapter.py` | Facade → Runtime 兼容适配器 |
| `tests/v14/` | 5 个测试文件 | 契约测试：bootstrap、execution、semantic、typed_query、facade adapter |
| `docs/` | `REFACTOR_PLAN_v14_FULL_UPGRADE.md` | v14 完整升级方案 |

**核心架构** (来自 v14 方案文档):

```
Python API / CLI / REST / WS / MCP
          ↓
    Runtime boundary
    QueryRequest
          ↓
    ExecutionPlanner (DAG/orchestration)
          ↓
  canonical QueryPlanner (QuerySpec → QueryPlan, single Provider)
          ↓
  ┌───────┴───────┐
  ↓               ↓
SemanticResultCache  Provider adapter (tdx/local/web)
  ↓               ↓
  └───────┬───────┘
          ↓
   QueryResult + Provenance
          ↓
    Runtime Response
```

**相对 main 的改进**:
1. 建立了完整的 `runtime/` 包，从零搭建了 QueryRequest → ExecutionPlanner → Provider Adapter → QueryResult 链路
2. 实现了 Provider 运行时适配器层（TDX / Web / Local），统一了 Provider 调用契约
3. 引入了 ExecutionPlanner（DAG 编排），与已有 QueryPlanner（语义规划）形成两层分工
4. 实现了 Runtime Bootstrap Factory（零依赖引导），解耦了运行时初始化
5. 建立了 Provider Capability Contract 和 Attempt Provenance（调用溯源）
6. 实现了语义缓存与运行时集成（canonical semantic cache injection）
7. 实现了 Typed Query Runtime Adapter，支持 `Runtime.execute_typed()`
8. 建立了 5 个 v14 契约测试套件，锁定了运行时行为边界

**冲突**: **0 个冲突**，可直接 fast-forward 或 squash 合并。

---

### 2.2 PR#6 `refactor/runtime-integration-v12` — v13 clean-break 统一运行时

**定位**: v13 clean-break 重构，删除全部 legacy facade/server，收敛为统一 Provider-first Runtime 架构。

**规模**: 85 文件 / +8,816 / −13,543（净减少 4,727 行），162 个 commit

**commit 类型分布**:
- refactor: 80 (大规模架构重构)
- test: 37 (新架构契约测试)
- fix: 22 (修正)
- docs: 19 (方案文档)
- feat: 2 / chore: 1 / style: 1

**新增模块** (35 个新文件):

| 类别 | 关键文件 | 职责 |
|------|---------|------|
| 运行时核心 | `tstdx/runtime.py`, `tstdx/orchestration.py`, `tstdx/direct_provider.py` | 统一运行时、跨 Provider 编排策略、直连 Provider 绑定 |
| 缓存 | `tstdx/cache_persistent.py`, `tstdx/stream_contract.py` | 持久化语义缓存 L2、流契约 |
| 客户端 | `tstdx/client_api.py` | v13 Client/AsyncClient |
| 能力目录 | `tstdx/capability_catalog.py`, `tstdx/typed_query.py` | 能力目录、Typed Query 合约 |
| 批处理 | `tstdx/batch.py` | BatchSpec + 可审计批量报价 |
| 错误 | `tstdx/error_envelope.py` | 规范化安全错误信封 |
| 集成 | `tstdx/integration/runtime_*.py` (4 文件) | HTTP/WS/Task 运行时传输层 |
| CLI | `tstdx/cli/runtime_commands.py` | v13 客户端命令 |
| 文档 | 6 个文档 | v13 方案、语义对齐矩阵、能力矩阵、执行状态 |

**删除的文件** (29 个):
- 全部 legacy facade: `facade/api.py`, `facade/market.py`, `facade/bridge.py`, `facade/binary.py`, `facade/response.py`, `facade/routing.py`, `facade/async_api.py`
- 全部 legacy server: `integration/http_server.py`, `integration/ws_server.py`
- 全部 legacy CLI: `cli/cmds_hosts.py`, `cli/cmds_market.py`, `cli/cmds_web.py`, `cli/_common.py`
- `sources/__init__.py` (legacy DataSourceRouter)
- 13 个 legacy 测试文件

**相对 main 的改进**:
1. **删除全部 legacy 架构**：移除了 facade 层、legacy server、DataSourceRouter、兼容路由，实现 clean-break
2. **统一运行时**：所有业务能力通过 `UnifiedRuntime` → `DirectProvider` 执行，无路由重复
3. **语义缓存 L2**：实现了持久化语义缓存（指纹 + 完整性校验），L1 有界 LRU 替代 flush
4. **SingleFlight + Negative Cache**：防止缓存击穿和无效请求穿透
5. **Provider Orchestration**：显式跨 Provider fallback 策略层，替代隐式路由
6. **v13 Client**：`Client` / `AsyncClient` 作为唯一执行入口
7. **Canonical Transports**：HTTP / WS / MCP / CLI 全部路由通过 Client，不再有独立业务实现
8. **ErrorEnvelope**：规范化错误信封，统一错误处理
9. **完整 Legacy 能力迁移**：全部 legacy 能力迁移到 v13 运行时，通过 Direct Binding 执行
10. **v14 前瞻**：尾部 2 个 feat commit 引入了 `typed_query.py` 和 `provider adapter contract foundation`

**冲突**: 1 个 — `tstdx/typed_query.py` (add/add)
- main 版本 100 行（15 个 Typed Query 类，包含 `IncomeStatementQuery` / `CashFlowQuery` / `FundHoldingsQuery` / `BondKlineQuery` / `FuturesKlineQuery` / `NewsQuery` / `ResearchReportQuery` / `F10Query`）
- PR#6 版本 75 行（7 个 Typed Query 类，仅基础集合）
- **解决方案**: 采用 main 版本（更完整），PR#6 只是缺少了后续扩展的类

---

### 2.3 PR#1 `refactor/industry-benchmark-v12` — v12 行业基准重构

**定位**: v12 是最大规模的行业基准重构，涵盖 Provider/Channel/Capability 术语统一、传输层硬化、全套契约测试。

**规模**: 234 文件 / +33,697 / −5,279，736 个 commit

**commit 类型分布**:
- test: 284 (契约测试 — 占比最大)
- fix: 188 (硬化修正)
- feat: 63 (新功能)
- refactor: 58 (重构)
- docs: 58 (方案文档 + Provider 文档)
- ci: 22 / style: 19 / harden: 15 / chore: 9

**新增模块** (112 个新文件):

| 类别 | 关键文件 | 职责 |
|------|---------|------|
| 服务层 | `tstdx/service.py`, `tstdx/planned_service.py`, `tstdx/async_service.py` | Planned Service 架构 |
| Provider API | `tstdx/provider_api.py` | Provider Direct API |
| 缓存 | `tstdx/cache_v2.py`, `tstdx/semantic_cache.py` | 缓存 v2 + 语义缓存 |
| 健康检查 | `tstdx/health.py`, `tstdx/freshness.py`, `tstdx/failure.py` | 熔断/新鲜度/失败策略 |
| 传输硬化 | `tstdx/transport/_*.py` (10 文件) | 连接/池/主机/限流/排序 硬化模块 |
| Facade | `tstdx/facade/planned.py`, `tstdx/facade/strict.py`, `tstdx/facade/strict_async.py` | Planned/Strict Facade |
| 集成 | `tstdx/integration/http_app.py`, `ws_app.py`, `mcp_app.py`, `tasks.py` | 应用层集成 |
| 执行 | `tstdx/execution.py` | 执行引擎 |
| 工具 | `tstdx/tools/host_audit.py` | 主机审计 |
| Provider 文档 | `docs/providers/*.md` (8 文件) | TDX/腾讯/新浪/东财/百度/集思录/中行/i问财 通道文档 |
| 架构文档 | `docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md` (1127 行) | v12 完整架构方案 |
| 契约测试 | ~100 个测试文件 | 覆盖 client/transport/service/codec/domain/provider/facade/streaming/integration |

**相对 main 的改进**:
1. **Provider/Channel/Capability 术语统一**：消除 source/Provider 二义性，内部只有 Provider 实体
2. **TDX 为主 Provider，禁止跨 Provider 静默替代**：核心硬约束
3. **传输层全面硬化**：连接超时验证、池工厂选择器、主机排名/健康、限流契约、请求帧标识
4. **Planned Service 架构**：显式 health/circuit/freshness 策略
5. **Strict Facade**：fail-closed 语义，拒绝静默回退
6. **完整 Provider 文档**：8 个 Provider 各有独立通道文档
7. **~100 个契约测试**：覆盖连接/池/主机/限流/错误信封/降级边界

**冲突**: 5 个 — `__init__.py`, `domain/period.py`, `providers/__init__.py`, `query.py`, `sources/__init__.py`
- 全部为 content 冲突（两侧都修改了同一文件的不同部分）
- `query.py`: main 有 7 个 PR#1 没有的方法（currentness、channel 选择、options 规范化），PR#1 有 5 个 main 没有的方法（unified channel、default channel、bar period 验证、default provider 解析、channel mismatch 拒绝）
- `sources/__init__.py`: main 有 6 个 PR#1 没有的方法，PR#1 有 9 个 main 没有的方法
- 根因：main 已经走了 42 个 commit 引入了 v14 typed_query / runtime contracts / providers 体系，PR#1 在完全相同位置做了大幅重构

---

## 三、分支间依赖与重叠分析

### 3.1 文件重叠矩阵

| 重叠区域 | 文件数 | 风险等级 |
|---------|--------|---------|
| PR#1 ∩ PR#6 ∩ PR#7 | 4 | 🔴 最高 |
| PR#1 ∩ PR#6 (不含 PR#7) | 15 | 🟡 高 |
| PR#6 ∩ PR#7 (不含 PR#1) | 7 | 🟡 中 |
| PR#1 ∩ PR#7 (不含 PR#6) | 0 | 🟢 无 |

**三分支共同冲突文件** (最高风险):

| 文件 | PR#1 改动 | PR#6 改动 | PR#7 改动 | main 当前 |
|------|----------|----------|----------|----------|
| `tstdx/facade/__init__.py` | 重构 | 删除 legacy + 新建 | 适配器导出 | legacy facade |
| `tstdx/providers/__init__.py` | Provider 术语重构 | 删除 legacy + 新建 | — | 521 行 |
| `tstdx/query.py` | Channel 语义重构 | — | — | 376 行 |
| `tests/facade/test_w11_w12_w13.py` | 重写测试 | 删除 legacy 测试 | — | legacy 测试 |

### 3.2 架构演进关系

```
main (v1.0 基线)
  │
  ├── PR#1: v12 行业基准重构 (2026-09-08~10, 737 commits)
  │     └── Provider/Channel 术语统一 + 传输硬化 + 契约测试
  │
  ├── PR#6: v13 clean-break (2026-09-09~12, 162 commits)
  │     └── 删除全部 legacy + 统一 Runtime + v14 前瞻
  │         └── 含 typed_query.py 基础版 (7 个类)
  │
  └── PR#7: v14-runtime-phase1 (2026-09-12~13, 111 commits)
        └── v14 运行时内核 + ExecutionPlanner + Provider Adapter
            └── 含 typed_query.py 扩展版 (15 个类, 已在 main)
```

**关键发现**:
- PR#7 是在 PR#6 基础上的延续（PR#6 尾部 2 个 commit 引入了 v14 基础，PR#7 完整实现了 v14 Phase 1）
- PR#1 是独立路线，与 PR#6/#7 在核心文件上做了不同的架构选择
- main 已经独立引入了 v14 typed_query 扩展（15 个类），超越了 PR#6 的版本

---

## 四、合并优化方案

### 4.1 合并优先级与策略

```
Phase 1: 合并 PR#7 (零冲突, 立即执行)
    ↓
Phase 2: 解决 PR#6 单文件冲突后合并 (typed_query.py 采用 main 版本)
    ↓
Phase 3: PR#1 拆分为可独立合并的子分支
    ↓
Phase 4: 清理 12 个已合并/过时远程分支
```

### 4.2 Phase 1: 合并 PR#7（立即执行）

**操作**:
```bash
# PR#7 零冲突，直接 squash merge
git checkout main
git pull origin main
git merge --squash refs/pull/7/head
git commit -m "feat(v14): merge runtime kernel phase 1 — ExecutionPlanner, Provider adapters, typed runtime, semantic cache integration"
git push origin main
```

**验证点**:
- [ ] `tstdx/runtime/` 包完整导入
- [ ] `tstdx/execution/` 包完整导入
- [ ] `tstdx/provider/` 包完整导入
- [ ] `tests/v14/` 5 个测试套件全部通过
- [ ] `Runtime.execute_typed()` 可调用
- [ ] 语义缓存注入正常

### 4.3 Phase 2: 解决 PR#6 冲突后合并

**冲突文件**: `tstdx/typed_query.py` (add/add)

**冲突根因**: main 已有 15 个 Typed Query 类（含 `IncomeStatementQuery` / `CashFlowQuery` / `FundHoldingsQuery` / `BondKlineQuery` / `FuturesKlineQuery` / `NewsQuery` / `ResearchReportQuery` / `F10Query`），PR#6 只有 7 个基础类。

**解决方案**: **采用 main 版本**。PR#6 对 `typed_query.py` 的改动仅为 docstring 补充，main 版本更完整。

**操作**:
```bash
# 1. 基于 PR#6 分支创建 rebase 工作区
git checkout refactor/runtime-integration-v12
git fetch origin

# 2. Rebase 到最新 main（合并 PR#7 后的 main）
git rebase origin/main

# 3. 冲突解决：typed_query.py 采用 main 版本
git checkout --theirs tstdx/typed_query.py
git add tstdx/typed_query.py
git rebase --continue

# 4. 验证测试
python -m pytest tests/runtime/ -v

# 5. 强制推送
git push --force-with-lease origin refactor/runtime-integration-v12

# 6. 在 GitHub 上 squash merge PR#6
```

**验证点**:
- [ ] `tstdx/runtime.py` 导入正常
- [ ] `tstdx/client_api.py` Client/AsyncClient 可用
- [ ] legacy facade 全部删除，无残留导入
- [ ] `tests/runtime/` 全部通过
- [ ] HTTP/WS/MCP/CLI 传输层正常
- [ ] 语义缓存 L2 持久化正常

### 4.4 Phase 3: PR#1 拆分策略

PR#1 有 737 个 commit、234 个文件，落后 main 42 个 commit，5 个冲突文件。直接合并不现实，需要拆分为可独立合并的子分支。

**拆分方案**:

| 子分支 | 来源 commit 范围 | 内容 | 预期冲突 | 合并顺序 |
|--------|-----------------|------|---------|---------|
| `split/v12-provider-docs` | docs/providers/*.md + ADR-013 | Provider 通道文档 + 术语 ADR | 0 | 第 1 批 |
| `split/v12-transport-hardening` | `tstdx/transport/_*.py` (10 文件) | 传输层硬化模块 | 0 | 第 2 批 |
| `split/v12-contract-tests` | tests/ 下新增测试 | 契约测试套件 | 低 | 第 3 批 |
| `split/v12-service-layer` | `tstdx/service.py` / `planned_service.py` / `health.py` / `freshness.py` | 服务层架构 | 中 | 第 4 批 |
| `split/v12-facade-strict` | `tstdx/facade/strict*.py` / `planned.py` | Strict Facade | 高（与 PR#6 删除的 legacy 冲突） | 待 PR#6 合并后评估 |
| `split/v12-core-refactor` | `query.py` / `providers/__init__.py` / `sources/__init__.py` | 核心语义重构 | 高 | 最后评估，可能需重做 |

**操作**:
```bash
# 1. 从 PR#1 分支 cherry-pick 文档类 commit（零冲突）
git checkout main
git checkout -b split/v12-provider-docs
git cherry-pick <docs/providers/*.md commit hashes>
git push origin split/v12-provider-docs
# 创建 PR

# 2. Cherry-pick 传输硬化模块
git checkout -b split/v12-transport-hardening main
# 使用 git checkout 从 PR#1 分支提取特定文件
git checkout refs/pull/1/head -- tstdx/transport/_async_close_hardening.py \
  tstdx/transport/_async_pool_hardening.py \
  tstdx/transport/_connection_contract_hardening.py \
  tstdx/transport/_host_selector_hardening.py \
  tstdx/transport/_pool_factory_hardening.py \
  tstdx/transport/_pool_family_hardening.py \
  tstdx/transport/_pool_hardening.py \
  tstdx/transport/_pool_provenance_hardening.py \
  tstdx/transport/_ranking_hardening.py
git add -A
git commit -m "feat(transport): add hardening modules from v12 industry benchmark"
git push origin split/v12-transport-hardening
# 创建 PR

# 3-6. 类似方式拆分其余模块
```

### 4.5 Phase 4: 分支清理

**删除 12 个已合并/过时远程分支**:
```bash
# 已在 main 中的分支
git push origin --delete docs/refactor-plan-v11-20260912
git push origin --delete refactor/cache-provenance-v11
git push origin --delete refactor/runtime-contracts-v11
git push origin --delete refactor/stream-state-v11
git push origin --delete refactor/v14-runtime-kernel
git push origin --delete refactor/v14-runtime-kernel-impl
git push origin --delete sync-main-into-v12-20260909
git push origin --delete sync-main-into-v12-20260909-b
git push origin --delete sync-main-into-v12-20260909-c
git push origin --delete sync-main-into-v12-20260909-d
git push origin --delete tmp-v14-runtime-start

# 已被 v12 超越的旧分支
git push origin --delete refactor/industry-benchmark-v11
```

---

## 五、合并后架构统一

### 5.1 目标架构（合并 PR#7 + PR#6 后）

```
Python API / CLI / REST / WS / MCP
          ↓
    tstdx.client_api.Client / AsyncClient
          ↓
    tstdx.runtime.Runtime
    (QueryRequest → ExecutionPlanner → Provider Router)
          ↓
  ┌───────┴───────┐
  ↓               ↓
SemanticResultCache  Provider Adapter
(L1 LRU + L2 persistent)  tdx / local / web / eastmoney / ...
  ↓               ↓
  └───────┬───────┘
          ↓
   QueryResult[T] + Provenance
          ↓
    Runtime Response
```

### 5.2 合并后包结构

```
tstdx/
├── __init__.py              # 公共 API 导出
├── runtime/                 # [PR#7] v14 运行时内核
│   ├── __init__.py
│   ├── bootstrap.py         # 零依赖引导工厂
│   ├── context.py           # 执行上下文
│   ├── request.py           # QueryRequest 模型
│   ├── response.py          # RuntimeResponse 模型
│   ├── runtime.py           # Runtime 主入口
│   └── typed.py             # Typed Query 运行时适配器
├── execution/               # [PR#7] 执行编排
│   ├── __init__.py
│   ├── graph.py             # 执行 DAG 图
│   ├── node.py              # 节点原语
│   ├── plan.py              # 执行计划
│   ├── planner.py           # ExecutionPlanner
│   └── semantic.py          # 语义桥接
├── provider/                # [PR#7] Provider 运行时适配器
│   ├── __init__.py
│   ├── base.py              # Provider 基础契约
│   ├── router.py            # Provider 路由
│   ├── tdx.py               # TDX 适配器
│   ├── local.py             # 本地适配器
│   └── web.py               # Web 适配器
├── runtime.py               # [PR#6] UnifiedRuntime
├── orchestration.py         # [PR#6] 跨 Provider 编排策略
├── direct_provider.py       # [PR#6] 直连 Provider 绑定
├── client_api.py            # [PR#6] Client/AsyncClient
├── capability_catalog.py    # [PR#6] 能力目录
├── typed_query.py           # [main] 15 个 Typed Query 类
├── cache_persistent.py      # [PR#6] 持久化语义缓存 L2
├── cache_semantic.py        # [PR#7→main] 语义缓存
├── error_envelope.py        # [PR#6] 规范化错误信封
├── batch.py                 # [PR#6] BatchSpec
├── stream_contract.py       # [PR#6] 流契约
├── providers/__init__.py    # [main] Provider/Channel/Capability 注册表
├── query.py                 # [main] QuerySpec/QueryPlanner
├── facade/                  # [PR#6+PR#7] 运行时 facade 适配器
│   ├── __init__.py
│   └── runtime_adapter.py   # [PR#7] Facade → Runtime 适配器
├── integration/             # [PR#6] 运行时传输层
│   ├── runtime_http.py
│   ├── runtime_ws.py
│   ├── runtime_tasks.py
│   └── serialization.py
├── transport/               # [main + PR#1 split] 传输层
│   ├── ... (现有)
│   └── _*_hardening.py      # [PR#1 split] 硬化模块
├── service.py               # [PR#1 split] Planned Service
├── health.py                # [PR#1 split] 健康检查
├── freshness.py             # [PR#1 split] 新鲜度
└── failure.py               # [PR#1 split] 失败策略
```

---

## 六、未来改进优化计划

### 6.1 短期 (合并后 1-2 周)

| 优先级 | 任务 | 依赖 | 验收标准 |
|--------|------|------|---------|
| P0 | 合并 PR#7 | 无 | v14 测试全绿 |
| P0 | 合并 PR#6 | PR#7 合并后 | legacy facade 全部删除，运行时测试全绿 |
| P1 | 拆分 PR#1 子分支 | PR#6 合并后 | 至少 3 个子 PR 可独立合并 |
| P1 | 删除 12 个过时远程分支 | 无 | 远程分支数 ≤ 5 |
| P1 | 更新 `docs/V14_UPGRADE_PROGRESS.md` | PR#7 合并后 | Phase 1 标记完成 |

### 6.2 中期 (合并后 2-4 周)

| 优先级 | 任务 | 描述 |
|--------|------|------|
| P0 | **Typed Capability 扩展** | 补全 `FinancialQuery` / `FundQuery` / `BondQuery` / `FuturesQuery` / `OptionsQuery` / `NewsQuery` / `ResearchQuery` / `F10Query` / `MacroQuery` / `SearchQuery`，每个能力需完整链路（Typed Query → Registry → Direct Binding → Runtime Execution → Domain Result → Surface Adapter → Contract Test） |
| P0 | **Domain Model 替换** | 逐步用 Domain Record 替换 `list[dict]`：`FinancialRecord` / `FundRecord` / `BondRecord` / `OptionRecord` / `NewsRecord` / `ResearchRecord` |
| P1 | **Provider Adapter 完整实现** | 拆分 `providers/` 为 `tdx/` / `eastmoney/` / `sina/` / `tencent/` / `boc/` / `iwencai/` / `derived/`，统一 `capabilities()` / `execute()` / `health()` / `metadata()` 接口 |
| P1 | **PR#1 传输硬化整合** | 将 PR#1 的 10 个传输硬化模块整合到合并后的 main，补全连接/池/主机/限流契约测试 |
| P1 | **PR#1 服务层整合** | 将 Planned Service / health / freshness / failure 策略整合到 v14 运行时 |
| P2 | **Contract Automation** | 自动验证 Typed Query = Registry = Binding = Runtime = Surface = Test 的完整链路一致性 |

### 6.3 长期 (合并后 1-3 个月)

| 优先级 | 任务 | 描述 |
|--------|------|------|
| P0 | **v14 完成验收** | 所有 capability 满足完整链路：禁止 hidden routing / dict-only business API / duplicate provider selection / untyped capability expansion / surface-specific business logic |
| P1 | **Provider 文档完善** | 基于 PR#1 的 `docs/providers/*.md` 模式，为每个 Provider 建立完整通道文档 |
| P1 | **性能基准** | 建立 v14 运行时性能基准，与 v12 行业基准对比 |
| P2 | **Streaming v14** | 将 `StreamState` 集成到 v14 运行时，统一流式生命周期管理 |
| P2 | **CI/CD 硬化** | 整合 PR#1 的 CI workflow（pool factory hardening in wheel smoke、direct connection hardening、release wheel smoke）|

### 6.4 架构收敛验收清单

合并全部 PR 后，tstdx 应满足以下验收标准：

- [ ] **单一运行时**: 所有业务能力通过 `Runtime.execute()` / `Runtime.execute_typed()` 执行
- [ ] **单一客户端**: `Client` / `AsyncClient` 是唯一执行入口
- [ ] **无 legacy facade**: `facade/api.py` / `facade/market.py` / `facade/bridge.py` 等全部删除
- [ ] **无 legacy server**: `integration/http_server.py` / `ws_server.py` 全部删除
- [ ] **无 legacy routing**: `DataSourceRouter` / `facade/routing.py` 全部删除
- [ ] **Provider 术语统一**: 内部只有 `Provider` 实体，无 `Source` 第二层
- [ ] **TDX 为主 Provider**: TDX 失败不自动跨 Provider 替代
- [ ] **Typed Query 完整**: 全部 capability 有对应的 Typed Query 类
- [ ] **Domain Model**: 业务结果用 Domain Record 而非 `list[dict]`
- [ ] **语义缓存**: L1 LRU + L2 persistent 正常工作
- [ ] **错误信封**: 全部错误通过 `ErrorEnvelope` 规范化
- [ ] **契约测试**: 每个 capability 有 Contract Test 锁定行为边界
- [ ] **传输硬化**: 连接/池/主机/限流契约全部有测试覆盖
- [ ] **CI 绿灯**: 全部测试 + lint + wheel smoke 通过

---

## 七、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| PR#6 rebase 后测试失败 | 中 | 先在本地运行全部 `tests/runtime/` 测试，失败则逐个修复 |
| PR#1 拆分后子分支仍有冲突 | 中 | 优先合并非冲突模块（文档/传输硬化/测试），核心重构最后评估 |
| PR#6 删除 legacy facade 后依赖断裂 | 高 | PR#6 已建立 `facade/runtime_adapter.py` 作为兼容层，验证所有导入路径 |
| PR#7 + PR#6 合并后 v14 运行时与 v12 服务层不兼容 | 中 | PR#1 的 service/health/freshness 模块设计为独立层，可作为 Provider 策略注入运行时 |
| 强制推送 PR#6 分支覆盖他人工作 | 低 | 该仓库为单人维护，风险极低 |

---

## 八、时间线

```
2026-09-13  Phase 1: 合并 PR#7 (零冲突, ~30 分钟)
2026-09-13  Phase 2: 解决 PR#6 冲突 + 合并 (~2 小时)
2026-09-14  Phase 3a: 拆分 PR#1 非冲突模块 (文档/传输硬化/测试)
2026-09-14  Phase 4: 删除 12 个过时分支
2026-09-15  Phase 3b: 评估 PR#1 核心重构模块合并可行性
2026-09-16  中期: Typed Capability 扩展 Phase 2
2026-09-20  中期: Domain Model 替换
2026-09-23  中期: Provider Adapter 完整实现
2026-10-07  长期: v14 完成验收
```

---

> 本方案基于 2026-09-13 的仓库状态分析生成。合并执行后需根据实际测试结果调整后续计划。
