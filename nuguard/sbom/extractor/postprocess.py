"""Post-processing passes applied after per-file extraction completes.

These functions operate on an already-built ``node_map`` (accumulator dict)
or a fully assembled ``AiSbomDocument`` — deduplication, name improvement,
and cross-node suppression. They have no dependency on the per-file
extraction loop itself, only on the shared accumulator/document shapes
defined in :mod:`nuguard.sbom.extractor.core` and :mod:`nuguard.sbom.models`.
Split out of ``core.py`` to keep the per-file extraction orchestration and
the after-the-fact cleanup passes independently readable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger

from ..models import ScanSummary
from ..types import ComponentType

if TYPE_CHECKING:
    from ..models import AiSbomDocument
    from .core import _NodeAccumulator

_log = get_logger(__name__)


def _make_scan_summary(d: dict) -> ScanSummary:
    """Convert the dict from ``build_scan_summary`` into a typed ``ScanSummary``."""
    return ScanSummary(
        use_case=d.get("use_case_summary") or "",
        frameworks=d.get("frameworks") or [],
        modalities=d.get("modalities") or [],
        modality_support=d.get("modality_support") or {},
        api_endpoints=d.get("api_endpoints") or [],
        deployment_platforms=d.get("deployment_platforms") or [],
        regions=d.get("regions") or [],
        environments=d.get("environments") or [],
        deployment_urls=d.get("deployment_urls") or [],
        iac_accounts=d.get("subscription_account_project") or [],
        node_counts=d.get("node_type_counts") or {},
        data_classification=d.get("data_classification") or [],
        classified_tables=d.get("classified_tables") or [],
        # IaC security / resilience aggregate fields
        secret_stores=d.get("secret_stores") or [],
        availability_zones=d.get("availability_zones") or [],
        encryption_at_rest_coverage=bool(d.get("encryption_at_rest_coverage")),
        security_findings=d.get("security_findings") or [],
        iam_principals=d.get("iam_principals") or [],
        service_accounts=d.get("service_accounts") or [],
        # App-launch discovery
        startup_commands=d.get("startup_commands") or [],
        env_files=d.get("env_files") or [],
        env_var_keys=d.get("env_var_keys") or [],
        local_url=d.get("local_url"),
        staging_urls=d.get("staging_urls") or [],
        production_urls=d.get("production_urls") or [],
        log_paths=d.get("log_paths") or [],
        # Streaming output detection
        uses_streaming=bool(d.get("uses_streaming", False)),
        streaming_endpoints=d.get("streaming_endpoints") or [],
        # 1.5.0 additions
        instrumentation=d.get("instrumentation"),
        testing=d.get("testing"),
    )


def _dedup_by_name_prefix(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Remove accumulator entries whose name is a strict prefix of another
    entry of the same component type that shares at least one source file.

    Handles cases where a regex adapter extracts a truncated model name
    (e.g. ``gemini-2.0``) while an AST adapter extracts the full string
    (``gemini-2.0-flash``) from an adjacent line of the same call.
    The shorter entry is dropped and its evidence absorbed by the longer one.

    A prefix match only counts as a truncation if it ends on a delimiter
    boundary in the longer name (e.g. the ``-`` in ``gemini-2.0-flash``).
    Without this, auto-generated numeric-ID names collide by coincidence —
    e.g. ``Prompt 407`` is a raw string-prefix of ``Prompt 4078`` even though
    they are unrelated string literals at different line numbers.

    DEPLOYMENT is excluded entirely: its generic keyword nodes (e.g.
    "docker", accumulated from every file mentioning that word across the
    repo) are always a word-boundary-respecting prefix of specific IaC node
    names that start with the same technology (e.g. "Docker Release"), even
    though they are not the same entity — merging would silently reattribute
    the generic bucket's unrelated evidence to one specific workflow node.

    API_ENDPOINT is also excluded: its display name is derived from the
    handler function name, and REST/RPC handlers routinely share a
    word-boundary prefix while being genuinely distinct routes in the same
    controller file — e.g. NestJS's ``sendMessage``/``sendMessageStream``/
    ``sendMessageWithFiles`` (see studyield-app chat.controller.ts) or
    ``getUser``/``getUserById``. API_ENDPOINT nodes already dedup correctly
    on their own (method + path) canonical name, so this pass has no
    truncation case to fix here and only causes real, distinct routes to be
    silently dropped.
    """
    keys_to_remove: set[tuple[ComponentType, str]] = set()
    files_by_key: dict[tuple[ComponentType, str], set[str]] = {
        key: {ev.location.path for ev in acc.evidence if ev.location}
        for key, acc in node_map.items()
        if key[0] not in (ComponentType.DEPLOYMENT, ComponentType.API_ENDPOINT)
    }

    # Compare only key pairs that actually share at least one source file.
    # This avoids global O(n^2) comparisons across unrelated components.
    file_to_keys: dict[tuple[ComponentType, str], list[tuple[ComponentType, str]]] = {}
    for key, files in files_by_key.items():
        ctype = key[0]
        for file_path in files:
            file_to_keys.setdefault((ctype, file_path), []).append(key)

    for keys in file_to_keys.values():
        if len(keys) < 2:
            continue
        sorted_keys = sorted(keys, key=lambda key: node_map[key].display_name.lower())
        for index, key_a in enumerate(sorted_keys[:-1]):
            if key_a in keys_to_remove:
                continue
            name_a = node_map[key_a].display_name.lower()
            if not name_a:
                continue

            winner_key: tuple[ComponentType, str] | None = None
            winner_name = ""
            for key_b in sorted_keys[index + 1 :]:
                if key_b in keys_to_remove:
                    continue
                name_b = node_map[key_b].display_name.lower()
                if name_b == name_a:
                    continue
                if not name_b.startswith(name_a):
                    break
                # Require a delimiter boundary right after the prefix so we
                # don't treat coincidental numeric-ID overlaps (prompt_407 /
                # prompt_4078) as a truncated/full name pair.
                if name_b[len(name_a)].isalnum():
                    continue
                if len(name_b) > len(winner_name):
                    winner_key = key_b
                    winner_name = name_b

            if winner_key is not None:
                node_map[winner_key].evidence.extend(node_map[key_a].evidence)
                keys_to_remove.add(key_a)
                _log.debug(
                    "dedup_by_name_prefix: dropped %s → kept %s", key_a, winner_key
                )

    for k in keys_to_remove:
        del node_map[k]


