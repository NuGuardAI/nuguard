"""DependencyAnalyzerPlugin — offline analysis of package dependencies.

Checks for:
- Unpinned dependencies
- Dev dependencies in production
- OSV.dev advisories
- Typosquatting heuristics
"""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument
from nuguard.sbom.toolbox.plugins._base import ToolResult

_OSV_URL = "https://api.osv.dev/v1/query"

# Common dev/test-only packages
_DEV_PACKAGES = frozenset(
    {
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "coverage",
        "mypy",
        "black",
        "ruff",
        "flake8",
        "pylint",
        "hypothesis",
        "factory-boy",
        "faker",
        "responses",
        "httpretty",
        "vcrpy",
        "bandit",
        "safety",
        "pre-commit",
        "tox",
        "nox",
        "isort",
        "autopep8",
        "pycodestyle",
        "pydocstyle",
    }
)

# Top-50 popular PyPI packages for typosquatting detection
_POPULAR_PACKAGES = [
    "requests",
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "flask",
    "django",
    "fastapi",
    "sqlalchemy",
    "pydantic",
    "boto3",
    "botocore",
    "click",
    "typer",
    "httpx",
    "aiohttp",
    "uvicorn",
    "gunicorn",
    "celery",
    "redis",
    "pymongo",
    "psycopg2",
    "pillow",
    "openai",
    "anthropic",
    "langchain",
    "transformers",
    "torch",
    "tensorflow",
    "sklearn",
    "scikit-learn",
    "cryptography",
    "paramiko",
    "jinja2",
    "yaml",
    "toml",
    "rich",
    "loguru",
    "pyyaml",
    "orjson",
    "ujson",
    "arrow",
    "pendulum",
    "pytest",
    "mypy",
    "black",
    "ruff",
    "poetry",
    "setuptools",
    "pip",
]


class DependencyAnalyzerPlugin:
    """Analyze package dependencies for risks."""

    def run(self, sbom: AiSbomDocument | dict, config: dict | None = None) -> ToolResult:
        """Analyze dependencies for version pinning, dev packages, and advisories.

        Args:
            sbom: :class:`~nuguard.models.sbom.AiSbomDocument` or plain dict.
            config: ``check_osv`` (bool) to query OSV.dev for advisories.

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult`.
        """
        config = config or {}
        if isinstance(sbom, dict):
            from nuguard.sbom.extractor.serializer import AiSbomSerializer
            doc = AiSbomSerializer.from_json(sbom)
        else:
            doc = sbom

        findings: list[dict] = []

        for dep in doc.deps:
            if not dep.name:
                continue

            # Unpinned version check
            if dep.version_spec is None or dep.version_spec.strip() in ("", "*"):
                findings.append(
                    {
                        "package": dep.name,
                        "severity": "warn",
                        "issue": "unpinned_version",
                        "description": f"Package '{dep.name}' has no version constraint.",
                        "remediation": f"Pin '{dep.name}' to a specific version.",
                    }
                )

            # Dev dependency detection
            if dep.name.lower().replace("_", "-") in {p.replace("_", "-") for p in _DEV_PACKAGES}:
                findings.append(
                    {
                        "package": dep.name,
                        "severity": "info",
                        "issue": "dev_dependency_in_production",
                        "description": f"Package '{dep.name}' is typically a dev/test dependency.",
                        "remediation": f"Move '{dep.name}' to dev/test dependency group.",
                    }
                )

            # Typosquatting heuristic
            typo_match = self._check_typosquatting(dep.name)
            if typo_match:
                findings.append(
                    {
                        "package": dep.name,
                        "severity": "warn",
                        "issue": "potential_typosquatting",
                        "description": (
                            f"Package '{dep.name}' is similar to popular package '{typo_match}' "
                            f"(edit distance < 2)."
                        ),
                        "remediation": f"Verify '{dep.name}' is the intended package.",
                    }
                )

        # OSV.dev advisory check
        if config.get("check_osv", False):
            findings.extend(self._check_osv(doc))

        if not findings:
            return ToolResult(
                status="pass",
                message="No dependency issues found.",
                details=[],
            )

        warn_count = sum(1 for f in findings if f["severity"] == "warn")
        info_count = sum(1 for f in findings if f["severity"] == "info")
        status = "warn" if warn_count > 0 else "pass"
        msg = (
            f"Dependency analysis: {len(findings)} issue(s), "
            f"{warn_count} warnings, {info_count} informational."
        )
        return ToolResult(status=status, message=msg, details=findings)

    @staticmethod
    def _check_typosquatting(name: str) -> str | None:
        """Return the popular package name if *name* is within edit distance 1."""
        name_norm = name.lower().replace("_", "-")
        for popular in _POPULAR_PACKAGES:
            pop_norm = popular.lower().replace("_", "-")
            if name_norm == pop_norm:
                return None  # exact match — not typosquatting
            if _levenshtein(name_norm, pop_norm) == 1:
                return popular
        return None

    @staticmethod
    def _check_osv(doc: AiSbomDocument) -> list[dict]:
        findings: list[dict] = []
        try:
            import httpx
        except ImportError:
            return []

        for dep in doc.deps:
            if not dep.name:
                continue
            ecosystem = dep.ecosystem or "PyPI"
            if ecosystem.lower() == "pypi":
                ecosystem = "PyPI"
            payload: dict = {"package": {"name": dep.name, "ecosystem": ecosystem}}
            if dep.version_spec:
                version = dep.version_spec.lstrip(">=<~^!=").split(",")[0].strip()
                if version:
                    payload["version"] = version
            try:
                resp = httpx.post(_OSV_URL, json=payload, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for vuln in (data.get("vulns") or [])[:3]:
                        vid = vuln.get("id", "UNKNOWN")
                        summary = vuln.get("summary", "")
                        findings.append(
                            {
                                "package": dep.name,
                                "severity": "warn",
                                "issue": "known_vulnerability",
                                "description": f"Known vulnerability in '{dep.name}': {summary} ({vid})",
                                "remediation": f"Upgrade '{dep.name}'. See https://osv.dev/{vid}",
                            }
                        )
            except Exception:
                pass

        return findings


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between *a* and *b*."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            insert = prev[j + 1] + 1
            delete = curr[j] + 1
            replace = prev[j] + (0 if ca == cb else 1)
            curr.append(min(insert, delete, replace))
        prev = curr
    return prev[-1]
