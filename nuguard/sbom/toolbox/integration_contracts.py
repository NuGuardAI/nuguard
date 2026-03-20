from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator
import re


class XrayConfig(BaseModel):
    url: HttpUrl
    project: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=8)
    tenant_id: str = Field(min_length=1, max_length=128)
    application_id: str = Field(min_length=1, max_length=128)
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    retries: int = Field(default=2, ge=0, le=5)

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        if not set(value).issubset(allowed):
            raise ValueError("project contains unsupported characters")
        return value


class AwsSecurityHubConfig(BaseModel):
    region: str = Field(min_length=1, max_length=32)
    aws_account_id: str = Field(pattern=r"^\d{12}$")
    product_arn_suffix: str = Field(default="xelo-toolbox", min_length=1, max_length=64)
    profile: str | None = None
    timeout: float = Field(default=15.0, ge=1.0, le=60.0)
    retries: int = Field(default=2, ge=0, le=5)

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if not re.match(r"^[a-z]{2}-[a-z]+-\d+$|^us-gov-[a-z]+-\d+$", value):
            raise ValueError(f"'{value}' does not look like a valid AWS region (e.g. us-east-1)")
        return value


class GhasConfig(BaseModel):
    token: str = Field(min_length=10)
    github_repo: str
    ref: str = Field(min_length=1)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    github_api_url: HttpUrl = Field(default="https://api.github.com")  # type: ignore[assignment]
    timeout: float = Field(default=15.0, ge=1.0, le=60.0)
    retries: int = Field(default=2, ge=0, le=5)

    @field_validator("github_repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if not re.match(r"^[^/]+/[^/]+$", value):
            raise ValueError(
                f"'{value}' is not a valid repository slug — expected 'owner/repo'"
            )
        return value

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        if not value.startswith("refs/"):
            raise ValueError(
                f"'{value}' must start with 'refs/' (e.g. refs/heads/main)"
            )
        return value