_MODEL_FORMAT_SUFFIX_RE = re.compile(
    r"(?:-(?:gguf|q\d[\w]*|bf16|f16|fp16|fp32|awq|gptq))+$",
    re.IGNORECASE,
)


def _normalize_model_variant_name(name: str) -> str:
    """Normalize a MODEL name to its base model identity, stripping
    HuggingFace org prefixes, file extensions, and quantization/format
    suffixes — e.g. ``Qwen/Qwen3.5-9B-GGUF`` and ``Qwen3.5-9B-Q4_K_M.gguf``
    both normalize to ``qwen3.5-9b``.
    """
    normalized = name.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized.endswith(".gguf"):
        normalized = normalized[: -len(".gguf")]
    while True:
        stripped = _MODEL_FORMAT_SUFFIX_RE.sub("", normalized)
        if stripped == normalized:
            break
        normalized = stripped
    return normalized


def _dedup_model_variants(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Merge MODEL nodes that are different repo-id/filename spellings of the
    same preconfigured model config entry — e.g. a Python dict with separate
    ``repo_id`` (``unsloth/Qwen3.5-9B-GGUF``) and ``filename``
    (``Qwen3.5-9B-Q4_K_M.gguf``) fields on adjacent lines of the same file.
    ``_dedup_by_location`` only catches same-line duplicates, so this pass
    groups MODEL nodes by (source file, normalized base name) instead. The
    longest original name wins and absorbs the others' evidence.
    """
    groups: dict[tuple[str, str], set[tuple[ComponentType, str]]] = {}
    for key, acc in node_map.items():
        if key[0] != ComponentType.MODEL:
            continue
        normalized = _normalize_model_variant_name(acc.display_name)
        if not normalized:
            continue
        for ev in acc.evidence:
            if ev.location:
                groups.setdefault((ev.location.path, normalized), set()).add(key)

    keys_to_remove: set[tuple[ComponentType, str]] = set()
    for keys in groups.values():
        remaining = sorted(keys - keys_to_remove, key=lambda k: -len(node_map[k].display_name))
        if len(remaining) < 2:
            continue
        winner, *losers = remaining
        for loser in losers:
            if loser in keys_to_remove:
                continue
            node_map[winner].evidence.extend(node_map[loser].evidence)
            keys_to_remove.add(loser)
            _log.debug("dedup_model_variants: dropped %s → kept %s", loser, winner)

    for k in keys_to_remove:
        del node_map[k]


def _dedup_by_location(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Remove accumulator entries that share (component_type, file, line) with a
    higher-priority entry, merging their evidence into the winner.

    Applies when two adapters fire on the exact same source token — e.g. an AST
    adapter producing ``gemini-2.0-flash`` and a regex adapter producing
    ``gemini-2.0`` from the same line.  The lower-priority-number (higher
    precedence) adapter wins; ties broken by confidence descending.
    """
    # loc → {key, ...} for all keys that have at least one evidence item at that location
    loc_to_keys: dict[tuple[ComponentType, str, int | None], set[tuple[ComponentType, str]]] = {}
    has_regex_evidence: dict[tuple[ComponentType, str], bool] = {}
    for key, acc in node_map.items():
        has_regex_evidence[key] = any(ev.kind == "regex" for ev in acc.evidence)
        for ev in acc.evidence:
            if ev.location:
                loc = (key[0], ev.location.path, ev.location.line)
                loc_to_keys.setdefault(loc, set()).add(key)

    keys_to_remove: set[tuple[ComponentType, str]] = set()
    for loc, key_set in loc_to_keys.items():
        keys = list(key_set)
        if len(keys) <= 1:
            continue
        # Sort: lower priority number = higher precedence; break ties by confidence desc
        keys_sorted = sorted(
            keys,
            key=lambda k: (node_map[k].priority, -node_map[k].confidence),
        )
        winner = keys_sorted[0]
        for loser in keys_sorted[1:]:
            if loser in keys_to_remove:
                continue
            # Only deduplicate when at least one node has regex evidence.
            # Two AST-only nodes at the same line are distinct components (e.g.
            # multiple imports on one line → multiple FAISS stores) and must
            # both be kept.
            if not (has_regex_evidence.get(winner, False) or has_regex_evidence.get(loser, False)):
                continue
            # FRAMEWORK/DEPLOYMENT nodes with different canonical names are
            # distinct entities even when detected on the same source line.
            # For FRAMEWORK: a comment mentioning both "autogen" and "crewai"
            # triggers both regex adapters at the same line. For DEPLOYMENT: a
            # generic keyword node (e.g. "docker") accumulates evidence from
            # every file mentioning that word across the whole repo, so a
            # single coincidental same-line hit against a specific IaC node
            # (e.g. "deployment_github_actions_docker_release", whose slug
            # trivially contains the keyword as a substring) must not absorb
            # that node's unrelated evidence from every other file. Keep both
            # kinds separate so each gets its own node in the SBOM.
            if winner[0] in (ComponentType.FRAMEWORK, ComponentType.DEPLOYMENT) and winner[1] != loser[1]:
                continue
            # MODEL nodes: two canonical names at the same location are
            # usually the same model detected twice with different boundaries
            # (e.g. regex hit "gpt-5.4" and "openai/gpt-5.4" on one token —
            # one name contains the other as a substring). But a
            # fallback/comparison list like `[claude-sonnet-4-6,
            # claude-haiku-4-5]` puts two genuinely different models on the
            # same line, and neither name is a substring of the other — keep
            # those separate instead of one silently absorbing the other's
            # evidence.
            if winner[0] == ComponentType.MODEL and winner[1] != loser[1]:
                if winner[1] not in loser[1] and loser[1] not in winner[1]:
                    continue
            # Absorb evidence so the winner node reflects all source locations
            node_map[winner].evidence.extend(node_map[loser].evidence)
            keys_to_remove.add(loser)
            _log.debug(
                "dedup_by_location: dropped %s (priority=%d conf=%.2f) → kept %s",
                loser,
                node_map[loser].priority,
                node_map[loser].confidence,
                winner,
            )

    for k in keys_to_remove:
        del node_map[k]


# ---------------------------------------------------------------------------
# Auth / deployment name improvement
# ---------------------------------------------------------------------------

# Ordered rules: first matching rule wins.  Checked against evidence snippets
# so that the most prominent tech keyword in the evidence determines the name.
_AUTH_NAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bjwt\b", re.IGNORECASE), "jwt_auth"),
    (re.compile(r"\bbearer\b", re.IGNORECASE), "bearer_auth"),
    (re.compile(r"\boauth2?\b", re.IGNORECASE), "oauth_auth"),
    (re.compile(r"\b(api[_-]?key|apikey)\b", re.IGNORECASE), "api_key_auth"),
    (re.compile(r"\b(bcrypt|passlib|argon2|pbkdf2|scrypt)\b", re.IGNORECASE), "password_auth"),
    (re.compile(r"\b(session[_.]cookie|cookie[_.]jar|csrf[_.]token)\b", re.IGNORECASE), "session_auth"),
]

