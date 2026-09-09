# tstdx 项目治理

> 本文档定义了 tstdx 项目的治理结构、决策流程和贡献规范。
> 所有维护者和贡献者都应阅读并遵循本治理文件。

## 1. 项目使命

tstdx 是一个**通达信（TDX）行情数据通用协议库**，目标是：

- 提供统一的 TDX 协议解析接口（7709 标准 / 7727 扩展 / MAC / F10 / 商品五套协议族）
- 支持同步/异步双 API、多协议族客户端、HTTP Web 源降级
- 零硬依赖、可选增强、跨平台（Linux / macOS / Windows）
- 面向 Python 量化研究与交易策略开发者

## 2. 治理结构

### 2.1 核心维护者（Core Maintainers）

核心维护者拥有：
- 合并 PR 的最终决策权
- 发布版本的批准权
- 治理文件的修改权（需 2/3 投票）
- Issue 标签和优先级设定权

当前核心维护者：
- （初始阶段：项目作者）

### 2.2 贡献者（Contributors）

贡献者通过以下方式参与：
- 提交 Issue（Bug / Feature Request / Question）
- 提交 Pull Request（代码 / 文档 / 测试）
- 参与讨论和代码审查

### 2.3 贡献者晋升

连续贡献 ≥3 个被合并的 PR 的参与者可申请成为核心维护者。

## 3. 决策流程

### 3.1 共识优先

- 日常决策：共识优先，任何核心维护者可提议
- 架构决策（ADR）：需 2/3 核心维护者批准
- 治理变更：需 2/3 核心维护者批准

### 3.2 决策记录（ADR）

重大决策应记录在 `docs/adr/` 目录中，格式：

```
ADR-XXX: 决策标题
状态: proposed / accepted / superseded
日期: YYYY-MM-DD
```

### 3.3 紧急修复

安全漏洞或严重 Bug 可由单个核心维护者直接合并，但需在 24 小时内通知其他维护者。

## 4. 版本管理

### 4.1 SemVer 规范

- MAJOR：不兼容的 API 变更
- MINOR：向后兼容的功能新增
- PATCH：向后兼容的 Bug 修复

### 4.2 弃用策略

- 弃用标记：使用 `@deprecated` 装饰器或 `DeprecationWarning`
- 保留周期：至少 2 个 minor 版本
- 迁移指南：每个弃用功能需在 `docs/migration/` 中提供迁移指南

### 4.3 发布流程

1. 确认 `pyproject.toml`、`tstdx.__version__`、README 和 CHANGELOG 版本一致；
2. 更新 `docs/releases/vX.Y.Z.md`，记录变更、兼容性和验证结果；
3. 运行 `python scripts/build_package.py --smoke` 和完整测试套件；
4. 创建发布提交并打 `vX.Y.Z` 标签；
5. 创建 GitHub Release，发布说明引用对应的 CHANGELOG/发布文档；
6. 由 `wheels.yml` 的 Trusted Publishing 工作流发布 wheel 和 sdist 到 PyPI；
7. 发布后检查 PyPI 安装、`import tstdx` 版本和 GitHub Release 资产。

v1.0.0 的具体发布记录见 [v1.0.0 发布说明](docs/releases/v1.0.0.md)。

## 5. 代码审查

### 5.1 PR 要求

- 必须有 ≥1 个核心维护者 review 通过
- 必须通过 CI 检查（lint / type / test / originality）
- 必须包含测试（新功能必须有测试覆盖）
- 必须更新文档（如适用）

### 5.2 代码风格

- 遵循 PEP 8
- 使用 ruff 进行 linting（配置在 pyproject.toml）
- 使用 mypy 进行类型检查
- 所有公开 API 必须有类型注解

### 5.3 测试要求

- 新功能必须有单元测试
- 协议解析必须有 Golden 数据测试
- 集成测试使用 `@pytest.mark.integration` 标记
- 覆盖率目标：≥80% 行覆盖

## 6. 安全政策

- 漏洞报告：不要公开发布，联系维护者
- 响应时间：严重漏洞 72 小时内响应
- 安全公告：通过 GitHub Security Advisory 发布
- 详见 SECURITY.md

## 7. 社区行为准则

- 所有参与者必须遵守 CODE_OF_CONDUCT.md
- 骚扰、歧视、恶意行为将受到警告、禁止等处分
- 严重违反行为将导致永久封禁

## 8. 许可

- 项目许可证：MIT License
- 贡献即同意：提交 PR 即同意以 MIT 许可贡献
- 第三方代码需通过 ORIGINALITY 扫描
- 详见 LICENSE 和 ORIGINALITY/LICENSE_ALLOWLIST.md

## 9. 沟通渠道

- Issue Tracker：GitHub Issues
- 讨论：GitHub Discussions
- 即时通讯：（待定）

---

**修订历史**：
- v1.0.0 (2026-09-09)：正式稳定版发布流程与治理信息对齐
