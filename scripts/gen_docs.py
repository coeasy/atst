# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""投影型文档生成器（v2 / W10 §5）。

**真值只在运行时**：Provider 注册表、Web 源登记表、命令账本、四张服务面
（CLI / HTTP / WS / MCP）都从代码现读现生成。生成物提交进仓库，守卫只做一条
判据——"生成物与工作区一致"。改代码不跑 `make docs-gen` 会让守卫红，而不是
让人肉文档悄悄漂移。

生成目录：``docs/generated/``（脚本是唯一写者）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "generated"

HEADER = "<!-- 本文件由 scripts/gen_docs.py 生成，请勿手改；改代码后运行 `make docs-gen`。 -->\n\n"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def render_web_sources() -> str:
    from atst.sources.sources import KNOWN_SOURCES

    lines = [
        HEADER.strip(),
        "",
        "# Web 源登记表",
        "",
        "| 源 | 摘要 | Referer | Cookie | 默认限流 (req/s) | 能力 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for spec in sorted(KNOWN_SOURCES.values(), key=lambda item: item.name):
        caps = "、".join(f"`{cap}`" for cap in spec.capabilities)
        lines.append(
            f"| `{_cell(spec.name)}` | {_cell(spec.summary)} | "
            f"{'是' if spec.needs_referer else '否'} | {'是' if spec.needs_cookie else '否'} | "
            f"{spec.default_rate} | {caps} | {_cell(spec.notes) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def render_capability_matrix() -> str:
    from atst.providers import (
        MIGRATED_BINDINGS,
        MIGRATED_CAPABILITIES,
        PROVIDERS,
        capability_statuses,
    )

    statuses = capability_statuses()
    declared: dict[str, list[str]] = {}
    available: dict[str, list[str]] = {}
    for capability in sorted(MIGRATED_CAPABILITIES):
        owners = [
            provider_id
            for provider_id in PROVIDERS.ids()
            if PROVIDERS.get(provider_id).supports(capability)
        ]
        declared[capability] = owners
        available[capability] = list(PROVIDERS.available_providers(capability))

    bindings = len(MIGRATED_BINDINGS)
    lines = [
        HEADER.strip(),
        "",
        "# capability × provider 矩阵",
        "",
        f"- Provider：{len(PROVIDERS.ids())} 个",
        f"- 迁移 capability：{len(MIGRATED_CAPABILITIES)} 个",
        f"- 迁移绑定：{bindings} 条",
        "",
        "| capability | 状态 | 声明 Provider | 可执行 Provider |",
        "|---|---|---|---|",
    ]
    for capability in sorted(MIGRATED_CAPABILITIES):
        owners = "、".join(f"`{item}`" for item in declared[capability]) or "—"
        live = "、".join(f"`{item}`" for item in available[capability]) or "—"
        lines.append(f"| `{_cell(capability)}` | `{statuses[capability]}` | {owners} | {live} |")

    lines += [
        "",
        "## Provider × Channel",
        "",
        "| Provider | Channel | live | local | 周期 | 下线能力 |",
        "|---|---|---|---|---|---|",
    ]
    for provider_id in PROVIDERS.ids():
        spec = PROVIDERS.get(provider_id)
        for channel in spec.channels:
            # periods / unavailable_capabilities 是 frozenset：迭代顺序随 PYTHONHASHSEED 变，
            # 生成物必须跨进程稳定，否则 docs-check 会在不同进程里假红。
            periods = "、".join(sorted(channel.periods)) or "—"
            unavailable = "、".join(sorted(channel.unavailable_capabilities)) or "—"
            lines.append(
                f"| `{_cell(provider_id)}` | `{_cell(channel.id)}` | "
                f"{'是' if channel.live else '否'} | {'是' if channel.local else '否'} | "
                f"{_cell(periods)} | {_cell(unavailable)} |"
            )
    return "\n".join(lines) + "\n"


def render_faces() -> str:
    from atst.cli.parser import build_parser
    from atst.server.mcp._tools_spec import TOOLS
    from atst.server.runtime_http import create_runtime_app
    from atst.server.runtime_ws import RuntimeJsonRpcHandler

    parser = build_parser()
    commands: list[str] = []

    def walk(current, prefix: tuple[str, ...]) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, sub in action.choices.items():
                path = prefix + (name,)
                child = [
                    item for item in sub._actions if isinstance(item, argparse._SubParsersAction)
                ]
                if child:
                    walk(sub, path)
                else:
                    commands.append(" ".join(path))

    walk(parser, ())
    app = create_runtime_app()
    routes = sorted(
        f"{','.join(sorted(route.methods or {'GET'}))} {route.path}"
        for route in app.routes
        if getattr(route, "path", "").startswith("/v13")
    )

    lines = [
        HEADER.strip(),
        "",
        "# 四张服务面",
        "",
        f"- CLI 叶子命令：{len(commands)} 条",
        f"- HTTP 路由：{len(routes)} 条",
        f"- WS / JSON-RPC 方法：{len(RuntimeJsonRpcHandler.METHODS)} 个",
        f"- MCP 工具：{len(TOOLS)} 个",
        "",
        "## WS / JSON-RPC 方法",
        "",
    ]
    lines += [f"- `{item}`" for item in sorted(RuntimeJsonRpcHandler.METHODS)]
    lines += ["", "## HTTP 路由", ""]
    lines += [f"- `{item}`" for item in routes]
    lines += ["", "## MCP 工具", ""]
    lines += [f"- `{tool.name}`" for tool in TOOLS]
    return "\n".join(lines) + "\n"


def render_tdx_commands() -> str:
    from atst.protocol.commands import COMMANDS

    verified = sum(1 for command in COMMANDS.values() if command.verified)
    lines = [
        HEADER.strip(),
        "",
        "# 通达信命令账本（投影）",
        "",
        f"- 命令总数：{len(COMMANDS)}",
        f"- `verified=True`：{verified}",
        f"- `verified=False`：{len(COMMANDS) - verified}",
        "",
        "| 命令 | 名称 | 层级 | verified | status |",
        "|---|---|---|---|---|",
    ]
    for key, command in sorted(COMMANDS.items()):
        lines.append(
            f"| `{_cell(key)}` | `{_cell(command.name)}` | {_cell(command.tier)} | "
            f"{command.verified} | {_cell(getattr(command, 'status', '') or '—')} |"
        )
    return "\n".join(lines) + "\n"


RENDERERS = {
    "web_sources.md": render_web_sources,
    "capability_matrix.md": render_capability_matrix,
    "faces.md": render_faces,
    "tdx_commands.md": render_tdx_commands,
}


def render_all() -> dict[Path, str]:
    return {OUT / name: renderer() for name, renderer in RENDERERS.items()}


def write_all() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path, content in render_all().items():
        path.write_text(content, encoding="utf-8", newline="")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成投影型文档")
    ap.add_argument("--check", action="store_true", help="只比对，不写盘；不一致则退出 1")
    args = ap.parse_args(argv)
    stale: list[str] = []
    for path, content in render_all().items():
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("以下生成物与工作区不一致，请运行 `make docs-gen`：", file=sys.stderr)
        for item in stale:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