_DEPLOY_NAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgcloud\b", re.IGNORECASE), "gcloud_deployment"),
    (re.compile(r"\bgsutil\b", re.IGNORECASE), "gcloud_deployment"),
    (re.compile(r"\b(kubectl|kubernetes|kustomize|skaffold|argocd|fluxcd)\b", re.IGNORECASE), "kubernetes_deployment"),
    (re.compile(r"\b(az\s+(?:login|group|webapp|container|acr|aks|functionapp)|azure[_-]cli|azd)\b", re.IGNORECASE), "azure_deployment"),
    (re.compile(r"\baws\s+(?:ec2|s3|lambda|ecs|eks|rds|cloudformation|deploy|ecr)\b", re.IGNORECASE), "aws_deployment"),
    (re.compile(r"\b(terraform|pulumi|cdktf)\b", re.IGNORECASE), "terraform_deployment"),
    (re.compile(r"\bansible\b", re.IGNORECASE), "ansible_deployment"),
    (re.compile(r"\bhelm\b", re.IGNORECASE), "helm_deployment"),
    (re.compile(r"\bheroku\b", re.IGNORECASE), "heroku_deployment"),
    (re.compile(r"\bvercel\b", re.IGNORECASE), "vercel_deployment"),
    (re.compile(r"\bnetlify\b", re.IGNORECASE), "netlify_deployment"),
    (re.compile(r"\b(docker|compose)\b", re.IGNORECASE), "docker_deployment"),
    (re.compile(r"\b(nginx|gunicorn|uvicorn|caddy|traefik)\b", re.IGNORECASE), "server_deployment"),
]


