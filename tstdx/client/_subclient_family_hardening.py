# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Make fixed-family client constructors reject contradictory ``family=`` input.

Goods/extended/MAC/F10 client classes intentionally pin their protocol family.
Their historical ``**kwargs`` constructors overwrote a caller-supplied family
silently, which made invalid configuration look accepted. Matching explicit
family values remain valid; conflicting values fail closed before pool binding.
"""

from __future__ import annotations

import functools
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import sync as _sync_impl


def _wrap_fixed_family_init(cls: type[Any], *, family: str) -> None:
    original = cls.__init__

    @functools.wraps(
        original,
        assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
    )
    def guarded(self: Any, *args: Any, **kwargs: Any) -> None:
        requested = kwargs.get("family", family)
        if requested != family:
            raise ConfigError(
                "固定协议族客户端不接受冲突 family: "
                f"client={cls.__name__}, required={family!r}, requested={requested!r}",
                context={
                    "client": cls.__name__,
                    "required_family": family,
                    "requested_family": requested,
                    "provider_switch_allowed": False,
                },
            )
        kwargs["family"] = family
        original(self, *args, **kwargs)

    cls.__init__ = guarded


for _cls, _family in (
    (_sync_impl.GoodsClient, Family.GOODS),
    (_sync_impl.ExMarketClient, Family.EXTENDED),
    (_sync_impl.MacClient, Family.MAC),
    (_sync_impl.F10Client, Family.F10),
    (_async_impl.AsyncGoodsClient, Family.GOODS),
    (_async_impl.AsyncExMarketClient, Family.EXTENDED),
    (_async_impl.AsyncMacClient, Family.MAC),
    (_async_impl.AsyncF10Client, Family.F10),
):
    _wrap_fixed_family_init(_cls, family=_family)

del _cls, _family
