# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""同步 / 异步客户端共享方法骨架——组合面与 re-export（P2-2 拆分后）。

原 1091 行单文件按方案 §4 P2-2 拆为三个私有模块，本文件只保留**组合类与
re-export**，既有 import（``atst.client.sync`` / ``async_`` / 测试）路径与
语义全部不变，行为零变化：

* :mod:`atst.client._trampoline` —— 驱动器 / trampoline 机制：op 定义
  （``_ReqOp`` / ``_CallOp``）、同步 / 异步双驱动器、告警转发口
  （``_forward_decode_caveats``）与栈深口径（逐帧列举的显式推导）；
  驱动器深度 ``_drive_depth`` 为线程局部（B4：多线程 ``quotes_concurrent``
  告警归属不再串帧）；
* :mod:`atst.client._templates_standard` —— 标准族协议模板
  （``_t_bars`` / ``_t_quotes`` / ``_t_file_download`` …），含 ``_t_bars``
  分页状态机的显式状态对象与纯判定函数；
* :mod:`atst.client._templates_family` —— Goods / Ex / Mac / F10 扩展族模板。

异步差异的消除方式——**模板 + trampoline**（机制总述随实现搬入
:mod:`atst.client._trampoline` 顶层 docstring）：公开方法壳留在 sync.py /
async_.py（签名、overload、docstring 摘要与迁移前一致）；``dispatch`` 经
:mod:`atst.client` 包级符号在**调用时**解析（monkeypatch 语义不变）。

**fail-closed 参数校验是模板的硬约束**：协议参数（市场标识、页偏移、日期、
文件名、批量容器）必须在**任何 pool I/O 之前**校验完毕并抛
:class:`~atst.errors.ParseError`；同步 / 异步两端不得各维护一套，校验只能
落在共享模板或 :mod:`atst.client.core` 的 SSOT 纯函数里。
"""

from __future__ import annotations

from ._templates_family import _FamilyExtensionTemplates  # noqa: F401
from ._templates_standard import (  # noqa: F401 —— re-export 保持既有 import 不变
    _QUOTES_SNAPSHOT_BATCH,
    MAX_BARS_PER_REQUEST,
    _StandardFamilyTemplates,
)
from ._trampoline import (  # noqa: F401 —— re-export 保持既有 import 不变
    _DOMAIN_CAVEAT_SAMPLES,
    _FRAMES_PER_NESTED_HOP,
    _MAX_TRAMPOLINE_STEPS,
    _SINGLE_HOP_STACKDEPTH,
    _TPL_WARN_STACKLEVEL,
    OutputFormat,
    _CallOp,
    _forward_decode_caveats,
    _op_call,
    _op_req,
    _ReqOp,
    _TrampolineMixin,
    _TrampolineOp,
)


class _ClientMixin(_StandardFamilyTemplates, _FamilyExtensionTemplates):
    """同步 / 异步客户端共享骨架（P2-2 组合类：标准族模板 + 扩展族模板，驱动器经 _TrampolineMixin 传递）。

    方法名与拆分前逐一相同：sync.py / async_.py 的公开方法壳经 ``self._t_*``
    与 ``self._drive`` / ``self._adrive`` 解析到同一份实现；``_set_last_errors``
    钩子（async 侧覆写为无锁直赋）与 monkeypatch 语义不变。
    """
