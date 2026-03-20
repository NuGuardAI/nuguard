"""Base class for xelo plugin adapters.

Third-party packages can register a plugin by:
1. Installing a package that subclasses ``PluginAdapter``.
2. Declaring an entry-point under the ``xelo.plugins`` group, or simply
   importing the subclass before constructing ``AiSbomExtractor``.

Plugins are opt-in.  Pass ``load_plugins=True`` to ``AiSbomExtractor`` or
call ``xelo.plugins.load_plugins()`` to discover and instantiate all
registered subclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter


class PluginAdapter(FrameworkAdapter, ABC):
    """Abstract base class for xelo plugin adapters.

    Subclass this to add custom framework detection.  The extractor will call
    :meth:`can_handle` for every file and, when it returns ``True``, call
    :meth:`extract` with the file content and AST parse result.

    You must implement :meth:`extract`.  Optionally override
    :meth:`can_handle` (the base implementation checks ``handles_imports``).
    """

    #: Human-readable name shown in logs and error messages.
    name: str = "unnamed-plugin"

    #: Lower integer = higher precedence during deduplication.  Built-in
    #: adapters use 0-30; use ≥ 50 for plugins to avoid overriding core
    #: detections unintentionally.
    priority: int = 50

    #: Module-name prefixes that activate this plugin (same semantics as
    #: :attr:`~xelo.adapters.base.FrameworkAdapter.handles_imports`).
    handles_imports: list[str] = []

    @abstractmethod
    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,  # xelo.ast_parser.ParseResult
    ) -> list[ComponentDetection]:
        """Extract component detections from *file_path*.

        ``parse_result`` is a ``ParseResult`` from ``xelo.ast_parser.parse()``.
        Return an empty list when nothing of interest is found.
        """
        ...