def _improve_generic_node_names(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Rename AUTH/DEPLOYMENT nodes named 'generic' to a more descriptive label.

    Scans each node's evidence detail strings (e.g. "auth_generic: Bearer",
    "deployment_generic: gcloud") and applies ordered rules to derive a human-
    readable name such as 'bearer_auth' or 'gcloud_deployment'.
    """
    for (ct, _canon), acc in node_map.items():
        if acc.display_name != "generic":
            continue
        if ct == ComponentType.AUTH:
            rules = _AUTH_NAME_RULES
        elif ct == ComponentType.DEPLOYMENT:
            rules = _DEPLOY_NAME_RULES
        else:
            continue
        # Collect all evidence snippets (the part after the adapter prefix).
        snippets = [
            ev.detail.split(":", 1)[-1].strip() if ":" in ev.detail else ev.detail
            for ev in acc.evidence
        ]
        for pattern, new_name in rules:
            if any(pattern.search(s) for s in snippets):
                _log.debug(
                    "generic_name_improve: %s %r → %r",
                    ct.value,
                    acc.canonical_name,
                    new_name,
                )
                acc.display_name = new_name
                break


def _suppress_generic_tech_regex_datastore(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Suppress generic tech-name DATASTORE nodes emitted by regex adapters when
    the same file already has specific AST-detected DATASTORE nodes for the same
    underlying technology.

    Example: a regex adapter emitting ``faiss`` from an import line in a file
    where the AST adapter has already extracted specific named stores
    (``docs_index``, ``tickets_index``, etc. with ``provider="faiss"``).  The
    generic node is redundant and introduces false positives.

    Only purely regex-evidence nodes whose ``display_name`` matches a known
    vector-store / database technology shortname are candidates for suppression.
    Suppression only fires when at least one specific (non-regex) DATASTORE node
    shares the same source file AND has the same technology in its provider
    metadata (or its display name starts with the tech name as a prefix).
    """
    keys_to_remove: set[tuple[ComponentType, str]] = set()
    datastore_keys = [key for key in node_map if key[0] == ComponentType.DATASTORE]
    is_regex_only: dict[tuple[ComponentType, str], bool] = {}
    evidence_paths: dict[tuple[ComponentType, str], set[str]] = {}

    for key in datastore_keys:
        acc = node_map[key]
        is_regex_only[key] = all(ev.kind == "regex" for ev in acc.evidence)
        evidence_paths[key] = {
            ev.location.path for ev in acc.evidence if ev.location and ev.location.path
        }

    # Collect: file → set of tech names from specific (non-regex) DATASTORE nodes
    file_to_specific_techs: dict[str, set[str]] = {}
    for key in datastore_keys:
        acc = node_map[key]
        if is_regex_only[key]:
            continue  # Skip — this is itself a regex-only node
        provider = str(acc.metadata.get("provider", "")).lower().strip()
        tech = provider or acc.display_name.lower()
        for file_path in evidence_paths[key]:
            file_to_specific_techs.setdefault(file_path, set()).add(tech)

    # Identify generic regex-only DATASTORE nodes that are covered by specific ones
    for key in datastore_keys:
        acc = node_map[key]
        if not is_regex_only[key]:
            continue  # Only suppress regex-only nodes
        tech_name = acc.display_name.lower()
        # Check whether any source file for this node already has a specific node
        for file_path in evidence_paths[key]:
            specific_techs = file_to_specific_techs.get(file_path, set())
            if tech_name in specific_techs:
                keys_to_remove.add(key)
                _log.debug(
                    "suppress_generic_tech_regex: dropped generic %r"
                    " (file %s already has specific %r nodes)",
                    tech_name,
                    file_path,
                    tech_name,
                )
                break

    for k in keys_to_remove:
        del node_map[k]


