"""Dockerfile adapter — extracts CONTAINER_IMAGE nodes from Dockerfile* files.

Parses every ``FROM`` instruction and emits one ``ComponentDetection`` per
unique base image reference.  Multi-stage builds produce one node per stage
image (scratch stages are skipped).

Supported syntaxes
------------------
    FROM <image>
    FROM <image>:<tag>
    FROM <image>@sha256:<digest>
    FROM <image>:<tag>@sha256:<digest>
    FROM [--platform=<platform>] <image>[:<tag>][@<digest>] [AS <alias>]
    FROM registry.example.com/org/image:1.2.3

Evidence kind: ``"dockerfile"``
"""
from __future__ import annotations

import logging
import re
from typing import Any

from xelo.adapters.base import ComponentDetection
from xelo.types import ComponentType

_log = logging.getLogger(__name__)

# Matches a FROM instruction (case-insensitive, handles --platform flag and AS alias)
_FROM_RE = re.compile(
    r"^\s*FROM\s+"
    r"(?:--platform=\S+\s+)?"   # optional --platform=...
    r"(?P<ref>[^\s#]+)"          # the image reference (no whitespace, no comment)
    r"(?:\s+AS\s+\S+)?",         # optional AS <alias>
    re.IGNORECASE | re.MULTILINE,
)

# EXPOSE <port> [<port>/<protocol>...]
_EXPOSE_RE = re.compile(
    r"^\s*EXPOSE\s+(?P<ports>[\d/\w\s]+)",
    re.IGNORECASE | re.MULTILINE,
)

# RUN … playwright install … (covers pip install playwright + npm exec playwright)
_RUN_PLAYWRIGHT_RE = re.compile(
    r"^\s*RUN\b.*\bplaywright\b",
    re.IGNORECASE | re.MULTILINE,
)

# RUN … pip install <pkg> or apt-get install <pkg> (for nginx / gunicorn / uvicorn)
_RUN_DEPLOY_TOOLS_RE = re.compile(
    r"^\s*RUN\b.*(?:nginx|gunicorn|uvicorn|caddy|traefik)",
    re.IGNORECASE | re.MULTILINE,
)

