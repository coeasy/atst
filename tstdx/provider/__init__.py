"""V14 dynamic provider execution adapters.

Static provider/channel/capability truth lives in :mod:`tstdx.providers`.
Caching is not a Provider identity; semantic cache integration lives above this
execution-adapter layer.
"""

from .base import Provider
from .local import LocalProvider
from .router import ProviderRouter
from .tdx import TdxProvider
from .web import WebProvider

__all__ = [
    "Provider",
    "ProviderRouter",
    "TdxProvider",
    "WebProvider",
    "LocalProvider",
]