def _dedup_generic_endpoints(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
) -> None:
    """Fold generic-regex API_ENDPOINT nodes into the matching framework node.

    The generic-regex adapter path (see the API_ENDPOINT grouping block in
    ``AiSbomExtractor.extract``) builds its ``canonical_name`` from the raw
    regex-captured path with no router-prefix resolution, so a route
    declared under a mounted router (e.g. ``/api/rag/re-embed``) never
    canonical-matches the generic node's unprefixed guess (``/re-embed``) in
    ``_merge_detection`` — leaving both as separate nodes. Rather than
    resolving prefixes for the regex path (fragile: it has no AST access to
    know which router variable a match belongs to, and this wouldn't cover
    frameworks like Flask that don't expose a prefix index at all), merge
    after the fact by matching on a path-segment-aligned suffix.

    Only folds a generic node when exactly one non-generic API_ENDPOINT node
    with the same HTTP method matches by suffix — an ambiguous match (e.g.
    ``/list`` matching both ``/api/x/list`` and ``/api/y/list``) is left
    alone rather than risk merging evidence into the wrong endpoint.
    """
    endpoint_keys = [key for key in node_map if key[0] == ComponentType.API_ENDPOINT]
    generic_keys = [key for key in endpoint_keys if node_map[key].metadata.get("_generic_endpoint_fallback")]
    real_keys = [key for key in endpoint_keys if key not in generic_keys]
    if not generic_keys or not real_keys:
        return

    keys_to_remove: set[tuple[ComponentType, str]] = set()
    for gkey in generic_keys:
        gacc = node_map[gkey]
        g_method = str(gacc.metadata.get("method") or "").upper()
        g_path = str(gacc.metadata.get("endpoint") or "")
        if not g_path:
            continue
        g_suffix = "/" + g_path.lstrip("/")

        matches = []
        for rkey in real_keys:
            racc = node_map[rkey]
            r_method = str(racc.metadata.get("method") or "").upper()
            if g_method and r_method and g_method != r_method:
                continue
            r_path = str(racc.metadata.get("endpoint") or "")
            if not r_path:
                continue
            r_full = "/" + r_path.lstrip("/")
            if r_full == g_suffix or r_full.endswith(g_suffix):
                matches.append(rkey)

        if len(matches) != 1:
            continue  # no match, or ambiguous — leave the generic node alone
        winner = matches[0]
        node_map[winner].evidence.extend(gacc.evidence)
        keys_to_remove.add(gkey)
        _log.debug(
            "dedup_generic_endpoints: folded generic %r (%s) into %r",
            g_path,
            g_method or "ANY",
            node_map[winner].display_name,
        )

    for k in keys_to_remove:
        del node_map[k]


