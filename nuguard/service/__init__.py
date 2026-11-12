"""NuGuard remote scan service: an HTTP job API for SBOM + static analysis of git repos.

Install with ``pip install "nuguard[service]"`` and run ``nuguard serve``. See
``documentation/docs/remote-api.md``.
"""

from __future__ import annotations

from nuguard import __version__

SERVICE_VERSION = __version__

__all__ = ["SERVICE_VERSION"]
