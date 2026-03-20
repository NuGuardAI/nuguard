"""xelo plugin discovery and loading.

Usage::

    from xelo.plugins import load_plugins

    plugins = load_plugins()

Or pass ``load_plugins=True`` when constructing ``AiSbomExtractor``::

    from xelo import AiSbomExtractor, AiSbomConfig
    extractor = AiSbomExtractor(AiSbomConfig(), load_plugins=True)
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
import pkgutil

from xelo.adapters.base import FrameworkAdapter
from xelo.plugins.base import PluginAdapter

__all__ = ["PluginAdapter", "load_plugins"]

logger = logging.getLogger(__name__)

# __path__ is a list[str] set by Python's import system for packages.
_pkg_path: list[str] = __path__


def load_plugins() -> list[FrameworkAdapter]:
    """Discover and return all installed plugin adapters.

    Discovery happens in two ways (both are tried):

    1. **Entry-points** — any installed package that declares an entry-point
       under the ``xelo.plugins`` group.  Each entry-point value must be a
       :class:`PluginAdapter` subclass.

    2. **Sub-modules** — any module directly inside this ``xelo.plugins``
       package (excluding ``__init__`` and ``base``).  Useful for shipping
       built-in optional adapters alongside xelo itself.

    Returns a list of instantiated :class:`PluginAdapter` objects sorted by
    :attr:`~xelo.adapters.base.FrameworkAdapter.priority`.
    """
    # 1. Load via entry-points (third-party packages)
    try:
        eps = importlib.metadata.entry_points(group="xelo.plugins")
        for ep in eps:
            try:
                ep.load()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load xelo plugin entry-point %r: %s", ep.name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Entry-point discovery failed: %s", exc)

    # 2. Load sub-modules within this package
    for module_info in pkgutil.iter_modules(_pkg_path):
        if module_info.name.startswith("_") or module_info.name == "base":
            continue
        full_name = f"{__name__}.{module_info.name}"
        try:
            importlib.import_module(full_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to import xelo plugin module %r: %s", full_name, exc)

    # Collect all concrete subclasses (including transitively loaded ones)
    def _all_subclasses(cls: type) -> list[type]:
        result: list[type] = []
        for sub in cls.__subclasses__():
            result.append(sub)
            result.extend(_all_subclasses(sub))
        return result

    instances: list[FrameworkAdapter] = []
    for cls in _all_subclasses(PluginAdapter):
        try:
            if getattr(cls, "__abstractmethods__", frozenset()):
                continue
            instance = cls()
            if isinstance(instance, FrameworkAdapter):
                instances.append(instance)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to instantiate plugin %r: %s", cls, exc)

    instances.sort(key=lambda p: p.priority)
    return instances