def _suppress_non_code_model_datastore(
    node_map: dict[tuple[ComponentType, str], "_NodeAccumulator"],
    docs_tier_rank: int,
) -> None:
    """Drop MODEL and DATASTORE nodes whose only evidence is from DOCS tier.

    Detections from lock files (``pnpm-lock.yaml``, ``package-lock.json``),
    README mentions, shell scripts, and plain-text files frequently produce
    spurious MODEL/DATASTORE nodes.  These files are classified as DOCS tier;
    IaC-tier detections (YAML configs, JSON configs, Dockerfiles) are kept
    because they legitimately describe datastores and models in environment
    definitions.

    Only DOCS-tier-only nodes are dropped.  This does not affect AGENT, TOOL,
    PROMPT, etc. which are typically harder to detect and worth surfacing from
    any tier.

    ``docs_tier_rank`` is the caller's ``_TIER_RANK[_TIER_DOCS]`` value,
    passed in rather than imported so this module has no dependency on
    ``core.py``'s tier-classification constants.
    """
    _suppressed_types = {ComponentType.MODEL, ComponentType.DATASTORE}
    keys_to_drop = [
        key
        for key, acc in node_map.items()
        if key[0] in _suppressed_types and acc.best_tier_rank >= docs_tier_rank
    ]
    for key in keys_to_drop:
        _log.debug(
            "suppress_docs_only: dropped %s (best_tier_rank=%d)",
            key,
            node_map[key].best_tier_rank,
        )
        del node_map[key]


