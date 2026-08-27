"""Shared utilities used across all nuguard capabilities."""

from nuguard.common.auth import AuthConfig, AuthSession, LoginFlowConfig
from nuguard.common.auth_runtime import (
    ResolvedAuthRuntime,
    bootstrap_auth_runtime,
    resolve_auth_runtime,
)
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.discovery import DiscoveredProfile, TargetDiscoveryResult
from nuguard.common.errors import (
    ConfigError,
    ExtractorError,
    NuGuardError,
    SbomError,
    ScanError,
    ValidationError,
)
from nuguard.common.logging import get_logger
from nuguard.common.secret_store import EncryptedBlob, SecretCipher, generate_secret_key
from nuguard.common.stream_runtime import StreamRunHandle
from nuguard.common.streaming_models import (
    STREAM_SCHEMA_VERSION,
    BehaviorProgressState,
    RedteamProgressState,
    StreamDeltaPayload,
    StreamEvent,
    StreamProgressPayload,
    StreamTerminalPayload,
    apply_event_to_behavior_state,
    apply_event_to_redteam_state,
)
from nuguard.common.target_verify_public_api import (
    EndpointSource,
    TargetSessionResolveRequest,
    TargetSessionResolveResult,
    TargetVerifyCheck,
    TargetVerifyRequest,
    TargetVerifyResult,
    TargetVerifyStatus,
    resolve_target_session_public,
    verify_target,
)

__all__ = [
    "DiscoveredProfile",
    "TargetDiscoveryResult",
    "NuGuardError",
    "SbomError",
    "ValidationError",
    "ScanError",
    "ConfigError",
    "ExtractorError",
    "get_logger",
    "AuthConfig",
    "AuthSession",
    "LoginFlowConfig",
    "AuthBootstrapper",
    "ResolvedAuthRuntime",
    "resolve_auth_runtime",
    "bootstrap_auth_runtime",
    "SecretCipher",
    "EncryptedBlob",
    "generate_secret_key",
    "StreamRunHandle",
    "STREAM_SCHEMA_VERSION",
    "StreamEvent",
    "StreamProgressPayload",
    "StreamDeltaPayload",
    "StreamTerminalPayload",
    "RedteamProgressState",
    "BehaviorProgressState",
    "apply_event_to_redteam_state",
    "apply_event_to_behavior_state",
    "TargetVerifyStatus",
    "EndpointSource",
    "TargetVerifyRequest",
    "TargetVerifyCheck",
    "TargetVerifyResult",
    "TargetSessionResolveRequest",
    "TargetSessionResolveResult",
    "verify_target",
    "resolve_target_session_public",
]
