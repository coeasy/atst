# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""全局测试夹具。

P#1 归档接线后的测试隔离：``ProtocolSniffer`` 已在 dispatch 的 L2/L3 路径
自动归档样本（默认 root 指向仓库内 ``PROTOCOL_SPEC/_sniffer/``）——测试大量
触发 L3 回落，若不隔离会向仓库写入垃圾样本。本夹具把默认 sniffer 禁用；
需要验证归档行为的测试自行构造独立 ``ProtocolSniffer(root=tmp_path)``。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_default_sniffer():
    """禁用全局 sniffer，防止 dispatch 测试向仓库目录写归档样本。"""
    from tstdx.protocol.generic import get_sniffer

    sniffer = get_sniffer()
    original = sniffer.enabled
    sniffer.enabled = False
    yield
    sniffer.enabled = original