def _dedup_deployment_nodes(doc: "AiSbomDocument") -> None:
    """Merge GitHub Actions workflow DEPLOYMENT nodes into the cloud-provider
    service nodes they deploy to, avoiding duplicate Azure/GHA entries.

    When the same deployment is described by both a GitHub Actions workflow
    node (deployment_target=github-actions) and one or more cloud-provider
    nodes (deployment_target=azure, aws, gcp), the GHA node is merged into
    the cloud node: CI/CD metadata (triggers, runners) is copied across, then
    the GHA node is removed.
    """
    deployment_nodes = [n for n in doc.nodes if n.component_type == ComponentType.DEPLOYMENT]

    # Build index: deployment_target → list of nodes
    by_target: dict[str, list] = {}
    for node in deployment_nodes:
        target = node.metadata.extras.get("deployment_target", "unknown")
        by_target.setdefault(target, []).append(node)

    gha_nodes = by_target.get("github-actions", [])
    # Only true generic-keyword-bucket nodes (deployment_generic adapter) are
    # removal candidates below. Using by_target["unknown"] here too would also
    # sweep in unrelated, structured DEPLOYMENT nodes that simply don't set a
    # deployment_target extra (e.g. a Dockerfile EXPOSE-port node or an nginx
    # proxy_pass node) — those would then satisfy the substring check against
    # their own canonical name and get wrongly removed.
    generic_nodes = [
        n for n in deployment_nodes
        if n.metadata.extras.get("adapter") == "deployment_generic"
    ]
    nodes_to_remove = []

    # A generic keyword node (e.g. "docker", "vercel", "kustomize" — one per
    # matched technology, see the deployment_generic adapter) is only
    # redundant when a more specific, non-generic DEPLOYMENT node already
    # names that same technology (e.g. "deployment_github_actions_docker_
    # release" already covers "docker"). Checking "any GHA/IaC node exists
    # at all" is not enough: having CI/CD workflows or a cloud deployment
    # target says nothing about whether e.g. Vercel or Kustomize usage is
    # covered elsewhere, and would otherwise wipe out every generic node
    # whenever the repo has so much as one GitHub Actions workflow.
    specific_canonical_names = [
        (n.metadata.extras.get("canonical_name") or "").lower()
        for n in deployment_nodes
        if n.metadata.extras.get("adapter") != "deployment_generic"
    ]

    for generic_node in generic_nodes:
        generic_canon = (generic_node.metadata.extras.get("canonical_name") or "").lower()
        if generic_canon and any(generic_canon in specific for specific in specific_canonical_names):
            if generic_node not in nodes_to_remove:
                nodes_to_remove.append(generic_node)

    # Merge GitHub Actions workflow nodes into the cloud-provider nodes they deploy to
    for gha_node in gha_nodes:
        if gha_node in nodes_to_remove:
            continue
        cloud_providers: list[str] = gha_node.metadata.extras.get("cloud_providers") or []
        for provider in cloud_providers:
            cloud_nodes = [
                n for n in by_target.get(provider, [])
                if n not in nodes_to_remove
                and n.metadata.extras.get("adapter") != "deployment_generic"
            ]
            if len(cloud_nodes) == 1:
                # Merge CI/CD metadata from the GHA workflow node into the cloud node
                target_node = cloud_nodes[0]
                gha_extras = gha_node.metadata.extras
                target_extras = target_node.metadata.extras
                for key in ("workflow_triggers", "runners", "uses_oidc", "secret_store"):
                    if key not in target_extras and gha_extras.get(key) is not None:
                        target_extras[key] = gha_extras[key]
                # Merge evidence (deduplicated)
                existing_locs = {
                    (e.location.path if e.location else None) for e in target_node.evidence
                }
                for ev in gha_node.evidence:
                    ev_path = ev.location.path if ev.location else None
                    if ev_path not in existing_locs:
                        target_node.evidence.append(ev)
                        existing_locs.add(ev_path)
                nodes_to_remove.append(gha_node)
                break  # only merge into one cloud node per GHA node

    for node in nodes_to_remove:
        if node in doc.nodes:
            doc.nodes.remove(node)
