"""Dockerfile adapter — extracts CONTAINER_IMAGE nodes from Dockerfile* files.

Parses every ``FROM`` instruction and emits one ``ComponentDetection`` per
unique base image reference (``image_role="base"``; scratch and references to
earlier build stages are skipped), plus one ``image_role="app"`` node per
Dockerfile describing the *built* image: final-stage USER/HEALTHCHECK, ENTRYPOINT,
CMD, WORKDIR, EXPOSE, dependency manifests, packages installed by ``RUN`` and the
OS inferred from the base image. The app node is linked to its base with a
``BUILT_FROM`` relationship.

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

import re
from dataclasses import dataclass, field
from typing import Any

from nuguard.common.logging import get_logger

from ..image_os import infer_os
from ..image_ref import is_templated, parse_image_ref
from ..types import ComponentType
from .base import ComponentDetection, RelationshipHint

_log = get_logger(__name__)

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

_parse_image_ref = parse_image_ref  # backward-compatible alias

# Dependency manifests worth recording when COPY'd into an image.
_MANIFEST_RE = re.compile(
    r"^(?:requirements[\w.\-]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile(?:\.lock)?"
    r"|package(?:-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|go\.(?:mod|sum)|pom\.xml"
    r"|build\.gradle(?:\.kts)?|Gemfile(?:\.lock)?|composer\.(?:json|lock)|[\w.\-]+\.csproj)$",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|key)\s*[=:]\s*)\S+"
)
_MAX_INSTRUCTION_CHARS = 200
DEFAULT_MAX_IMAGE_PACKAGES = 200

# (regex over one shell command, package manager)
# Consume complete flag tokens without backtracking: pip's optional second dash
# and nested repetitions otherwise make malformed RUN commands exponentially slow.
# A single leading dash covers both short and long flags; the install verb cannot
# start with a dash, so committing to each flag preserves valid-command matching.
_INSTALL_COMMANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bapt(?:-get)?\s+(?:-\S++\s++)*+install\s+(?P<args>.+)", re.I), "apt"),
    (re.compile(r"\bapk\s+(?:-\S++\s++)*+add\s+(?P<args>.+)", re.I), "apk"),
    (re.compile(r"\b(?:yum|dnf|microdnf)\s+(?:-\S++\s++)*+install\s+(?P<args>.+)", re.I), "yum"),
    (re.compile(r"\bpip[0-9.]*\s+(?:-\S++\s++)*+install\s+(?P<args>.+)", re.I), "pip"),
    (re.compile(r"\bnpm\s+(?:i|install|add)\s+(?P<args>.+)", re.I), "npm"),
    (re.compile(r"\byarn\s+add\s+(?P<args>.+)", re.I), "npm"),
)
_FLAGS_WITH_VALUE = frozenset({"-r", "--requirement", "-c", "--constraint", "-e", "--editable",
                               "-i", "--index-url", "--extra-index-url", "-t", "--target",
                               "--prefix", "-f", "--find-links", "--registry"})


@dataclass
class _Instruction:
    line: int
    keyword: str
    args: str


@dataclass
class _Stage:
    ref: str
    alias: str | None
    line: int
    instructions: list[_Instruction] = field(default_factory=list)

    def last(self, keyword: str) -> _Instruction | None:
        for ins in reversed(self.instructions):
            if ins.keyword == keyword:
                return ins
        return None


def _logical_instructions(content: str) -> list[_Instruction]:
    """Dockerfile → instructions with ``\\`` continuations joined and comments dropped."""
    out: list[_Instruction] = []
    buf: list[str] = []
    start = 0
    for i, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if buf and stripped.startswith("#"):
            continue  # comment line inside a continuation
        if not buf:
            start = i
        if stripped.endswith("\\"):
            buf.append(stripped[:-1].strip())
            continue
        buf.append(stripped)
        text = " ".join(p for p in buf if p)
        buf = []
        kw, _, rest = text.partition(" ")
        out.append(_Instruction(start, kw.upper(), rest.strip()))
    if buf:
        text = " ".join(p for p in buf if p)
        kw, _, rest = text.partition(" ")
        out.append(_Instruction(start, kw.upper(), rest.strip()))
    return out


def _parse_stages(content: str) -> tuple[list[_Stage], dict[str, str]]:
    """Return build stages and global ``ARG`` defaults declared before the first FROM."""
    stages: list[_Stage] = []
    global_args: dict[str, str] = {}
    for ins in _logical_instructions(content):
        if ins.keyword == "FROM":
            toks = [t for t in ins.args.split() if not t.startswith("--platform")]
            if not toks:
                continue
            ref = toks[0]
            alias = toks[2] if len(toks) >= 3 and toks[1].lower() == "as" else None
            stages.append(_Stage(ref=ref, alias=alias, line=ins.line))
        elif not stages and ins.keyword == "ARG" and "=" in ins.args:
            k, _, v = ins.args.partition("=")
            global_args[k.strip()] = v.strip().strip("\"'")
        elif stages:
            stages[-1].instructions.append(ins)
    return stages, global_args


_ARG_REF_RE = re.compile(r"\$(?:\{(?P<braced>\w+)(?::?-(?P<default>[^}]*))?\}|(?P<bare>\w+))")


def _resolve_args(ref: str, args: dict[str, str]) -> str:
    """Substitute ``$VAR`` / ``${VAR:-default}`` from global ARG defaults (unknowns stay)."""

    def sub(m: re.Match[str]) -> str:
        name = m.group("braced") or m.group("bare")
        if name in args:
            return args[name]
        default = m.group("default")
        return default if default is not None else m.group(0)

    return _ARG_REF_RE.sub(sub, ref) if "$" in ref else ref


def _redact(text: str) -> str:
    return _SECRET_VALUE_RE.sub(r"\1***", text)[:_MAX_INSTRUCTION_CHARS]


def _split_packages(args: str, manager: str) -> list[tuple[str, str | None]]:
    """Package (name, version) tokens of one install command (names only; no paths/URLs)."""
    pkgs: list[tuple[str, str | None]] = []
    toks = args.split()
    skip = False
    for raw_tok in toks:
        tok = raw_tok.strip("\"'")
        if skip:
            skip = False
            continue
        if tok in _FLAGS_WITH_VALUE:
            skip = True
            continue
        if tok.startswith("-") or any(c in tok for c in "$`\"'<>|&;(){}*") or tok in {".", ".."}:
            continue
        if tok.startswith(("./", "../", "/", "http://", "https://", "git+")) or tok.endswith(
            (".txt", ".whl", ".tar.gz", ".deb", ".rpm")
        ):
            continue
        name, version = tok, None
        if manager == "pip":
            m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?(?:(?:==|~=|>=|<=|>|<)(.+))?$", tok)
            if not m:
                continue
            name, version = m.group(1), m.group(2)
        elif manager == "apt":
            name, _, version = tok.partition("=")
            version = version or None
        elif manager == "apk":
            name, _, version = tok.partition("=")
            version = version or None
        elif manager == "npm":
            at = tok.rfind("@")
            if at > 0:
                name, version = tok[:at], tok[at + 1 :]
        if name and re.match(r"^[@A-Za-z0-9][A-Za-z0-9_.+\-/@]*$", name):
            pkgs.append((name, version))
    return pkgs


def _run_packages(stage_chain: list[_Stage], limit: int) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for stage in stage_chain:
        for ins in stage.instructions:
            if ins.keyword != "RUN":
                continue
            for cmd in re.split(r"&&|;|\|\|", ins.args):
                for pattern, manager in _INSTALL_COMMANDS:
                    m = pattern.search(cmd)
                    if not m:
                        continue
                    for name, version in _split_packages(m.group("args"), manager):
                        key = (manager, name)
                        if key in seen:
                            continue
                        seen.add(key)
                        packages.append(
                            {"name": name, "version": version, "manager": manager,
                             "source": "dockerfile_run"}
                        )
                        if len(packages) >= limit:
                            return packages
                    break
    return packages


def _stage_chain(stages: list[_Stage], final: _Stage) -> list[_Stage]:
    """Final stage plus the earlier stages it inherits from via ``FROM <alias>``."""
    by_alias = {s.alias.lower(): s for s in stages if s.alias}
    chain = [final]
    cur = final
    while cur.ref.lower() in by_alias and by_alias[cur.ref.lower()] not in chain:
        cur = by_alias[cur.ref.lower()]
        chain.append(cur)
    return list(reversed(chain))  # oldest ancestor first


def _effective_base(stages: list[_Stage], final: _Stage, args: dict[str, str]) -> str | None:
    chain = _stage_chain(stages, final)
    ref = _resolve_args(chain[0].ref, args)
    if ref.lower() == "scratch" or is_templated(ref):
        return None
    return ref


class DockerfileAdapter:
    """Scans Dockerfile content and emits CONTAINER_IMAGE component detections.

    Unlike ``FrameworkAdapter``, this class is not AST-aware and is invoked
    directly by the extractor for files named ``Dockerfile*`` or ``*.dockerfile``.
    """

    name     = "dockerfile"
    priority = 5  # high priority — Dockerfiles are ground truth for container images

    def __init__(self, max_packages: int = DEFAULT_MAX_IMAGE_PACKAGES) -> None:
        self.max_packages = max_packages

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        """Return base-image detections, one app-image detection, and RUN-derived tools."""
        stages, global_args = _parse_stages(content)
        aliases = {s.alias.lower() for s in stages if s.alias}
        detections: list[ComponentDetection] = []
        seen: set[str] = set()

        for stage in stages:
            ref = _resolve_args(stage.ref.strip(), global_args)
            if ref.lower() == "scratch":
                _log.debug("%s: skipping FROM scratch", file_path)
                continue
            if ref.lower() in aliases:
                continue  # FROM <earlier stage>
            if is_templated(ref):
                _log.debug("%s: skipping unresolved FROM %s", file_path, ref)
                continue

            canonical = f"container_image:{ref.lower()}"
            if canonical in seen:
                continue
            seen.add(canonical)
            detections.append(self._base_detection(ref, canonical, stage, content, file_path))

        # Annotate base image nodes with file-level security signals
        self._annotate_security_signals(detections, content, file_path)
        self._annotate_exposed_ports(detections, content, file_path)

        app = self._app_detection(stages, global_args, content, file_path)
        if app is not None:
            detections.append(app)

        _log.info(
            "dockerfile adapter: %d image node(s) found in %s", len(detections), file_path
        )
        detections.extend(self._detect_run_tools(content, file_path))
        return detections

    # ------------------------------------------------------------------
    # Node builders
    # ------------------------------------------------------------------

    def _base_detection(
        self, ref: str, canonical: str, stage: _Stage, content: str, file_path: str
    ) -> ComponentDetection:
        parts = parse_image_ref(ref)
        name = parts["name"] or ref
        tag = parts["tag"]
        digest = parts["digest"]
        registry = parts["registry"]

        display = name
        if tag:
            display = f"{name}:{tag}"
        elif not digest:
            display = f"{name}:latest"

        metadata: dict[str, Any] = {
            "base_image": ref,
            "image_name": name,
            "image_tag": tag or ("" if digest else "latest"),
            "image_digest": digest,
            "registry": registry or "docker.io",
            "dockerfile": file_path,
            "image_role": "base",
        }
        self._apply_os(metadata, ref)
        _log.debug(
            "%s:%d — detected container image: %s (tag=%s digest=%s registry=%s)",
            file_path, stage.line, name, tag, digest, registry,
        )
        return ComponentDetection(
            component_type=ComponentType.CONTAINER_IMAGE,
            canonical_name=canonical,
            display_name=display,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.99,  # FROM is authoritative — no ambiguity
            metadata=metadata,
            file_path=file_path,
            line=stage.line,
            snippet=f"FROM {stage.ref}"[:120],
            evidence_kind="dockerfile",
        )

    @staticmethod
    def _apply_os(metadata: dict[str, Any], ref: str) -> None:
        os_info = infer_os(ref)
        if os_info is None:
            return
        metadata["os_name"] = os_info.name
        metadata["os_family"] = os_info.family
        metadata["os_evidence"] = os_info.evidence
        if os_info.version:
            metadata["os_version"] = os_info.version

    def _app_detection(
        self,
        stages: list[_Stage],
        global_args: dict[str, str],
        content: str,
        file_path: str,
    ) -> ComponentDetection | None:
        """The image this Dockerfile *builds* (its final stage)."""
        if not stages:
            return None
        final = stages[-1]
        chain = _stage_chain(stages, final)
        base_ref = _effective_base(stages, final, global_args)
        directory = file_path.rsplit("/", 1)[0] if "/" in file_path else "."
        label = directory.rsplit("/", 1)[-1] if directory != "." else "app"
        file_name = file_path.rsplit("/", 1)[-1]
        if file_name.lower() not in ("dockerfile",) and not file_name.lower().endswith(".dockerfile"):
            label = f"{label} ({file_name})"

        metadata: dict[str, Any] = {
            "image_role": "app",
            "dockerfile": file_path,
            "build_dir": directory,
            "multi_stage_build": len(stages) > 1,
        }
        if final.alias:
            metadata["stage_alias"] = final.alias
        if base_ref:
            metadata["base_image"] = base_ref
            self._apply_os(metadata, base_ref)

        def latest(keyword: str) -> _Instruction | None:
            for stage in reversed(chain):
                ins = stage.last(keyword)
                if ins is not None:
                    return ins
            return None

        for keyword, key in (("ENTRYPOINT", "entrypoint"), ("CMD", "cmd"), ("WORKDIR", "workdir")):
            ins = latest(keyword)
            if ins is not None:
                metadata[key] = _redact(ins.args)

        user = latest("USER")
        if user is not None:
            metadata["runs_as_root"] = user.args.split()[0].lower() in _ROOT_USERS
        hc = latest("HEALTHCHECK")
        if hc is not None:
            metadata["has_health_check"] = hc.args.strip().upper() != "NONE"

        manifests: list[str] = []
        for stage in chain:
            for ins in stage.instructions:
                if ins.keyword not in ("COPY", "ADD") or "--from" in ins.args.lower():
                    continue
                toks = [t for t in ins.args.split() if not t.startswith("--")]
                for src in toks[:-1]:
                    base = src.rstrip("/").rsplit("/", 1)[-1]
                    if _MANIFEST_RE.match(base) and src not in manifests:
                        manifests.append(src)
        if manifests:
            metadata["dependency_manifests"] = manifests

        packages = _run_packages(chain, self.max_packages)
        if packages:
            metadata["image_packages"] = packages

        ports = self._expose_ports(chain)
        if ports:
            metadata["exposed_ports"] = ports

        if _SECRET_ARG_ENV_RE.search(content):
            metadata["security_findings"] = ["secrets_in_build_args"]

        relationships: list[RelationshipHint] = []
        if base_ref:
            relationships.append(
                RelationshipHint(
                    source_canonical=f"container_image:app:{file_path.lower()}",
                    source_type=ComponentType.CONTAINER_IMAGE,
                    target_canonical=f"container_image:{base_ref.lower()}",
                    target_type=ComponentType.CONTAINER_IMAGE,
                    relationship_type="BUILT_FROM",
                )
            )
        return ComponentDetection(
            component_type=ComponentType.CONTAINER_IMAGE,
            canonical_name=f"container_image:app:{file_path.lower()}",
            display_name=f"{label} image",
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.97,
            metadata=metadata,
            file_path=file_path,
            line=final.line,
            snippet=f"FROM {final.ref}"[:120],
            evidence_kind="dockerfile",
            relationships=relationships,
        )

    @staticmethod
    def _expose_ports(chain: list[_Stage]) -> list[dict[str, Any]]:
        ports: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for stage in chain:
            for ins in stage.instructions:
                if ins.keyword != "EXPOSE":
                    continue
                for tok in ins.args.split():
                    num, _, proto = tok.partition("/")
                    if not num.isdigit():
                        continue
                    key = (int(num), (proto or "tcp").lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    ports.append(
                        {"container_port": key[0], "protocol": key[1],
                         "exposure": "unknown", "source": "EXPOSE"}
                    )
        return ports

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

    def _annotate_exposed_ports(
        self, detections: list[ComponentDetection], content: str, file_path: str
    ) -> None:
        """Attach EXPOSE ports as metadata on the CONTAINER_IMAGE node(s) in this file.

        Ports are deployment/infrastructure details of the image itself, not a
        distinct deployment concept — they used to be emitted as standalone
        DEPLOYMENT nodes (``Port 3010``, ``Port 80``), fragmenting the
        deployment graph. They describe which ports a container listens on,
        not the HTTP routes exposed by the application.
        """
        image_dets = [d for d in detections if d.component_type == ComponentType.CONTAINER_IMAGE]
        if not image_dets:
            return

        exposed_ports: list[dict[str, Any]] = []
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
                if not port_str.isdigit() or port_str in seen_ports:
                    continue
                seen_ports.add(port_str)
                _log.debug(
                    "%s:%d — detected EXPOSE port %s", file_path, line, port_str
                )
                exposed_ports.append(
                    {
                        "port": int(port_str),
                        "protocol": token.split("/")[1] if "/" in token else "tcp",
                    }
                )

        if not exposed_ports:
            return
        for det in image_dets:
            det.metadata["exposed_ports"] = exposed_ports

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
