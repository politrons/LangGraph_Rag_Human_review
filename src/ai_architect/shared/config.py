from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StateBackend(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class Settings(BaseSettings):
    """Runtime configuration. Production integrations are deliberately explicit."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    oidc_jwks_url: AnyHttpUrl | None = None
    oidc_issuer: AnyHttpUrl | None = None
    oidc_audience: str | None = None
    state_backend: StateBackend = StateBackend.MEMORY
    postgres_connection_uri: SecretStr | None = None

    audit_database_url: str = "sqlite:///data/audit.sqlite3"
    audit_hmac_key: SecretStr

    ollama_base_url: AnyHttpUrl = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_decision_model: str = "llama3.1:8b"
    ollama_scoring_model: str | None = None
    rag_index_path: Path = Path("data/index/policy_chunks.jsonl")
    rag_service_url: AnyHttpUrl = "http://127.0.0.1:8081"

    case_routing_url: AnyHttpUrl | None = None
    case_routing_api_key: SecretStr | None = None
    request_timeout_seconds: float = 20.0

    @field_validator("audit_database_url")
    @classmethod
    def validate_audit_database_url(cls, value: str) -> str:
        if value.startswith("sqlite:///") or value.startswith("postgresql://"):
            return value
        raise ValueError("AUDIT_DATABASE_URL must use sqlite:/// or postgresql://")

    def ensure_state_backend_is_configured(self) -> None:
        if self.state_backend is StateBackend.POSTGRES and self.postgres_connection_uri is None:
            raise ValueError("POSTGRES_CONNECTION_URI is required when STATE_BACKEND=postgres")

    def ensure_identity_provider_is_configured(self) -> None:
        if self.app_env.lower() in {"production", "staging"}:
            if not (self.oidc_jwks_url and self.oidc_issuer and self.oidc_audience):
                raise ValueError("OIDC_JWKS_URL, OIDC_ISSUER and OIDC_AUDIENCE are required")
            return
