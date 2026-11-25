"""``docker-compose.yml`` 的静态可执行性守卫。

这里只锁已经实测过的两类具体故障：宿主端口撞车（两服务都写 8000:8000，`up` 起不来），
以及 ``ATST_*`` 拼写错误——该前缀是保留命名空间，未登记的键会让 ``Client()`` 在
``config_from_env()`` 处直接 ConfigError，容器实际上一个请求也处理不了。
"""

from __future__ import annotations

import re
from pathlib import Path

from atst.config.loader import config_from_env
from atst.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"

_SERVICE = re.compile(r"^  ([a-zA-Z][\w-]*):[ \t]*$", re.MULTILINE)
_PORT_MAP = re.compile(r'"(\d+):(\d+)"')
_ENV_ASSIGN = re.compile(r"^\s*-\s+(ATST_[A-Z0-9_]+)=(.*)$")
_COMMAND_PORT = re.compile(r'"--port",\s*"(\d+)"')


def _compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _services(text: str) -> dict[str, str]:
    matches = list(_SERVICE.finditer(text))
    assert matches, "没扫到 service，判据自身失效"
    return {
        match.group(1): text[match.end() : matches[i + 1].start() if i + 1 < len(matches) else None]
        for i, match in enumerate(matches)
    }


def test_compose_env_names_pass_strict_config_scan() -> None:
    offenders: list[str] = []
    for line in _compose().splitlines():
        match = _ENV_ASSIGN.match(line)
        if not match:
            continue
        name, value = match.groups()
        try:
            config_from_env({name: value})
        except ConfigError as exc:
            offenders.append(f"{name}: {exc.message}")
    assert offenders == [], f"未登记/拼错的 ATST_* 变量会让容器 fail closed：{offenders}"


def test_compose_host_ports_unique_and_match_container_command() -> None:
    services = _services(_compose())
    host_ports: dict[str, str] = {}
    offenders: list[str] = []
    for name, block in services.items():
        command_port = _COMMAND_PORT.search(block)
        for line in block.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for host_port, container_port in _PORT_MAP.findall(line):
                if host_port in host_ports:
                    offenders.append(
                        f"宿主端口 {host_port} 同时被 {host_ports[host_port]} 与 {name} 占用"
                    )
                host_ports[host_port] = name
                if command_port and command_port.group(1) != container_port:
                    offenders.append(
                        f"{name}: --port {command_port.group(1)} 与容器端口 {container_port} 不一致"
                    )
    assert host_ports, "没扫到端口映射，判据自身失效"
    assert offenders == [], f"docker-compose.yml 端口配置有误：{offenders}"
