# 贡献指南

感谢你对 tstdx 项目的关注！本指南将帮助你快速开始贡献。

## 行为准则

请阅读并遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 贡献方式

### 1. 报告 Bug

- 使用 GitHub Issue 的 Bug Report 模板
- 提供复现步骤和日志输出
- 标记严重程度

### 2. 功能请求

- 使用 GitHub Issue 的 Feature Request 模板
- 描述使用场景和替代方案
- 考虑实现复杂度

### 3. 提交代码

#### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/coeasy/tstdx.git
cd tstdx

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows

# 安装开发依赖
make install

# 验证安装
python -c "import tstdx; print(tstdx.__version__)"
```

#### 开发流程

1. **创建分支**
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **编写代码**
   - 遵循现有代码风格（PEP 8 + ruff 配置）
   - 添加类型注解
   - 编写 docstring
   - 编写测试

3. **运行检查**
   ```bash
   # Lint
   make lint
   
   # 类型检查
   make type-check
   
   # 测试
   make test
   
   # 24 项贯通审计
   make audit-bridges
   ```

4. **提交 PR**
   - 使用 PR 模板
   - 关联相关 Issue
   - 确保 CI 通过

#### 代码风格

- **Lint**: 使用 ruff（配置在 `pyproject.toml`）
- **格式**: 使用 ruff format
- **类型**: 使用 mypy
- **测试**: 使用 pytest

#### 测试要求

- 新功能必须有单元测试
- 协议解析必须有 Golden 数据测试
- 集成测试使用 `@pytest.mark.integration` 标记
- 覆盖率目标：≥80% 行覆盖

### 4. 提交文档

- 使用 GitHub Issue 的 Documentation 模板
- 提供具体的修改建议

## 发布检查清单

发布新版本时，必须保持以下信息一致：

1. 更新 `pyproject.toml` 的 `project.version` 与 `tstdx.__version__`；
2. 在 `CHANGELOG.md` 写入带日期的版本章节，并更新对应发布说明；
3. 更新 README 当前版本、兼容性和文档导航；
4. 运行 `python scripts/build_package.py --smoke`，确认 wheel/sdist 与安装冒烟均通过；
5. 运行全量测试、ruff 检查和文档链接检查；
6. 创建 `vX.Y.Z` 标签并发布 GitHub Release；工作流会自动上传 PyPI，并将 wheel/sdist
   附加到 Release 资产栏。

历史版本引用（例如弃用时间线和归档路线图）应保留原版本号，并明确其历史语义，
不得为追求字符串一致而批量改写。

## 协议规范

如果你要添加新的协议命令，请：

1. 在 `PROTOCOL_SPEC/` 下创建 YAML spec 文件
2. 实现解析器（`tstdx/protocol/parsers/`）
3. 注册到命令账本（`tstdx/protocol/commands.py`）
4. 添加 Golden 数据样本（`tests/golden/`）
5. 更新 spec 覆盖率测试

详见 `PROTOCOL_SPEC/README.md`。

## 安全

- **不要**在公开 Issue 中报告安全漏洞
- 使用 GitHub Security Advisory 或邮件联系维护者
- 详见 [SECURITY.md](SECURITY.md)

## 许可证

提交 PR 即表示你同意以 MIT 许可贡献你的代码。

## 沟通

- **讨论**: GitHub Discussions
- **即时通讯**: （待定）
- **邮件**: 项目维护团队

## 感谢

感谢每一位贡献者！你的贡献让 tstdx 变得更好。
