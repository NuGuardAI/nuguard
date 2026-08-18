"""Unit tests for the auth-detection RegexAdapters in nuguard.sbom.adapters.registry.

Covers the distinction between the ordinary "reads a secret from env" finding
(auth_runtime) and the higher-severity "hardcoded fallback secret" finding
(auth_runtime_insecure_default) — see the registry.py Tier 1 / Tier 1b comments.
"""
from __future__ import annotations

from nuguard.sbom.adapters.base import RegexAdapter
from nuguard.sbom.adapters.registry import default_registry


def _adapter(name: str) -> RegexAdapter:
    for a in default_registry():
        if isinstance(a, RegexAdapter) and a.name == name:
            return a
    raise AssertionError(f"no RegexAdapter named {name!r} in default_registry()")


class TestInsecureDefaultSecretDetection:
    def test_two_arg_literal_default_flagged_insecure(self) -> None:
        adapter = _adapter("auth_runtime_insecure_default")
        code = 'SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")\n'
        detection = adapter.detect(code)
        assert detection is not None
        assert detection.metadata["insecure_default"] is True
        assert detection.canonical_name == "auth:generic:insecure_default"

    def test_one_arg_no_default_not_flagged_insecure(self) -> None:
        insecure_adapter = _adapter("auth_runtime_insecure_default")
        runtime_adapter = _adapter("auth_runtime")
        code = 'SECRET_KEY = os.getenv("SECRET_KEY")\n'
        assert insecure_adapter.detect(code) is None
        detection = runtime_adapter.detect(code)
        assert detection is not None
        assert "insecure_default" not in detection.metadata

    def test_two_arg_none_default_not_flagged_insecure(self) -> None:
        """A None default is not a hardcoded secret — must fall through to the
        tier-1 (no-default) pattern's severity, not the insecure-default one."""
        adapter = _adapter("auth_runtime_insecure_default")
        code = 'SECRET_KEY = os.getenv("SECRET_KEY", None)\n'
        assert adapter.detect(code) is None

    def test_two_arg_variable_default_not_flagged_insecure(self) -> None:
        """A default sourced from another variable/call isn't a literal
        hardcoded secret — must not be flagged as insecure_default."""
        adapter = _adapter("auth_runtime_insecure_default")
        code = "SECRET_KEY = os.getenv('SECRET_KEY', fallback_secret)\n"
        assert adapter.detect(code) is None


class TestCloudSecretsManagerDetection:
    def test_aws_secrets_manager_detected(self) -> None:
        adapter = _adapter("auth_aws_secrets_manager")
        code = (
            "client = boto3.client('secretsmanager')\n"
            "resp = client.get_secret_value(SecretId='prod/openai-key')\n"
        )
        detection = adapter.detect(code)
        assert detection is not None
        assert detection.metadata["auth_type"] == "cloud_secrets_manager"
        assert detection.metadata["provider"] == "aws"

    def test_azure_key_vault_detected(self) -> None:
        adapter = _adapter("auth_azure_key_vault")
        code = (
            "client = SecretClient(vault_url='https://vault.vault.azure.net/', "
            "credential=DefaultAzureCredential())\n"
            "secret = client.get_secret('openai-key')\n"
        )
        detection = adapter.detect(code)
        assert detection is not None
        assert detection.metadata["provider"] == "azure"

    def test_gcp_secret_manager_detected(self) -> None:
        adapter = _adapter("auth_gcp_secret_manager")
        code = (
            "client = secretmanager.SecretManagerServiceClient()\n"
            "resp = client.access_secret_version(name=name)\n"
        )
        detection = adapter.detect(code)
        assert detection is not None
        assert detection.metadata["provider"] == "gcp"

    def test_unrelated_boto3_client_not_flagged_as_secrets_manager(self) -> None:
        adapter = _adapter("auth_aws_secrets_manager")
        code = "client = boto3.client('s3')\n"
        assert adapter.detect(code) is None
