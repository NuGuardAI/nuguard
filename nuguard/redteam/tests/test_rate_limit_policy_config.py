"""Rate-limit policy configuration and public model (issue #766). Offline only."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nuguard.config import NuGuardConfig, RedteamRateLimitPolicy
from nuguard.redteam.public_api import RedteamRunRequest


def test_policy_is_unset_by_default() -> None:
    assert NuGuardConfig().resolved_redteam_rate_limit_policy() is None


def test_policy_resolves_when_both_keys_are_set() -> None:
    cfg = NuGuardConfig(redteam_rate_limit_max_requests=3, redteam_rate_limit_window_seconds=10)
    policy = cfg.resolved_redteam_rate_limit_policy()
    assert policy == RedteamRateLimitPolicy(max_requests=3, window_seconds=10.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"redteam_rate_limit_max_requests": 3},
        {"redteam_rate_limit_window_seconds": 10},
    ],
)
def test_half_set_policy_is_rejected(kwargs) -> None:
    with pytest.raises(ValidationError):
        NuGuardConfig(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_requests": 0, "window_seconds": 10},
        {"max_requests": 3, "window_seconds": 0},
        {"max_requests": 3, "window_seconds": -5},
    ],
)
def test_invalid_policy_values_are_rejected(kwargs) -> None:
    with pytest.raises(ValidationError):
        RedteamRateLimitPolicy(**kwargs)


def test_policy_round_trips_through_json() -> None:
    policy = RedteamRateLimitPolicy(max_requests=3, window_seconds=10.0)
    dumped = policy.model_dump(mode="json")
    assert dumped == {"max_requests": 3, "window_seconds": 10.0}
    assert RedteamRateLimitPolicy.model_validate(dumped) == policy


def test_public_request_exposes_optional_policy_defaulting_to_none() -> None:
    field = RedteamRunRequest.model_fields["rate_limit_policy"]
    assert field.default is None