# USER instruction — detect root user
# USER root | USER 0 | USER 0:0 → runs as root
_USER_RE = re.compile(
    r"^\s*USER\s+(?P<user>\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_ROOT_USERS = frozenset({"root", "0", "0:0", "0:root", "root:0", "root:root"})

# HEALTHCHECK instruction
_HEALTHCHECK_RE = re.compile(
    r"^\s*HEALTHCHECK\b(?P<rest>[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)

# ARG / ENV instructions with secret-sounding names
_SECRET_ARG_ENV_RE = re.compile(
    r"^\s*(?:ARG|ENV)\s+(?P<name>[A-Z0-9_]+(?:_KEY|_SECRET|_TOKEN|_PASSWORD|_PASS|_CREDENTIAL|_APIKEY|_API_KEY|_ACCESS_KEY|_PRIVATE_KEY))",
    re.IGNORECASE | re.MULTILINE,
)

# Splits an image reference into registry + name + tag + digest
# Examples:
#   python:3.12-slim               → name=python  tag=3.12-slim  digest=None
#   gcr.io/myproj/app:latest       → registry=gcr.io name=myproj/app tag=latest
#   ubuntu@sha256:abc123           → name=ubuntu   digest=sha256:abc123
#   registry/img:tag@sha256:abc    → name=registry/img tag=tag digest=sha256:abc
_REF_RE = re.compile(
    r"^"
    r"(?:(?P<registry>[a-zA-Z0-9._\-]+\.[a-zA-Z]{2,}(?::[0-9]+)?)/)?"  # optional registry
    r"(?P<name>[^:@\s]+)"                                                # image name / path
    r"(?::(?P<tag>[^@\s]+))?"                                            # optional :tag
    r"(?:@(?P<digest>sha256:[a-f0-9]{7,}))?"                             # optional @sha256:…
    r"$",
)


def _parse_image_ref(ref: str) -> dict[str, str | None]:
    """Break *ref* into registry, name, tag, digest components."""
    m = _REF_RE.match(ref.strip())
    if not m:
        return {"registry": None, "name": ref, "tag": None, "digest": None}
    return {
        "registry": m.group("registry"),
        "name":     m.group("name"),
        "tag":      m.group("tag"),
        "digest":   m.group("digest"),
    }


class DockerfileAdapter:
    """Scans Dockerfile content and emits CONTAINER_IMAGE component detections.

    Unlike ``FrameworkAdapter``, this class is not AST-aware and is invoked
    directly by the extractor for files named ``Dockerfile`` or ``*.dockerfile``.
    """

    name     = "dockerfile"
    priority = 5  # high priority — Dockerfiles are ground truth for container images

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        """Return one ``ComponentDetection`` per unique base image in *content*."""
        detections: list[ComponentDetection] = []
        seen: set[str] = set()

        for match in _FROM_RE.finditer(content):
            ref = match.group("ref").strip()
            if ref.lower() == "scratch":
                _log.debug("%s: skipping FROM scratch", file_path)
                continue

            line = content[: match.start()].count("\n") + 1
            canonical = f"container_image:{ref.lower()}"

            if canonical in seen:
                continue
            seen.add(canonical)

            parts = _parse_image_ref(ref)
            name  = parts["name"] or ref
            tag   = parts["tag"]
            digest = parts["digest"]
            registry = parts["registry"]

            # Build a compact display name
            display = name
            if tag:
                display = f"{name}:{tag}"
            elif not digest:
                display = f"{name}:latest"

            metadata: dict[str, Any] = {
                "base_image": ref,
                "image_name": name,
                "image_tag":  tag or ("" if digest else "latest"),
                "image_digest": digest,
                "registry":  registry or "docker.io",
                "dockerfile": file_path,
            }

            _log.debug(
                "%s:%d — detected container image: %s (tag=%s digest=%s registry=%s)",
                file_path, line, name, tag, digest, registry,
            )

            detections.append(
                ComponentDetection(
                    component_type=ComponentType.CONTAINER_IMAGE,
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.99,  # FROM is authoritative — no ambiguity
                    metadata=metadata,
                    file_path=file_path,
                    line=line,
                    snippet=match.group(0).strip()[:120],
                    evidence_kind="dockerfile",
                )
            )

        # Annotate the first image node with security signals (or all if none shared)
        self._annotate_security_signals(detections, content, file_path)

        _log.info(
            "dockerfile adapter: %d unique image(s) found in %s",
            len(detections), file_path,
        )
        detections.extend(self._detect_exposed_ports(content, file_path))
        detections.extend(self._detect_run_tools(content, file_path))
        return detections

    def _annotate_security_signals(
        self, detections: list[ComponentDetection], content: str, file_path: str
    ) -> None:
        """Inject security metadata into existing CONTAINER_IMAGE detections.

        Populates ``runs_as_root``, ``has_health_check``, and
        ``security_findings`` on the metadata dict of every image node.
        """
        if not detections:
            return

        # Count FROM statements to detect multi-stage builds
        from_count = len(_FROM_RE.findall(content))
        multi_stage = from_count > 1

        # USER instruction: scan all USER lines; last one wins for final stage
        runs_as_root: bool | None = None
        for m in _USER_RE.finditer(content):
            user_val = m.group("user").strip().lower()
            if user_val in _ROOT_USERS:
                runs_as_root = True
            else:
                runs_as_root = False  # non-root user explicitly set

        # HEALTHCHECK instruction
        has_health_check: bool | None = None
        hc_m = _HEALTHCHECK_RE.search(content)
        if hc_m:
            rest = hc_m.group("rest").strip().upper()
            has_health_check = rest != "NONE"

        # Secret-like ARG / ENV names
        security_findings: list[str] = []
        if _SECRET_ARG_ENV_RE.search(content):
            security_findings.append("secrets_in_build_args")

        # Write signals into every CONTAINER_IMAGE detection in this file
        for det in detections:
            if det.component_type != ComponentType.CONTAINER_IMAGE:
                continue
            det.metadata["runs_as_root"] = runs_as_root
            det.metadata["has_health_check"] = has_health_check
            det.metadata["multi_stage_build"] = multi_stage
            if security_findings:
                det.metadata["security_findings"] = security_findings
        _log.debug(
            "%s: security signals — runs_as_root=%s healthcheck=%s multi_stage=%s findings=%s",
            file_path,
            runs_as_root,
            has_health_check,
            multi_stage,
            security_findings,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_exposed_ports(
        self, content: str, file_path: str
    ) -> list[ComponentDetection]:
        """Emit API_ENDPOINT nodes for each EXPOSE instruction."""
        results: list[ComponentDetection] = []
        seen_ports: set[str] = set()
        for match in _EXPOSE_RE.finditer(content):
            raw_ports = match.group("ports")
            line = content[: match.start()].count("\n") + 1
            for token in raw_ports.split():
                token = token.strip()
                if not token:
                    continue
                # Normalise port spec: strip trailing /tcp|/udp
                port_str = token.split("/")[0]
                if not port_str.isdigit():
                    continue
                canonical = f"api_endpoint:port:{port_str}"
                if canonical in seen_ports:
                    continue
                seen_ports.add(canonical)
                _log.debug(
                    "%s:%d — detected EXPOSE port %s", file_path, line, port_str
                )
                results.append(
                    ComponentDetection(
                        component_type=ComponentType.API_ENDPOINT,
                        canonical_name=canonical,
                        display_name=f"Port {port_str}",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "port": int(port_str),
                            "protocol": token.split("/")[1] if "/" in token else "tcp",
                            "source": "dockerfile_expose",
                        },
                        file_path=file_path,
                        line=line,
                        snippet=match.group(0).strip()[:120],
                        evidence_kind="dockerfile",
                    )
                )
        return results

    def _detect_run_tools(
        self, content: str, file_path: str
    ) -> list[ComponentDetection]:
        """Emit TOOL nodes for ``RUN playwright install`` instructions."""
        results: list[ComponentDetection] = []
        seen: set[str] = set()
        for match in _RUN_PLAYWRIGHT_RE.finditer(content):
            canonical = "tool:playwright"
            if canonical in seen:
                continue
            seen.add(canonical)
            line = content[: match.start()].count("\n") + 1
            _log.debug("%s:%d — detected RUN playwright install", file_path, line)
            results.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonical,
                    display_name="Playwright",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={"source": "dockerfile_run", "category": "browser_automation"},
                    file_path=file_path,
                    line=line,
                    snippet=match.group(0).strip()[:120],
                    evidence_kind="dockerfile",
                )
            )
        return results
