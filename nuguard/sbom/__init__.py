"""xelo — deterministic AI SBOM generator."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("xelo")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0.dev0"

from .config import AiSbomConfig
from .extractor import AiSbomExtractor
from .models import AiSbomDocument
from .serializer import AiSbomSerializer

__all__ = [
    "__version__",
    "AiSbomDocument",
    "AiSbomConfig",
    "AiSbomExtractor",
    "AiSbomSerializer",
]
