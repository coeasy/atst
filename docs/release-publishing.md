# PyPI 自动发布配置

更新：2026-10-04

## 当前状态

`.github/workflows/wheels.yml` 已包含 PyPI Trusted Publishing：推送符合规则的 `v*` tag 后，先运行完整发布门禁、构建并验证 wheel/sdist，再创建 GitHub draft release。2026-10-04 已完成 GitHub 仓库侧配置：`pypi` Environment 已创建，只允许匹配 `v*` 的标签部署；仓库 Actions variable `PUBLIC_RELEASE=true` 已启用。环境未设置 required reviewer，以保留 tag 发布后的自动流程。

本次核查 PyPI JSON API：[`https://pypi.org/pypi/atst/json`](https://pypi.org/pypi/atst/json) 返回 404，当前没有 `atst` 项目版本可供安装。用户已确认 PyPI 账号侧 Trusted Publisher 设置完成；首次发布前应再核对其字段与下表完全一致。Pending publisher 允许首次 GitHub Actions 发布创建项目，但在成功发布前不会保留项目名；首次使用后会转为普通 publisher。

GitHub 仓库侧设置已通过登录后的仓库设置页面保存并回读确认。PyPI pending publisher 由用户确认已配置；本地无法读取 PyPI 账号设置，也不能在本地模拟 GitHub Actions OIDC 身份。下列信息作为后续复核清单。

## 1. PyPI Trusted Publisher（用户已配置）

用户已确认在 PyPI 账号设置中完成配置。发布前可在账号设置的 **Publishing** 页面复核以下字段：

| 字段 | 值 |
|---|---|
| PyPI project name | `atst` |
| Owner | `coeasy` |
| Repository name | `atst` |
| Workflow filename | `wheels.yml` |
| Environment name | `pypi` |

由于项目尚不存在，这应是账号级 pending publisher，而不是给已有项目添加 publisher。Pending publisher 在首次成功发布前**不会保留项目名**；首次使用后会转为普通 publisher。

参考：[PyPI：用 Trusted Publisher 创建项目](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)；[PyPI：添加 Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)。

## 2. GitHub Environment（已配置）

`coeasy/atst` 的 `Settings → Environments` 中已创建名为 `pypi` 的环境，与 workflow 和 PyPI publisher 的环境名一致。环境部署规则只允许 `v*` 标签。当前没有 required reviewer，避免需要人工批准而中断自动发布。该环境没有 PyPI API token。

如果希望每次发布都经过人工审核，可在此环境加 required reviewer；这会暂停自动发布，直到有人批准。若要调整部署规则，建议仍只允许版本标签。

当前部署规则仅允许 `v*` tag。若之后需要每次发布都经过人工审核，可增加 required reviewer；这会暂停无人值守发布，直到有人批准。工作流使用 OIDC，不需要 PyPI API token。

PyPI Trusted Publisher 匹配仓库、workflow 和可选 environment；字段必须与此次工作流身份完全一致。该项目的发布 job 已在 job 级授予 `id-token: write`。详见 [PyPI Trusted Publishing 安全建议](https://docs.pypi.org/trusted-publishers/security-model/)。

## 3. 仓库变量（已启用）

`Settings → Secrets and variables → Actions → Variables` 中已经保存**仓库变量**（不是 secret）：

```text
PUBLIC_RELEASE=true
```

该变量已经允许未来符合条件的 tag 发布自动上传 PyPI。普通 PR、main 分支 push、本地构建和 `workflow_dispatch` 都不会发布 PyPI。

DockerHub 是另外的可选发布面；只有计划发布 Docker 镜像时才配置 `PUBLISH_DOCKER=true` 和 Docker Hub credentials。

## 4. 安全与重试行为

工作流先在没有发布身份的 `pypi-preflight` job 中比对版本与本地产物 SHA-256；具有 OIDC 权限的 `publish-pypi` job 只下载单独制作的 wheel/sdist artifact 并调用 PyPA 发布 action，不 checkout 源码、不运行本地脚本。发布完成后，另一个没有 OIDC 权限的 job 会查询 PyPI 并验证最终文件摘要。遇到相同版本重试时，只有 wheel/sdist 文件集合与 SHA-256 完全一致才会按成功处理；有差异就 fail closed。

该 workflow 的确切顺序为：

```text
push vX.Y.Z
  → source 门禁和可复现构建
  → 跨系统 wheel 安装冒烟
  → 创建 GitHub draft release
  → PUBLIC_RELEASE=true 时：检查 PyPI → OIDC 上传 → PyPI 读回校验
  → 发布 GitHub Release
```

## 5. 正式发布验证

1. 按 README 的版本流程更新 `atst/_version.py`、`CHANGELOG.md` 和 `docs/releases/vX.Y.Z.md`。
2. 确认改动已经合入 `main`；发布 workflow 会拒绝不在 main 历史上的 tag。
3. 推送 `vX.Y.Z` tag，并在 GitHub Actions 检查 `pypi-preflight`、`publish-pypi`、`verify-pypi` 和 `publish-release`。若启用了 required reviewer，先批准 `pypi` environment。
4. 在 PyPI 项目页确认版本、wheel、sdist 和 provenance/attestation，再用干净环境验证：

   ```bash
   python -m pip index versions atst
   python -m pip install "atst==X.Y.Z"
   ```

`workflow_dispatch` 仅用于手动运行构建检查；GitHub Release 的创建/发布逻辑要求 tag push，因此它不替代正式 tag 发布。
