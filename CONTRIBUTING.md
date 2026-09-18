# 贡献指南

感谢你对 tstdx 项目的关注。本指南只描述当前仓库实际执行的开发、测试与发布门禁；本地流程应能复现 GitHub CI，而不是维护一套更宽松的替代流程。

## 行为准则

请阅读并遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 贡献方式

### 1. 报告 Bug

- 使用 GitHub Issue 的 Bug Report 模板。
- 提供最小复现步骤、环境信息和完整错误日志。
- 对 Provider/缓存/实时数据问题同时提供 provider、channel、freshness/provenance 信息；不要把其它 Provider 的结果当作兜底证明。

### 2. 功能请求

- 使用 GitHub Issue 的 Feature Request 模板。
- 描述使用场景、明确的数据 Provider/Channel 语义和替代方案。
- 新能力不得通过跨 Provider fallback、stale cache、replay 或 synthetic 数据冒充实时结果。

### 3. 提交代码

#### 开发环境设置

```bash
git clone https://github.com/coeasy/tstdx.git
cd tstdx

python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

# 安装与 CI 一致的 all + dev 依赖、构建工具和 pre-commit。
make install
pre-commit install

python -c "import tstdx; print(tstdx.__version__)"
```

`make install` 是开发依赖的推荐入口。不要单独手工拼装 pytest/ruff/mypy 版本后把结果当作正式门禁结论。

在 Windows / 只有 `uv` 的环境里，等价的本地入口是：

```bash
uv sync --all-extras --dev                 # 生成 .venv，与 CI 依赖一致
.venv\Scripts\python.exe -X utf8 -m pytest # Windows 下用 venv 解释器，别用裸 python
.venv\Scripts\python.exe -X utf8 scripts/contract_audit.py --ci
uvx ruff@latest check tstdx tests scripts   # 与 CI 同版本更佳
```

`-X utf8` 在 Windows 上是必需的：文档、golden 样本与协议注释含中文，默认 GBK 代码页会让
读写在 CI 与本机之间产生假差异。

#### 开发流程

1. **创建分支**

   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **修改实现并同步门禁**

   - 遵循现有 Ruff 配置和类型约束。
   - 公共 API、状态机、错误边界、构建/发布行为发生变化时必须同时添加对应回归测试。
   - Provider 选择保持 fail-closed；TDX host failover 只能发生在 TDX Provider 内部。
   - `KeyboardInterrupt` / `SystemExit` 等进程控制信号不得包装成 Provider 失败。
   - **文档即门禁对象**：改动作数（CLI 子命令数、HTTP 路由数、MCP 工具数、capability 数、
     Provider 数）或公开符号时，同步改 `README.md` / `docs/`；
     `tests/architecture/test_doc_code_consistency.py` 会解析活文档里的 import 与反引号路径，
     并逐项核对 README 宣称的数字。
   - **契约先行必须自带实现**：新增 capability / 绑定 / 公开符号的 PR，需在**同一提交**内
     带上生产消费者与守卫；不得"先声明后补线"，否则该符号会以孤儿形态进入公开面
     （v17 F-9 的 `freshness/health/failure` 三件套即为此教训）。
   - **不做跨 PR 的"先删后补"**：删除一个层与其消费者的迁移必须在同一 PR 内闭环，
     不留中间态（clean break 不引入别名）。

3. **先跑快速提交前检查**

   ```bash
   make pre-commit
   ```

   该入口覆盖 Ruff check/format、mypy、originality、strict spec audit、docs link integrity 和基础文件卫生检查。

