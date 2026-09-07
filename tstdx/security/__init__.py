# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""安全与合规（§I2）。

历史说明
--------
``CredentialStore``（三级凭据回退）已于 v10 按 :doc:`ADR-007-010 </docs/adr/ADR-007-010>`
删除——全库零调用方、属过度工程。若未来 trade/CLI 出现真实凭据需求，
按该 ADR 重新设计（env → file 两级起步），勿从 git 历史整体找回。

后续扩展
--------
- ``tstdx/security/capture.py``  —— 抓包法律自检（交易时段阻断）。
- ``tstdx/security/origin.py``   —— 数据来源声明嵌入（水印 / 元数据）。
"""

from __future__ import annotations

__all__: list[str] = []
