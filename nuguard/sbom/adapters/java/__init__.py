"""First-class Java source adapters."""

from ._java_base import JavaFrameworkAdapter
from .java_ai import JavaAIAdapter
from .java_web import JavaWebAdapter

__all__ = ["JavaAIAdapter", "JavaFrameworkAdapter", "JavaWebAdapter"]