4. **提交 PR 前跑完整确定性门禁**

   ```bash
   make gates
   ```

   `make gates` 包含当前 PR 阻塞门禁的本地可复现集合：

   - Ruff check + format
   - mypy（含 `--warn-unused-ignores`）
   - 非联网测试 + **coverage >= 77**
   - Bridge Audit
   - Golden 三旗标门禁
   - strict Spec Coverage
   - Adversarial matrix
   - Reachability
   - Originality
   - synthetic benchmark smoke
   - docs relative-link integrity

   77% 是当前**最低阻塞阈值**，不是长期目标；新增代码应尽量保持或提升覆盖率，项目目标继续向 80%+ 收敛。不得为了通过 CI 下调阈值、删测试或增加跳过。

   **退出码必须裸取**。`some-gate | tail` 之后 `$?` 是管道末端（`tail`）的退出码，不是门禁的：
   曾被读成"已绿"的 originality / spec-coverage / reachability 就是踩在这个上面。复测时要么
   先重定向再取 RC，要么显式取管道首段：

   ```bash
   python -m tstdx.tools.spec_audit --json --strict > /tmp/spec.log 2>&1; echo "RC=$?"
   make audit-reachability 2>&1 | tail -20; echo "RC=${PIPESTATUS[0]}"
   ```

   同理：`grep -c` 在零匹配时返回 1，不要把"没有匹配"当成命令失败。

5. **需要联网验证时单独运行**

   ```bash
   make test-live
   make host-audit
   ```

   这些是公网/主站可用性探测，不属于确定性的 PR merge gate。网络波动不能被解释成源码绿色，也不能通过 `continue-on-error` 伪装成功。

6. **提交 PR**

   - 使用 PR 模板并关联相关 Issue。
   - PR 在同一个 head SHA 上获得所有真实阻塞门禁绿色前，不应合并。
   - `steps=null`、runner 未分配或没有真实日志的 Actions 失败不是源码门禁执行结果；同样不能通过跳过/禁用 gate 处理。

#### 代码风格

- **Lint / format**：Ruff（配置在 `pyproject.toml`）。
- **类型**：mypy；包声明为 PEP 561 typed package，`tstdx/py.typed` 必须随 wheel 发布。
- **测试**：pytest；网络测试使用 `network` marker 并与离线主矩阵分离。

#### 测试要求

- 新功能必须有对应行为测试。
- 协议解析变更必须维护 Golden/spec 门禁。
- 并发、缓存、Streaming、Batch/SingleFlight 等修改必须覆盖失败/取消/超时/终态边界。
- 当前 CI 行覆盖硬门禁为 77%；不得回退，新增代码目标为 80%+。

### 4. 构建与发布

本地只构建和验证，不直接发布：

```bash
make build
```

该命令使用 PEP 517 隔离构建，要求恰好一个 `py3-none-any` wheel + 一个 sdist，并在干净 venv 中验证版本、CLI、`py.typed` 和 `pip check`。

```bash
make publish
```

会**故意失败**。PyPI 发布只能通过 `.github/workflows/wheels.yml`：Release tag 必须与源码/`pyproject.toml` 版本一致，同一个 canonical wheel 必须先通过 Linux/macOS/Windows × Python 3.10–3.13 安装矩阵，再使用 OIDC Trusted Publishing 一次性发布。Docker Release 镜像也复用同一 wheel，不允许重新构建第二份包。

### 5. 提交文档

- 使用 GitHub Issue 的 Documentation 模板。
- `docs/` 内相对链接必须留在仓库边界内并指向真实文件。
- 提交前运行 `make audit-docs` 或 `make pre-commit`。

## 协议规范

如果要添加新的协议命令：

1. 在 `PROTOCOL_SPEC/` 下创建或更新 YAML spec。
2. 实现解析器（`tstdx/protocol/parsers/`）。
3. 注册到命令账本（`tstdx/protocol/commands.py`）。
4. 添加/更新 Golden 数据样本。
5. 运行 strict spec、Golden、adversarial 和相关单元测试。

详见 [PROTOCOL_SPEC/README.md](PROTOCOL_SPEC/README.md)。

## 安全

- **不要**在公开 Issue 中披露未修复的安全漏洞。
- 使用 GitHub Security Advisory 的私密报告渠道。
- 详见 [SECURITY.md](SECURITY.md)。

## 许可证

提交 PR 即表示你同意以 MIT License 贡献代码。
