from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from ..shared.models import CaseSubmission, HumanReview, SanitizedSubmission


class AuthenticationError(PermissionError):
    """Authentication or tenant/role authorization failed."""


@dataclass(frozen=True)
class Principal:
    actor_id: str
    tenant_id: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "ai_admin" in self.roles


class IdentityVerifier(Protocol):
    def verify(self, request: Request) -> Principal: ...


class DevelopmentIdentityVerifier:
    """Local-only header resolver; staging and production must use verified OIDC tokens."""

    def verify(self, request: Request) -> Principal:
        actor_id = request.headers.get("X-Development-Actor-ID", "").strip()
        tenant_id = request.headers.get("X-Development-Tenant-ID", "").strip()
        roles = frozenset(
            role.strip().lower()
            for role in request.headers.get("X-Development-Roles", "").split(",")
            if role.strip()
        )
        if not actor_id or not tenant_id or not roles:
            raise AuthenticationError("Development identity headers are required")
        return Principal(actor_id=actor_id, tenant_id=tenant_id, roles=roles)


class OidcJwtVerifier:
    def __init__(self, *, jwks_url: str, issuer: str, audience: str) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience

    def verify(self, request: Request) -> Principal:
        import jwt

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise AuthenticationError("Bearer token is required")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            signing_key = jwt.PyJWKClient(self._jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except Exception as exc:
            raise AuthenticationError("OIDC token verification failed") from exc
        return _principal_from_claims(claims)


def _principal_from_claims(claims: Mapping[str, object]) -> Principal:
    actor_id = str(claims.get("sub", "")).strip()
    tenant_id = str(claims.get("tenant_id", "")).strip()
    raw_roles = claims.get("roles", [])
    roles = (
        frozenset(str(value).strip().lower() for value in raw_roles if str(value).strip())
        if isinstance(raw_roles, list)
        else frozenset()
    )
    if not actor_id or not tenant_id or not roles:
        raise AuthenticationError("Token misses required sub, tenant_id or roles claims")
    return Principal(actor_id=actor_id, tenant_id=tenant_id, roles=roles)


def authorize_submission(principal: Principal, submission: CaseSubmission) -> None:
    if principal.is_admin:
        return
    if principal.actor_id != submission.actor_id or principal.tenant_id != submission.tenant_id:
        raise AuthenticationError("Actor or tenant does not match authenticated identity")
    if set(submission.actor_roles) != principal.roles:
        raise AuthenticationError("Submitted roles do not match authenticated identity")


def authorize_sanitized_submission(principal: Principal, submission: SanitizedSubmission) -> None:
    if principal.is_admin:
        return
    if principal.tenant_id != submission.tenant_id:
        raise AuthenticationError("Decision thread does not belong to authenticated tenant")


def authorize_review(principal: Principal, review: HumanReview) -> None:
    if not principal.is_admin and principal.actor_id != review.reviewer_id:
        raise AuthenticationError("Reviewer ID does not match authenticated identity")
    if review.outcome.value == "approve" and not principal.roles.intersection(
        {"underwriter", "claims_adjuster", "ai_admin"}
    ):
        raise AuthenticationError("The authenticated role cannot approve a decision")
