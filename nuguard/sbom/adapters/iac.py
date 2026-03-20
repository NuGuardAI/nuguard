"""IaC (Infrastructure-as-Code) adapters.

Extracts security and resilience metadata from cloud infrastructure files.

Adapters
--------
``K8sAdapter``
    Kubernetes manifests — workload DEPLOYMENT nodes (security context, probes,
    resource limits, HA) and RBAC IAM nodes (ServiceAccount, Role, RoleBinding).

``TerraformAdapter``
    HashiCorp Terraform (``.tf`` / ``.tfvars``) — DEPLOYMENT + IAM nodes for
    AWS, Azure, and GCP resources.

``CloudFormationAdapter``
    AWS CloudFormation (YAML/JSON with ``AWSTemplateFormatVersion`` or ``AWS::``
    resource types) — DEPLOYMENT + IAM nodes.

``BicepAdapter``
    Azure Bicep (``.bicep``) — DEPLOYMENT + IAM nodes for Azure resources.

``GcpDeploymentManagerAdapter``
    GCP Deployment Manager (YAML ``resources:`` lists with ``gcp-types/`` or
    ``*.v1.*`` type prefixes) and Jinja2 templates (``.jinja``).

All adapters return ``list[ComponentDetection]`` and follow the same pattern
as ``DockerfileAdapter`` — no AST dependency, invoked directly by the
extractor.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xelo.adapters.base import ComponentDetection
from xelo.types import ComponentType

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _try_load_yaml(content: str) -> Any:
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(content)
    except Exception:  # noqa: BLE001
        return None


def _try_load_json(content: str) -> Any:
    try:
        import json

        return json.loads(content)
    except Exception:  # noqa: BLE001
        return None


def _first_line(text: str, pattern: re.Pattern[str]) -> int:
    """Return 1-based line number of the first match of *pattern* in *text*."""
    m = pattern.search(text)
    if m is None:
        return 1
    return text[: m.start()].count("\n") + 1


def _uniq(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in lst:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _cap20(lst: list[str]) -> list[str]:
    return lst[:20]


def _make_det(
    *,
    component_type: ComponentType,
    canonical_name: str,
    display_name: str,
    adapter_name: str,
    confidence: float,
    metadata: dict[str, Any],
    file_path: str,
    line: int = 1,
    snippet: str = "",
) -> ComponentDetection:
    return ComponentDetection(
        component_type=component_type,
        canonical_name=canonical_name,
        display_name=display_name,
        adapter_name=adapter_name,
        priority=8,  # IaC adapters — below framework adapters (1-7) but above regex-only
        confidence=confidence,
        metadata=metadata,
        file_path=file_path,
        line=line,
        snippet=snippet[:120],
        evidence_kind="iac",
    )


# ---------------------------------------------------------------------------
# Kubernetes adapter
# ---------------------------------------------------------------------------

#: Workload kinds that warrant a DEPLOYMENT node
_K8S_WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"})
#: RBAC kinds that warrant an IAM node
_K8S_IAM_KINDS = frozenset(
    {
        "ServiceAccount",
        "Role",
        "ClusterRole",
        "RoleBinding",
        "ClusterRoleBinding",
    }
)
_K8S_ALL_KINDS = _K8S_WORKLOAD_KINDS | _K8S_IAM_KINDS

_K8S_API_VERSION_RE = re.compile(r"^\s*apiVersion\s*:", re.MULTILINE)
_K8S_KIND_RE = re.compile(r"^\s*kind\s*:\s*(\S+)", re.MULTILINE)


def _is_k8s_manifest(data: Any, content: str) -> bool:
    """True when the YAML looks like a Kubernetes manifest."""
    if not isinstance(data, dict):
        return False
    if "apiVersion" not in data or "kind" not in data:
        return False
    kind = str(data.get("kind", ""))
    return kind in _K8S_ALL_KINDS or bool(re.search(r"^[A-Z]", kind))


def _extract_rbac_permissions(rules: list[Any]) -> list[str]:
    """Summarise K8s RBAC rules as 'verb:resource' strings (up to 20)."""
    out: list[str] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        verbs = rule.get("verbs") or []
        resources = rule.get("resources") or []
        for r in resources:
            for v in verbs:
                out.append(f"{v}:{r}")
                if len(out) >= 20:
                    return out
    return out


def _k8s_name(data: dict[str, Any]) -> str:
    return str((data.get("metadata") or {}).get("name") or "unknown")


def _k8s_namespace(data: dict[str, Any]) -> str | None:
    ns = (data.get("metadata") or {}).get("namespace")
    return str(ns) if ns else None


class K8sAdapter:
    """Kubernetes manifest adapter.

    Triggered on YAML files that contain both ``apiVersion`` and ``kind`` keys
    and whose ``kind`` is a known workload or RBAC type.

    Emits:
    - ``DEPLOYMENT`` nodes for workload kinds
    - ``IAM`` nodes for RBAC kinds
    """

    name = "k8s"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        if "apiVersion" not in content or "kind" not in content:
            return []

        data = _try_load_yaml(content)
        if not _is_k8s_manifest(data, content):
            return []

        kind = str(data.get("kind", ""))
        if kind in _K8S_WORKLOAD_KINDS:
            return self._workload(data, content, file_path)
        if kind in _K8S_IAM_KINDS:
            return self._rbac(data, content, file_path)
        return []

    # ------------------------------------------------------------------
    # Workload → DEPLOYMENT
    # ------------------------------------------------------------------

    def _workload(
        self, data: dict[str, Any], content: str, file_path: str
    ) -> list[ComponentDetection]:
        name = _k8s_name(data)
        namespace = _k8s_namespace(data) or "default"
        spec = data.get("spec") or {}
        pod_spec = (spec.get("template") or {}).get("spec") or {}
        security_ctx = pod_spec.get("securityContext") or {}
        containers = pod_spec.get("containers") or []

        # HA mode
        ha_mode: str | None = None
        replicas = spec.get("replicas")
        if isinstance(replicas, int) and replicas > 1:
            ha_mode = "replicated"
        if spec.get("topologySpreadConstraints") or (pod_spec.get("affinity") or {}).get(
            "nodeAffinity"
        ):
            ha_mode = "multi-az"

        # Security context: runs as root?
        runs_as_root: bool | None = None
        if security_ctx.get("runAsNonRoot") is False:
            runs_as_root = True
        elif security_ctx.get("runAsUser") == 0:
            runs_as_root = True
        elif security_ctx.get("runAsNonRoot") is True:
            runs_as_root = False
        elif security_ctx.get("runAsUser") is not None and security_ctx["runAsUser"] != 0:
            runs_as_root = False
        # Also check container-level securityContext
        for c in containers:
            csc = (c or {}).get("securityContext") or {}
            if csc.get("runAsNonRoot") is False or csc.get("runAsUser") == 0:
                runs_as_root = True
                break
            if csc.get("runAsNonRoot") is True and runs_as_root is None:
                runs_as_root = False

        # Health checks
        has_health_check: bool | None = (
            any(
                (c or {}).get("livenessProbe") or (c or {}).get("readinessProbe")
                for c in containers
            )
            or None
        )
        if has_health_check is None and containers:
            has_health_check = False  # containers exist but no probes

        # Resource limits
        has_resource_limits: bool | None = None
        if containers:
            has_resource_limits = any(
                (c or {}).get("resources", {}).get("limits") for c in containers
            )

        # Secret store via env references or volume secrets
        secret_store: str | None = None
        for c in containers:
            for ev in (c or {}).get("env") or []:
                if isinstance(ev, dict) and (ev.get("valueFrom") or {}).get("secretKeyRef"):
                    secret_store = "k8s_secret"
                    break
            if secret_store:
                break
        if not secret_store:
            for vol in pod_spec.get("volumes") or []:
                if isinstance(vol, dict) and vol.get("secret"):
                    secret_store = "k8s_secret"
                    break

        # Detect cloud-provider secret store annotations in pod annotations / labels
        annotations = (data.get("metadata") or {}).get("annotations") or {}
        for k in annotations:
            k_lower = str(k).lower()
            if "vault" in k_lower:
                secret_store = "hashicorp_vault"
            elif "secretsmanager" in k_lower or "secrets-store" in k_lower:
                secret_store = "aws_secrets_manager"
            elif "keyvault" in k_lower:
                secret_store = "azure_key_vault"

        canonical = f"deployment:k8s:{namespace}:{name}".lower()
        meta: dict[str, Any] = {
            "iac_format": "kubernetes",
            "k8s_kind": data.get("kind"),
            "k8s_namespace": namespace,
            "deployment_target": "kubernetes",
            "ha_mode": ha_mode,
            "runs_as_root": runs_as_root,
            "has_health_check": has_health_check,
            "has_resource_limits": has_resource_limits,
            "secret_store": secret_store,
        }
        _log.debug("k8s_adapter: DEPLOYMENT %s in %s", name, file_path)
        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=name,
                adapter_name=self.name,
                confidence=0.95,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"{data.get('kind')}: {name}",
            )
        ]

    # ------------------------------------------------------------------
    # RBAC → IAM
    # ------------------------------------------------------------------

    def _rbac(self, data: dict[str, Any], content: str, file_path: str) -> list[ComponentDetection]:
        kind = str(data.get("kind", ""))
        name = _k8s_name(data)
        namespace = _k8s_namespace(data)

        iam_type: str
        permissions: list[str] | None = None
        trust_principals: list[str] | None = None
        iam_scope: str | None = None

        if kind == "ServiceAccount":
            iam_type = "service_account"
            iam_scope = "namespace" if namespace else "cluster"
        elif kind in ("Role", "ClusterRole"):
            iam_type = "role"
            permissions = _extract_rbac_permissions(data.get("rules") or []) or None
            iam_scope = "namespace" if kind == "Role" else "cluster"
        else:  # RoleBinding / ClusterRoleBinding
            iam_type = "role_binding"
            subjects = data.get("subjects") or []
            trust_principals = [
                str(s.get("name", "")) for s in subjects if isinstance(s, dict) and s.get("name")
            ] or None
            iam_scope = "namespace" if kind == "RoleBinding" else "cluster"

        canonical = f"iam:k8s:{kind}:{name}".lower()
        meta: dict[str, Any] = {
            "iac_format": "kubernetes",
            "iam_type": iam_type,
            "principal": name,
            "iam_scope": iam_scope,
            "permissions": _cap20(permissions) if permissions else None,
            "trust_principals": trust_principals,
            "k8s_namespace": namespace,
        }
        return [
            _make_det(
                component_type=ComponentType.IAM,
                canonical_name=canonical,
                display_name=name,
                adapter_name=self.name,
                confidence=0.92,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"{kind}: {name}",
            )
        ]


# ---------------------------------------------------------------------------
# Terraform adapter
# ---------------------------------------------------------------------------

# Provider detection
_TF_PROVIDER_RE = re.compile(r'provider\s+"(aws|azurerm|google|vault|kubernetes)"', re.IGNORECASE)

# Region
_TF_REGION_RE = re.compile(r'\bregion\s*=\s*"([^"]+)"')
_TF_LOCATION_RE = re.compile(r'\blocation\s*=\s*"([^"]+)"')

# AZs
_TF_AZ_LIST_RE = re.compile(r"availability_zones\s*=\s*\[([^\]]+)\]")
_TF_AZ_SINGLE_RE = re.compile(r'\bavailability_zone\s*=\s*"([^"]+)"')
_TF_MULTI_AZ_RE = re.compile(r"\bmulti_az\s*=\s*true", re.IGNORECASE)
_TF_MULTI_REGION_RE = re.compile(r"\bmulti_region\s*=\s*true", re.IGNORECASE)

# Encryption
_TF_ENCRYPTED_RE = re.compile(r"\b(?:encrypted|storage_encrypted)\s*=\s*true", re.IGNORECASE)
_TF_DISK_ENC_RE = re.compile(r"\benable_disk_encryption\s*=\s*true", re.IGNORECASE)
_TF_CMEK_RE = re.compile(
    r'\b(?:kms_key_id|disk_encryption_set_id|crypto_key_id|kms_key_name)\s*=\s*"([^"]+)"'
)

# Secret stores
_TF_SECRET_STORES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'resource\s+"aws_secretsmanager_secret"'), "aws_secrets_manager"),
    (re.compile(r'resource\s+"aws_ssm_parameter"'), "aws_secrets_manager"),
    (re.compile(r'resource\s+"azurerm_key_vault"'), "azure_key_vault"),
    (re.compile(r'resource\s+"google_secret_manager_secret"'), "gcp_secret_manager"),
    (
        re.compile(r'resource\s+"vault_(?:generic_secret|kv_secret|aws_auth_backend)"'),
        "hashicorp_vault",
    ),
]

# IAM resource blocks
_TF_IAM_RESOURCE_RE = re.compile(
    r'resource\s+"'
    r"(aws_iam_role|aws_iam_policy|aws_iam_user|aws_iam_group|aws_iam_instance_profile"
    r"|google_service_account|google_project_iam_binding|google_project_iam_member"
    r"|google_service_account_iam_binding|google_service_account_iam_member"
    r"|azurerm_user_assigned_identity|azurerm_role_assignment|azurerm_service_principal"
    r"|kubernetes_service_account|kubernetes_role|kubernetes_cluster_role"
    r"|kubernetes_role_binding|kubernetes_cluster_role_binding"
    r')"\s+"([^"]+)"',
    re.IGNORECASE,
)

_TF_BLOCK_CONTENT_RE = re.compile(
    r'resource\s+"[^"]+"\s+"[^"]+"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', re.DOTALL
)
_TF_PRINCIPAL_FROM_ARN_RE = re.compile(r'"principal"\s*:\s*\{[^}]*"AWS"\s*:\s*"([^"]+)"', re.DOTALL)
_TF_MEMBERS_RE = re.compile(r"members\s*=\s*\[([^\]]+)\]")
_TF_ROLE_RE = re.compile(r'\brole\s*=\s*"([^"]+)"')
_TF_PRINCIPAL_ID_RE = re.compile(r'\bprincipal_id\s*=\s*"([^"\$][^"]*)"')
_TF_ACCOUNT_ID_RE = re.compile(r'\baccount_id\s*=\s*"([^"]+)"')
_TF_ACTION_RE = re.compile(r'"Action"\s*:\s*(?:"([^"]+)"|\[([^\]]+)\])')
_TF_INLINE_PERMISSION_RE = re.compile(r'"([a-zA-Z0-9_*]+:[a-zA-Z0-9_*]+)"')


def _tf_provider_to_target(provider: str) -> str:
    mapping = {
        "aws": "aws",
        "azurerm": "azure",
        "google": "gcp",
        "vault": "hashicorp_vault",
        "kubernetes": "kubernetes",
    }
    return mapping.get(provider.lower(), provider.lower())


def _tf_iam_type(resource_type: str) -> str:
    rt = resource_type.lower()
    if "role_assignment" in rt or "iam_binding" in rt or "iam_member" in rt or "role_binding" in rt:
        return "role_binding"
    if "policy" in rt:
        return "policy"
    if "service_account" in rt or "user" in rt or "service_principal" in rt:
        return "service_account"
    if "identity" in rt:
        return "managed_identity"
    if "instance_profile" in rt:
        return "role"
    return "role"


class TerraformAdapter:
    """Terraform ``.tf`` / ``.tfvars`` adapter.

    One DEPLOYMENT node per file (capturing provider, region, AZs, encryption,
    secret stores) plus one IAM node per ``aws_iam_*``, ``google_*``, or
    ``azurerm_*`` identity resource block.
    """

    name = "terraform"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        results.extend(self._deployment(content, file_path))
        results.extend(self._iam(content, file_path))
        return results

    def _deployment(self, content: str, file_path: str) -> list[ComponentDetection]:
        providers = _uniq([m.group(1).lower() for m in _TF_PROVIDER_RE.finditer(content)])
        if not providers:
            # If no provider block, don't emit a deployment node unless there
            # are interesting resource blocks (encryption, AZs, etc.)
            has_resources = bool(
                _TF_ENCRYPTED_RE.search(content)
                or any(p.search(content) for p, _ in _TF_SECRET_STORES)
            )
            if not has_resources:
                return []

        deployment_target = providers[0] if providers else "unknown"
        target_display = _tf_provider_to_target(deployment_target)

        # Region
        region_match = _TF_REGION_RE.search(content) or _TF_LOCATION_RE.search(content)
        cloud_region = region_match.group(1) if region_match else None

        # AZs
        availability_zones: list[str] = []
        az_list = _TF_AZ_LIST_RE.search(content)
        if az_list:
            availability_zones = [
                z.strip().strip('"').strip("'") for z in az_list.group(1).split(",") if z.strip()
            ]
        else:
            availability_zones = [m.group(1) for m in _TF_AZ_SINGLE_RE.finditer(content)]

        # HA mode
        ha_mode: str | None = None
        if _TF_MULTI_AZ_RE.search(content) or len(availability_zones) > 1:
            ha_mode = "multi-az"
        elif _TF_MULTI_REGION_RE.search(content):
            ha_mode = "multi-az"

        # Encryption
        encryption_at_rest = bool(
            _TF_ENCRYPTED_RE.search(content) or _TF_DISK_ENC_RE.search(content)
        )
        enc_key_match = _TF_CMEK_RE.search(content)
        encryption_key_ref = enc_key_match.group(1) if enc_key_match else None

        # Secret stores
        secret_store: str | None = None
        for pattern, store_name in _TF_SECRET_STORES:
            if pattern.search(content):
                secret_store = store_name
                break

        file_label = file_path.replace("/", "_").replace("\\", "_")
        canonical = f"deployment:terraform:{target_display}:{file_label}".lower()[:128]

        meta: dict[str, Any] = {
            "iac_format": "terraform",
            "deployment_target": target_display,
            "providers": providers,
            "cloud_region": cloud_region,
            "availability_zones": availability_zones or None,
            "ha_mode": ha_mode,
            "encryption_at_rest": encryption_at_rest or None,
            "encryption_key_ref": encryption_key_ref,
            "secret_store": secret_store,
        }
        display = f"terraform:{target_display}:{cloud_region or 'unknown-region'}"
        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=display,
                adapter_name=self.name,
                confidence=0.88,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"terraform provider={deployment_target} region={cloud_region}",
            )
        ]

    def _iam(self, content: str, file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        for m in _TF_IAM_RESOURCE_RE.finditer(content):
            resource_type = m.group(1)
            resource_name = m.group(2)
            iam_type = _tf_iam_type(resource_type)
            line = content[: m.start()].count("\n") + 1

            # Try to extract principal / role / permissions from the block
            permissions: list[str] | None = None
            trust_principals: list[str] | None = None
            principal: str | None = resource_name

            # Actions from inline policy JSON
            action_match = _TF_ACTION_RE.search(content[m.start() : m.start() + 2000])
            if action_match:
                raw = action_match.group(1) or action_match.group(2) or ""
                permissions = _cap20([a.strip().strip('"') for a in raw.split(",") if a.strip()])

            # Trust policy principals (AWS)
            tp_match = _TF_PRINCIPAL_FROM_ARN_RE.search(content[m.start() : m.start() + 2000])
            if tp_match:
                trust_principals = [tp_match.group(1)]

            # GCP members list
            members_match = _TF_MEMBERS_RE.search(content[m.start() : m.start() + 1000])
            if members_match:
                trust_principals = [
                    v.strip().strip('"').strip("'")
                    for v in members_match.group(1).split(",")
                    if v.strip()
                ]
                if not permissions:
                    role_m = _TF_ROLE_RE.search(content[m.start() : m.start() + 1000])
                    permissions = [role_m.group(1)] if role_m else None

            # Azure principal_id
            pid_match = _TF_PRINCIPAL_ID_RE.search(content[m.start() : m.start() + 1000])
            if pid_match:
                principal = pid_match.group(1)

            # Accountid for service accounts
            acct_match = _TF_ACCOUNT_ID_RE.search(content[m.start() : m.start() + 1000])
            if acct_match:
                principal = acct_match.group(1)

            canonical = f"iam:terraform:{resource_type}:{resource_name}".lower()[:128]
            meta: dict[str, Any] = {
                "iac_format": "terraform",
                "iam_type": iam_type,
                "tf_resource_type": resource_type,
                "principal": principal,
                "permissions": permissions,
                "trust_principals": trust_principals,
            }
            results.append(
                _make_det(
                    component_type=ComponentType.IAM,
                    canonical_name=canonical,
                    display_name=resource_name,
                    adapter_name=self.name,
                    confidence=0.90,
                    metadata=meta,
                    file_path=file_path,
                    line=line,
                    snippet=f'{resource_type} "{resource_name}"',
                )
            )
        return results


# ---------------------------------------------------------------------------
# CloudFormation adapter
# ---------------------------------------------------------------------------

_CFN_IAM_TYPES = frozenset(
    {
        "AWS::IAM::Role",
        "AWS::IAM::ManagedPolicy",
        "AWS::IAM::Policy",
        "AWS::IAM::InstanceProfile",
        "AWS::IAM::User",
        "AWS::IAM::Group",
        "AWS::IAM::AccessKey",
    }
)

_CFN_SECRET_RESOURCES = frozenset(
    {
        "AWS::SecretsManager::Secret",
        "AWS::SSM::Parameter",
        "AWS::SSM::SecretString",
    }
)

_CFN_ENCRYPTION_KEYS: list[str] = [
    "StorageEncrypted",
    "Encrypted",
    "EnableEncryption",
    "EnableAtRestEncryption",
    "EncryptionAtRest",
]

_CFN_KMS_KEYS: list[str] = [
    "KmsKeyId",
    "KMSKeyId",
    "MasterKeyArn",
    "KmsKeyArn",
]

_CFN_MULTI_AZ_KEYS: list[str] = ["MultiAZ", "MultiAz"]


def _is_cfn(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "AWSTemplateFormatVersion" in data or "SAMTemplateVersion" in data:
        return True
    resources = data.get("Resources") or data.get("resources")
    if isinstance(resources, dict):
        return any(str((v or {}).get("Type", "")).startswith("AWS::") for v in resources.values())
    return False


def _cfn_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in {"true", "yes", "1"}
    return False


def _cfn_extract_actions(policy_doc: Any) -> list[str]:
    """Extract Action strings from a CFN policy document dict."""
    actions: list[str] = []
    if not isinstance(policy_doc, dict):
        return actions
    for stmt in policy_doc.get("Statement") or []:
        if not isinstance(stmt, dict):
            continue
        action = stmt.get("Action") or []
        if isinstance(action, str):
            actions.append(action)
        elif isinstance(action, list):
            actions.extend(str(a) for a in action)
    return _cap20(actions)


class CloudFormationAdapter:
    """AWS CloudFormation adapter (YAML or JSON).

    Triggered when the parsed document contains ``AWSTemplateFormatVersion``
    or a ``Resources`` mapping with at least one ``AWS::`` type.

    Emits one ``DEPLOYMENT`` node per template file and one ``IAM`` node per
    ``AWS::IAM::*`` resource.
    """

    name = "cloudformation"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        # Try YAML first, then JSON (CFN supports both)
        data: Any = None
        if content.lstrip().startswith("{"):
            data = _try_load_json(content)
        if data is None:
            data = _try_load_yaml(content)
        if not _is_cfn(data):
            return []

        results: list[ComponentDetection] = []
        results.extend(self._deployment(data, content, file_path))
        results.extend(self._iam(data, file_path))
        return results

    def _deployment(
        self, data: dict[str, Any], content: str, file_path: str
    ) -> list[ComponentDetection]:
        resources: dict[str, Any] = data.get("Resources") or {}

        # Encryption
        encryption_at_rest = False
        encryption_key_ref: str | None = None
        secret_store: str | None = None
        multi_az = False
        availability_zones: list[str] = []

        for _res_name, res in (resources or {}).items():
            if not isinstance(res, dict):
                continue
            res_type = str(res.get("Type") or "")
            props = res.get("Properties") or {}

            # Secret stores
            if res_type in _CFN_SECRET_RESOURCES:
                secret_store = "aws_secrets_manager"

            # Encryption flags
            for enc_key in _CFN_ENCRYPTION_KEYS:
                if _cfn_bool(props.get(enc_key)):
                    encryption_at_rest = True

            # KMS key references
            for kms_key in _CFN_KMS_KEYS:
                val = props.get(kms_key)
                if val and isinstance(val, str) and not val.startswith("!"):
                    encryption_key_ref = val

            # Multi-AZ
            for maz_key in _CFN_MULTI_AZ_KEYS:
                if _cfn_bool(props.get(maz_key)):
                    multi_az = True

            # Availability zones
            az_prop = props.get("AvailabilityZones") or props.get("AvailabilityZone")
            if isinstance(az_prop, list):
                availability_zones.extend(str(z) for z in az_prop)
            elif isinstance(az_prop, str):
                availability_zones.append(az_prop)

        ha_mode: str | None = "multi-az" if (multi_az or len(availability_zones) > 1) else None

        # Parameters — look for region-like default values
        params = data.get("Parameters") or {}
        cloud_region: str | None = None
        for _p, pval in (params if isinstance(params, dict) else {}).items():
            default = (pval or {}).get("Default", "")
            if re.match(r"[a-z]{2}-[a-z]+-\d", str(default)):
                cloud_region = str(default)
                break

        # Also check Metadata or Mappings for region hints
        if not cloud_region:
            region_m = re.search(r"\b(us|eu|ap|sa|ca|af|me)[-_][a-z0-9-]+\b", content[:4000])
            if region_m:
                cloud_region = region_m.group(0)

        file_label = file_path.replace("/", "_").replace("\\", "_")
        canonical = f"deployment:cloudformation:{file_label}".lower()[:128]
        meta: dict[str, Any] = {
            "iac_format": "cloudformation",
            "deployment_target": "aws",
            "cloud_region": cloud_region,
            "availability_zones": availability_zones or None,
            "ha_mode": ha_mode,
            "encryption_at_rest": encryption_at_rest or None,
            "encryption_key_ref": encryption_key_ref,
            "secret_store": secret_store,
        }
        template_ver = data.get("AWSTemplateFormatVersion") or "CloudFormation"
        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=f"cloudformation:{cloud_region or 'aws'}",
                adapter_name=self.name,
                confidence=0.90,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"AWSTemplateFormatVersion: {template_ver}",
            )
        ]

    def _iam(self, data: dict[str, Any], file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        resources: dict[str, Any] = data.get("Resources") or {}
        for res_name, res in (resources if isinstance(resources, dict) else {}).items():
            if not isinstance(res, dict):
                continue
            res_type = str(res.get("Type") or "")
            if res_type not in _CFN_IAM_TYPES:
                continue

            props = res.get("Properties") or {}
            iam_type = _cfn_iam_type_from(res_type)

            # Trust policy
            trust_principals: list[str] | None = None
            assume_doc = props.get("AssumeRolePolicyDocument") or {}
            trust_stmts = (assume_doc if isinstance(assume_doc, dict) else {}).get(
                "Statement"
            ) or []
            raw_trusts: list[str] = []
            for stmt in trust_stmts:
                principal = (stmt or {}).get("Principal")
                if isinstance(principal, str):
                    raw_trusts.append(principal)
                elif isinstance(principal, dict):
                    for v in principal.values():
                        if isinstance(v, str):
                            raw_trusts.append(v)
                        elif isinstance(v, list):
                            raw_trusts.extend(str(x) for x in v)
            trust_principals = raw_trusts[:10] or None

            # Permissions
            permissions: list[str] | None = None
            for doc_key in ("PolicyDocument", "ManagedPolicyDocument", "PolicyDeclaration"):
                pdoc = props.get(doc_key)
                if pdoc:
                    permissions = _cfn_extract_actions(pdoc) or None
                    break

            # Inline policies nested list
            if not permissions:
                for inline in props.get("Policies") or []:
                    if isinstance(inline, dict):
                        actions = _cfn_extract_actions(inline.get("PolicyDocument") or {})
                        if actions:
                            permissions = actions
                            break

            principal = (
                props.get("RoleName") or props.get("UserName") or props.get("GroupName") or res_name
            )

            canonical = f"iam:cfn:{res_type}:{res_name}".lower()[:128]
            meta: dict[str, Any] = {
                "iac_format": "cloudformation",
                "iam_type": iam_type,
                "cfn_resource_type": res_type,
                "principal": principal,
                "permissions": _cap20(permissions) if permissions else None,
                "trust_principals": trust_principals,
            }
            results.append(
                _make_det(
                    component_type=ComponentType.IAM,
                    canonical_name=canonical,
                    display_name=principal or res_name,
                    adapter_name=self.name,
                    confidence=0.90,
                    metadata=meta,
                    file_path=file_path,
                    line=1,
                    snippet=f"{res_type} {res_name}",
                )
            )
        return results


def _cfn_iam_type_from(res_type: str) -> str:
    if "Role" in res_type or "InstanceProfile" in res_type:
        return "role"
    if "Policy" in res_type or "ManagedPolicy" in res_type:
        return "policy"
    if "User" in res_type or "Group" in res_type or "AccessKey" in res_type:
        return "service_account"
    return "role"


# ---------------------------------------------------------------------------
# Bicep adapter
# ---------------------------------------------------------------------------

_BICEP_RESOURCE_RE = re.compile(
    r"resource\s+(\w+)\s+'([^']+)'\s*=\s*\{",
    re.MULTILINE,
)
_BICEP_PARAM_LOCATION_RE = re.compile(
    r"param\s+location\s+string\s*=\s*'([^']+)'",
    re.IGNORECASE,
)
_BICEP_VAR_LOCATION_RE = re.compile(
    r"var\s+location\s*=\s*'([^']+)'",
    re.IGNORECASE,
)
_BICEP_ZONES_RE = re.compile(r"zones\s*:\s*\[([^\]]+)\]")
_BICEP_ZONE_REDUNDANT_RE = re.compile(r"zoneRedundant\s*:\s*true", re.IGNORECASE)
_BICEP_ENCRYPTION_ENABLED_RE = re.compile(
    r"(?:enabled|status)\s*:\s*(?:true|'Enabled'|\"Enabled\")",
    re.IGNORECASE,
)
_BICEP_ENCRYPTION_BLOCK_RE = re.compile(r"encryption\s*:\s*\{[^}]+\}", re.DOTALL)
_BICEP_KEY_URL_RE = re.compile(r"keyUrl\s*:\s*'([^']+)'")
_BICEP_DISK_ENC_RE = re.compile(r"diskEncryptionSetId\s*:\s*'([^']+)'")


_BICEP_SECRET_TYPES: list[tuple[str, str]] = [
    ("Microsoft.KeyVault/vaults", "azure_key_vault"),
    ("Microsoft.KeyVault/secrets", "azure_key_vault"),
]
_BICEP_IAM_TYPES: dict[str, tuple[str, str]] = {
    # resource_type_fragment: (iam_type, description)
    "microsoft.managedidentity/userassignedidentities": (
        "managed_identity",
        "User-Assigned Managed Identity",
    ),
    "microsoft.authorization/roleassignments": ("role_binding", "Role Assignment"),
    "microsoft.authorization/policyassignments": ("policy", "Policy Assignment"),
    "microsoft.authorization/roledefinitions": ("role", "Role Definition"),
    "microsoft.authorization/policydefinitions": ("policy", "Policy Definition"),
}

_BICEP_PROPS_RE = re.compile(r"properties\s*:\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}", re.DOTALL)
_BICEP_PRINCIPAL_ID_RE = re.compile(r"principalId\s*:\s*([^\n]+)")
_BICEP_ROLE_DEF_RE = re.compile(r"roleDefinitionId\s*:\s*([^\n]+)")
_BICEP_HA_REPLICAS_RE = re.compile(r"replicasPerMaster\s*:\s*(\d+)")


class BicepAdapter:
    """Azure Bicep (``.bicep``) adapter.

    One DEPLOYMENT node per file plus IAM nodes for identity / role resources.
    """

    name = "bicep"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        results.extend(self._deployment(content, file_path))
        results.extend(self._iam(content, file_path))
        return results

    def _deployment(self, content: str, file_path: str) -> list[ComponentDetection]:
        # Cloud region
        loc_m = _BICEP_PARAM_LOCATION_RE.search(content) or _BICEP_VAR_LOCATION_RE.search(content)
        cloud_region = loc_m.group(1) if loc_m else None
        if not cloud_region:
            # resourceGroup().location is dynamic; treat as unknown
            cloud_region = None

        # Availability zones
        availability_zones: list[str] = []
        for zm in _BICEP_ZONES_RE.finditer(content):
            availability_zones.extend(
                z.strip().strip("'\"") for z in zm.group(1).split(",") if z.strip()
            )

        ha_mode: str | None = None
        if _BICEP_ZONE_REDUNDANT_RE.search(content) or len(availability_zones) > 1:
            ha_mode = "multi-az"
        rep_m = _BICEP_HA_REPLICAS_RE.search(content)
        if rep_m and int(rep_m.group(1)) > 0:
            ha_mode = ha_mode or "replicated"

        # Secret stores
        secret_store: str | None = None
        for res_m in _BICEP_RESOURCE_RE.finditer(content):
            rtype = res_m.group(2).split("@")[0].lower()
            for frag, store in _BICEP_SECRET_TYPES:
                if frag.lower() == rtype:
                    secret_store = store
                    break

        # Encryption
        encryption_at_rest: bool | None = None
        enc_block_m = _BICEP_ENCRYPTION_BLOCK_RE.search(content)
        if enc_block_m:
            if _BICEP_ENCRYPTION_ENABLED_RE.search(enc_block_m.group(0)):
                encryption_at_rest = True

        enc_key_ref: str | None = None
        ku_m = _BICEP_KEY_URL_RE.search(content)
        de_m = _BICEP_DISK_ENC_RE.search(content)
        if ku_m:
            enc_key_ref = ku_m.group(1)
            encryption_at_rest = True
        if de_m:
            enc_key_ref = enc_key_ref or de_m.group(1)
            encryption_at_rest = True

        has_any = bool(
            cloud_region
            or availability_zones
            or secret_store
            or encryption_at_rest
            or _BICEP_RESOURCE_RE.search(content)
        )
        if not has_any:
            return []

        file_label = file_path.replace("/", "_").replace("\\", "_")
        canonical = f"deployment:bicep:azure:{file_label}".lower()[:128]
        meta: dict[str, Any] = {
            "iac_format": "bicep",
            "deployment_target": "azure",
            "cloud_region": cloud_region,
            "availability_zones": availability_zones or None,
            "ha_mode": ha_mode,
            "encryption_at_rest": encryption_at_rest,
            "encryption_key_ref": enc_key_ref,
            "secret_store": secret_store,
        }
        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=f"bicep:azure:{cloud_region or 'unknown-region'}",
                adapter_name=self.name,
                confidence=0.88,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"bicep deployment region={cloud_region}",
            )
        ]

    def _iam(self, content: str, file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        for m in _BICEP_RESOURCE_RE.finditer(content):
            sym = m.group(1)
            rtype_full = m.group(2)
            rtype_base = rtype_full.split("@")[0].lower()

            iam_entry = _BICEP_IAM_TYPES.get(rtype_base)
            if not iam_entry:
                continue
            iam_type, description = iam_entry
            line = content[: m.start()].count("\n") + 1

            # Props block — scan content after opening brace
            rest = content[m.end() :]
            principal: str | None = sym
            permissions: list[str] | None = None

            pid_m = _BICEP_PRINCIPAL_ID_RE.search(rest[:1000])
            if pid_m:
                principal = pid_m.group(1).strip().strip("'\"")

            role_m = _BICEP_ROLE_DEF_RE.search(rest[:1000])
            permissions = [role_m.group(1).strip().strip("'\"")] if role_m else None

            canonical = f"iam:bicep:{rtype_base}:{sym}".lower()[:128]
            meta: dict[str, Any] = {
                "iac_format": "bicep",
                "iam_type": iam_type,
                "bicep_resource_type": rtype_full,
                "principal": principal,
                "permissions": permissions,
            }
            results.append(
                _make_det(
                    component_type=ComponentType.IAM,
                    canonical_name=canonical,
                    display_name=sym,
                    adapter_name=self.name,
                    confidence=0.88,
                    metadata=meta,
                    file_path=file_path,
                    line=line,
                    snippet=f"resource {sym} '{rtype_full}'",
                )
            )
        return results


# ---------------------------------------------------------------------------
# GCP Deployment Manager adapter
# ---------------------------------------------------------------------------

_GCP_TYPE_RE = re.compile(
    r"(?:gcp-types/|compute\.v\d\.?\w*|container\.v\d\.?\w*|iam\.v\d\.?\w*"
    r"|cloudresourcemanager|secretmanager|cloudkms|storage\.v\d)",
    re.IGNORECASE,
)

_GCP_IAM_RESOURCE_TYPES = frozenset(
    {
        "iam.v1.serviceaccounts",
        "iam.v1.serviceaccountkey",
        "gcp-types/iam-v1:iam.serviceaccounts",
    }
)

_GCP_IAM_BINDING_FRAGMENTS = (
    "cloudresourcemanager",
    "iammemberBinding",
    "iamBinding",
    "iamMemberBinding",
    "iamPolicy",
)


def _is_gcp_dm(data: Any, content: str) -> bool:
    if not isinstance(data, dict):
        return False
    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        return False
    return any(
        _GCP_TYPE_RE.search(str((r or {}).get("type", "")))
        for r in resources
        if isinstance(r, dict)
    )


class GcpDeploymentManagerAdapter:
    """GCP Deployment Manager adapter.

    Handles YAML ``resources:`` lists with ``gcp-types/`` or ``*.v1.*`` type
    prefixes, and Jinja2 (``.jinja``) DM templates.
    """

    name = "gcp_deployment_manager"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        # Jinja files: strip template directives before YAML parse
        parse_content = content
        if file_path.lower().endswith(".jinja"):
            parse_content = re.sub(r"\{%.*?%\}", "", content, flags=re.DOTALL)
            parse_content = re.sub(r"\{\{.*?\}\}", "placeholder", parse_content)

        data = _try_load_yaml(parse_content)
        if not _is_gcp_dm(data, content):
            return []

        results: list[ComponentDetection] = []
        results.extend(self._deployment(data, content, file_path))
        results.extend(self._iam(data, file_path))
        return results

    def _deployment(
        self, data: dict[str, Any], content: str, file_path: str
    ) -> list[ComponentDetection]:
        resources: list[Any] = data.get("resources") or []
        regions: list[str] = []
        zones: list[str] = []
        secret_store: str | None = None
        encryption_at_rest: bool | None = None
        ha_mode: str | None = None

        for res in resources:
            if not isinstance(res, dict):
                continue
            props = res.get("properties") or {}
            rtype = str(res.get("type", "")).lower()

            # Region / zone
            region = props.get("region") or props.get("location")
            if region and isinstance(region, str):
                regions.append(region)
            zone = props.get("zone")
            if zone and isinstance(zone, str):
                zones.append(zone)
            zone_list = props.get("zones") or props.get("locations")
            if isinstance(zone_list, list):
                zones.extend(str(z) for z in zone_list)

            # Secret store
            if "secretmanager" in rtype:
                secret_store = "gcp_secret_manager"

            # Encryption (Cloud KMS)
            if "cloudkms" in rtype:
                encryption_at_rest = True

            # HA via multi-zone or autoscaling
            if "autoscaling" in rtype or props.get("autoscaler"):
                ha_mode = "replicated"
            if len(zones) > 1 or props.get("nodeCount", 1) > 1:
                ha_mode = "multi-az"

        cloud_region = regions[0] if regions else None
        availability_zones = _uniq(zones) if zones else None
        if availability_zones and len(availability_zones) > 1 and not ha_mode:
            ha_mode = "multi-az"

        file_label = file_path.replace("/", "_").replace("\\", "_")
        canonical = f"deployment:gcpdm:gcp:{file_label}".lower()[:128]
        meta: dict[str, Any] = {
            "iac_format": "gcp_deployment_manager",
            "deployment_target": "gcp",
            "cloud_region": cloud_region,
            "availability_zones": availability_zones,
            "ha_mode": ha_mode,
            "encryption_at_rest": encryption_at_rest,
            "secret_store": secret_store,
        }
        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=f"gcp-dm:{cloud_region or 'gcp'}",
                adapter_name=self.name,
                confidence=0.87,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"GCP Deployment Manager region={cloud_region}",
            )
        ]

    def _iam(self, data: dict[str, Any], file_path: str) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        resources: list[Any] = data.get("resources") or []
        for res in resources:
            if not isinstance(res, dict):
                continue
            rtype = str(res.get("type", "")).lower()
            name = str(res.get("name", "unknown"))
            props = res.get("properties") or {}

            is_sa = rtype in _GCP_IAM_RESOURCE_TYPES
            is_binding = any(frag.lower() in rtype for frag in _GCP_IAM_BINDING_FRAGMENTS)

            if not is_sa and not is_binding:
                continue

            iam_type = "service_account" if is_sa else "role_binding"
            principal = (
                props.get("accountId")
                or props.get("serviceAccountId")
                or props.get("member")
                or name
            )
            permissions: list[str] | None = None
            trust_principals: list[str] | None = None

            if is_binding:
                bindings = props.get("bindings") or []
                for binding in bindings if isinstance(bindings, list) else []:
                    role = binding.get("role")
                    members = binding.get("members") or []
                    if role:
                        permissions = _cap20((permissions or []) + [str(role)])
                    trust_principals = _cap20((trust_principals or []) + [str(m) for m in members])

                # Also handle single-binding format
                if not permissions:
                    role = props.get("role")
                    member = props.get("member")
                    permissions = [str(role)] if role else None
                    trust_principals = [str(member)] if member else None

            canonical = f"iam:gcpdm:{rtype}:{name}".lower()[:128]
            meta: dict[str, Any] = {
                "iac_format": "gcp_deployment_manager",
                "iam_type": iam_type,
                "gcp_resource_type": res.get("type"),
                "principal": principal,
                "permissions": permissions,
                "trust_principals": trust_principals,
            }
            results.append(
                _make_det(
                    component_type=ComponentType.IAM,
                    canonical_name=canonical,
                    display_name=str(principal or name),
                    adapter_name=self.name,
                    confidence=0.87,
                    metadata=meta,
                    file_path=file_path,
                    line=1,
                    snippet=f"GCP DM {res.get('type', '')} {name}",
                )
            )
        return results


# ---------------------------------------------------------------------------
# GitHub Actions adapter
# ---------------------------------------------------------------------------

# Action refs that imply cloud OIDC / identity federation
_GHA_OIDC_ACTIONS: list[tuple[re.Pattern[str], str, str]] = [
    # (pattern, provider, iam_type_hint)
    (re.compile(r"aws-actions/configure-aws-credentials"), "aws", "role"),
    (re.compile(r"google-github-actions/auth"), "gcp", "service_account"),
    (re.compile(r"azure/login"), "azure", "managed_identity"),
    (re.compile(r"hashicorp/vault-action"), "hashicorp_vault", "role"),
]

# Cloud login actions that also reveal region/zone info in `with:` inputs
_GHA_AWS_REGION_RE = re.compile(r"aws-region['\"]?\s*:\s*['\"]?([a-z0-9-]+)", re.IGNORECASE)
_GHA_GCP_SA_RE = re.compile(
    r"(?:workload_identity_provider|service_account)['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# Secrets reference pattern — any ${{ secrets.XYZ }}
_GHA_SECRET_REF_RE = re.compile(r"\$\{\{\s*secrets\.[A-Za-z0-9_]+\s*\}\}")

# environment: reference inside a job
_GHA_ENV_NAME_RE = re.compile(r"^\s+environment\s*:", re.MULTILINE)


def _gha_safe_on(data: dict[str, Any]) -> Any:
    """Return the 'on' trigger value, handling both ``on`` and ``'on'`` keys."""
    return data.get("on") or data.get(True) or data.get("'on'") or data.get('"on"')  # type: ignore[call-overload]


def _gha_permissions(perm_obj: Any) -> list[str]:
    """Flatten a GHA permissions dict into 'scope:access' strings."""
    if not isinstance(perm_obj, dict):
        return []
    result: list[str] = []
    for scope, access in perm_obj.items():
        result.append(f"{scope}:{access}")
    return result


def _gha_runners(jobs: dict[str, Any]) -> list[str]:
    """Collect unique runner labels from all jobs."""
    runners: list[str] = []
    seen: set[str] = set()
    for job in (jobs or {}).values():
        if not isinstance(job, dict):
            continue
        runs_on = job.get("runs-on")
        if isinstance(runs_on, str) and runs_on not in seen:
            seen.add(runs_on)
            runners.append(runs_on)
        elif isinstance(runs_on, list):
            for r in runs_on:
                r_str = str(r)
                if r_str not in seen:
                    seen.add(r_str)
                    runners.append(r_str)
    return runners


def _gha_environments(jobs: dict[str, Any]) -> list[str]:
    """Collect unique environment names referenced across all jobs."""
    envs: list[str] = []
    seen: set[str] = set()
    for job in (jobs or {}).values():
        if not isinstance(job, dict):
            continue
        env = job.get("environment")
        if isinstance(env, str) and env not in seen:
            seen.add(env)
            envs.append(env)
        elif isinstance(env, dict):
            name = env.get("name")
            if isinstance(name, str) and name not in seen:
                seen.add(name)
                envs.append(name)
    return envs


def _gha_triggers(on_val: Any) -> list[str]:
    """Return a sorted list of trigger names."""
    if isinstance(on_val, str):
        return [on_val]
    if isinstance(on_val, list):
        return sorted(str(v) for v in on_val)
    if isinstance(on_val, dict):
        return sorted(str(k) for k in on_val.keys())
    return []


class GitHubActionsAdapter:
    """GitHub Actions workflow adapter.

    Triggered on YAML files that contain both ``jobs:`` and ``on:`` keys
    (the canonical GitHub Actions structure).  Handles files under
    ``.github/workflows/`` and any root-level workflow YAML.

    Emits:
    - ``DEPLOYMENT`` node per workflow capturing triggers, runners,
      environments, permissions, secret-store usage, cloud providers,
      OIDC usage, and HA mode (matrix strategy).
    - ``IAM`` nodes for each cloud OIDC trust (AWS role, GCP service
      account, Azure managed identity) found in workflow steps.
    """

    name = "github_actions"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        # Quick content guard: must look like a GHA workflow
        if "jobs:" not in content:
            return []
        # Must have an 'on:' trigger (literal or YAML boolean key 'true')
        if not re.search(r"(?m)^on\s*:", content) and "on:" not in content:
            return []

        data = _try_load_yaml(content)
        if not isinstance(data, dict):
            return []
        jobs = data.get("jobs")
        if not isinstance(jobs, dict):
            return []
        on_val = _gha_safe_on(data)
        if on_val is None:
            return []

        results: list[ComponentDetection] = []
        results.extend(self._deployment(data, content, file_path))
        results.extend(self._iam_nodes(data, content, file_path))
        return results

    # ------------------------------------------------------------------
    # Workflow → DEPLOYMENT
    # ------------------------------------------------------------------

    def _deployment(
        self, data: dict[str, Any], content: str, file_path: str
    ) -> list[ComponentDetection]:
        import os

        jobs: dict[str, Any] = data.get("jobs") or {}
        on_val = _gha_safe_on(data)
        triggers = _gha_triggers(on_val)
        runners = _gha_runners(jobs)
        environments = _gha_environments(jobs)

        # Top-level and job-level permissions
        top_perms = _gha_permissions(data.get("permissions"))
        uses_oidc = any("id-token" in p for p in top_perms)
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            job_perms = _gha_permissions(job.get("permissions"))
            uses_oidc = uses_oidc or any("id-token" in p for p in job_perms)

        # Aggregate all step actions to detect cloud providers
        cloud_providers: list[str] = []
        cloud_region: str | None = None
        seen_providers: set[str] = set()
        all_steps: list[dict[str, Any]] = []
        for job in jobs.values():
            if isinstance(job, dict):
                all_steps.extend(s for s in (job.get("steps") or []) if isinstance(s, dict))

        for step in all_steps:
            uses = str(step.get("uses") or "")
            step_with = step.get("with") or {}
            for pat, provider, _ in _GHA_OIDC_ACTIONS:
                if pat.search(uses) and provider not in seen_providers:
                    seen_providers.add(provider)
                    cloud_providers.append(provider)
                    # Extract region for AWS
                    if provider == "aws" and isinstance(step_with, dict):
                        r = step_with.get("aws-region") or _GHA_AWS_REGION_RE.search(str(step_with))
                        if isinstance(r, str):
                            cloud_region = r
                        elif r:
                            cloud_region = r.group(1)

        # Secret store — GitHub Actions secrets or Vault
        secret_store: str | None = None
        if _GHA_SECRET_REF_RE.search(content):
            secret_store = "github_actions_secret"
        if "hashicorp_vault" in cloud_providers:
            secret_store = "hashicorp_vault"

        # HA mode — matrix strategy = multi-target
        ha_mode: str | None = None
        for job in jobs.values():
            if isinstance(job, dict) and job.get("strategy", {}).get("matrix"):
                ha_mode = "replicated"
                break

        # Workflow name — prefer explicit `name:` field or derive from filename
        workflow_name = str(data.get("name") or os.path.basename(file_path))
        canonical = f"deployment:github-actions:{workflow_name}".lower().replace(" ", "-")[:128]

        meta: dict[str, Any] = {
            "iac_format": "github_actions",
            "deployment_target": "github-actions",
            "workflow_triggers": triggers,
            "runners": runners,
            "environments": environments,
            "cloud_providers": cloud_providers,
            "cloud_region": cloud_region,
            "uses_oidc": uses_oidc,
            "secret_store": secret_store,
            "ha_mode": ha_mode,
            # These are not applicable for GHA workflows
            "runs_as_root": None,
            "has_health_check": None,
            "has_resource_limits": None,
        }
        if top_perms:
            meta["permissions"] = _cap20(top_perms)

        return [
            _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=workflow_name,
                adapter_name=self.name,
                confidence=0.95,
                metadata=meta,
                file_path=file_path,
                line=1,
                snippet=f"GitHub Actions: {workflow_name} triggers={triggers[:3]}",
            )
        ]

    # ------------------------------------------------------------------
    # Cloud OIDC trusts → IAM
    # ------------------------------------------------------------------

    def _iam_nodes(
        self, data: dict[str, Any], content: str, file_path: str
    ) -> list[ComponentDetection]:
        jobs: dict[str, Any] = data.get("jobs") or {}
        results: list[ComponentDetection] = []
        seen_principals: set[str] = set()

        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                uses = str(step.get("uses") or "")
                step_with = step.get("with") or {}

                for pat, provider, iam_type in _GHA_OIDC_ACTIONS:
                    if not pat.search(uses):
                        continue

                    # Extract principal identifier from step inputs
                    principal: str | None = None
                    iam_scope: str | None = None
                    permissions: list[str] | None = None
                    trust_principals: list[str] = ["github-actions"]

                    if provider == "aws":
                        if isinstance(step_with, dict):
                            principal = step_with.get("role-to-assume") or step_with.get("role_arn")
                        iam_scope = "account"

                    elif provider == "gcp":
                        if isinstance(step_with, dict):
                            m = _GHA_GCP_SA_RE.search(str(step_with))
                            principal = m.group(1) if m else step_with.get("service_account")
                        iam_scope = "project"

                    elif provider == "azure":
                        if isinstance(step_with, dict):
                            principal = step_with.get("client-id") or step_with.get("creds")
                        iam_scope = "subscription"

                    elif provider == "hashicorp_vault":
                        if isinstance(step_with, dict):
                            principal = step_with.get("role") or "vault-role"
                        iam_scope = "namespace"

                    if not principal:
                        principal = f"{provider}-identity-{job_id}"

                    if principal in seen_principals:
                        continue
                    seen_principals.add(principal)

                    # Collect job-level permission grants
                    job_perms = _gha_permissions(job.get("permissions"))
                    if job_perms:
                        permissions = _cap20(job_perms)

                    canonical = f"iam:gha:{provider}:{principal}".lower()[:128]
                    meta: dict[str, Any] = {
                        "iac_format": "github_actions",
                        "iam_type": iam_type,
                        "principal": principal,
                        "iam_scope": iam_scope,
                        "permissions": permissions,
                        "trust_principals": trust_principals,
                        "cloud_provider": provider,
                        "step_action": uses.split("@")[0],
                    }
                    results.append(
                        _make_det(
                            component_type=ComponentType.IAM,
                            canonical_name=canonical,
                            display_name=str(principal),
                            adapter_name=self.name,
                            confidence=0.93,
                            metadata=meta,
                            file_path=file_path,
                            line=1,
                            snippet=f"GHA OIDC {provider}: {principal}",
                        )
                    )
        return results
