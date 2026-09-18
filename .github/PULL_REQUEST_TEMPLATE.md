## PR 描述

<!-- 简要说明问题、根因、方案，以及为什么不能用更宽松的替代方案。 -->

## 变更类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 破坏性变更
- [ ] 文档更新
- [ ] 测试/门禁补充
- [ ] 重构
- [ ] 性能优化
- [ ] 构建/发布/CI
- [ ] 其他：______

## 关联 Issue

Fixes #____

## Provider / 数据语义影响

- [ ] 不涉及 Provider/Channel/freshness/cache 语义
- [ ] 涉及；下方已明确说明 Provider、Channel、freshness/provenance 变化

<!--
硬约束：
- TDX 是默认主 Provider；其它 Provider 是显式独立通道。
- 禁止跨 Provider silent fallback。
- TDX host failover 只能发生在 TDX Provider 内。
- stale cache / replay / synthetic 不得冒充当前实时数据。
-->

## 实现与行为门禁

<!-- 列出实现改动，以及每一项对应的测试/契约门禁。不要只写“已测试”。 -->

| 实现/行为变化 | 对应测试或 gate |
|---|---|
| | |

## 本地验证

- [ ] `make pre-commit`
- [ ] `make gates`
- [ ] 如涉及打包：`make build`
- [ ] 如涉及联网能力：已单独运行所需的 `make test-live` / `make host-audit`，且没有把公网结果当作确定性源码 gate

### 关键输出

```text
# 粘贴失败/成功门禁的关键输出；不要只贴 workflow 总状态。
```

## CI / 合并证据

- [ ] Ruff check + format 真实执行并绿色
- [ ] mypy 真实执行并绿色
- [ ] Linux/Windows Python 矩阵真实执行并绿色
- [ ] Bridge / Golden / Spec / Adversarial / Reachability / Originality / Benchmark / Docs 真实执行并绿色
- [ ] 所有阻塞门禁来自**同一个 head SHA**
- [ ] 没有通过删除/跳过测试、降低 coverage（当前硬门禁 77）、移除 strict 参数或 `continue-on-error` 换取绿色

> `steps=null`、runner 未分配、没有 checkout/命令日志的 Actions failure 不算源码 gate 已执行；同样不能把它改成 skipped/soft-fail 来绕过。

## 构建 / 发布影响（如适用）

- [ ] wheel 仍为 canonical `py3-none-any` 并包含 `tstdx/py.typed`
- [ ] Release tag / `pyproject.toml` / `tstdx.__version__` 身份一致
- [ ] PyPI 仍只通过 GitHub Release + OIDC Trusted Publishing 发布
- [ ] Docker Release 镜像复用已通过矩阵验证的同一个 canonical wheel，不二次构建

## 自审

- [ ] 变更没有引入新的 linter/type warning
- [ ] 新行为有失败/边界回归测试
- [ ] 文档和开发入口已与实现同步
- [ ] `KeyboardInterrupt` / `SystemExit` 等进程控制信号没有被错误归一化为业务错误
- [ ] PR 在全部阻塞门禁真实绿色前不会被合并

## 备注

<!-- 记录已知外部阻塞、runner 基础设施问题或仍需维护者关注的事项。 -->
