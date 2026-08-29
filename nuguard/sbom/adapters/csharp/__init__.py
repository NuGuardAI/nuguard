"""C# framework adapters."""

from ._csharp_base import CSharpFrameworkAdapter
from .aspnet_core import CSharpAspNetCoreAdapter
from .auth import CSharpAuthAdapter
from .datastores import CSharpDatastoreAdapter
from .llm_clients import CSharpLLMClientsAdapter
from .mlnet import CSharpMLNetAdapter
from .prompts import CSharpPromptAdapter
from .semantic_kernel import (
    CSharpSemanticKernelAdapter,
)

__all__ = [
    "CSharpAspNetCoreAdapter",
    "CSharpAuthAdapter",
    "CSharpDatastoreAdapter",
    "CSharpFrameworkAdapter",
    "CSharpLLMClientsAdapter",
    "CSharpMLNetAdapter",
    "CSharpPromptAdapter",
    "CSharpSemanticKernelAdapter",
]
