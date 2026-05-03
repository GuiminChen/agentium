"""Plugin-oriented identity and access control primitives."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, Field
from typing_extensions import Protocol

from agentium.models.context import RequestContext
from agentium.shared.errors import ConfigurationError

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


class Principal(BaseModel):
    """Authenticated principal used by authorization plugins."""

    subject: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    roles: Set[str] = Field(default_factory=set)
    attributes: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class AccessDecision(BaseModel):
    """Authorization decision returned by access control plugins."""

    allowed: bool
    reason: str = Field(min_length=1)
    policy_id: Optional[str] = None

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class IdentityProvider(Protocol):
    """Identity provider protocol for SSO/IAM integrations."""

    def authenticate(self, token: str) -> Optional[Principal]:
        """Resolve principal from access token."""


class AuthorizationPlugin(Protocol):
    """Authorization plugin protocol for RBAC/ABAC engines."""

    def authorize(
        self,
        principal: Principal,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Evaluate whether principal can execute action on resource."""


class TokenDecoder(Protocol):
    """Protocol for OIDC token decoders."""

    def decode(self, token: str) -> Dict[str, Any]:
        """Decode token and return claims payload."""


class StaticTokenIdentityProvider:
    """Simple token map identity provider for local and tests."""

    def __init__(self, token_to_principal: Dict[str, Principal]) -> None:
        self._token_to_principal = token_to_principal

    def authenticate(self, token: str) -> Optional[Principal]:
        """Resolve principal from static token mapping."""

        principal = self._token_to_principal.get(token)
        if principal is None:
            return None
        return principal.copy(deep=True)


class ABACRule(BaseModel):
    """ABAC rule with wildcard action/resource and attribute constraints."""

    id: str = Field(min_length=1)
    effect: str = Field(pattern="^(allow|deny)$")
    action_patterns: List[str] = Field(default_factory=list)
    resource_patterns: List[str] = Field(default_factory=list)
    required_roles: Set[str] = Field(default_factory=set)
    subject_conditions: Dict[str, Any] = Field(default_factory=dict)
    context_conditions: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="ABAC rule matched", min_length=1)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"

    def matches(
        self,
        principal: Principal,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> bool:
        """Return True when current rule matches request tuple."""

        if self.action_patterns and not _matches_any(action, self.action_patterns):
            return False
        if self.resource_patterns and not _matches_any(resource, self.resource_patterns):
            return False
        if self.required_roles and not self.required_roles.issubset(principal.roles):
            return False
        if not _conditions_match(self.subject_conditions, principal.attributes):
            return False
        if not _conditions_match(self.context_conditions, context):
            return False
        return True


class ABACPolicyDocument(BaseModel):
    """File-based ABAC policy document."""

    version: str = Field(default="v1", min_length=1)
    default_allow: bool = False
    default_reason: str = Field(default="Denied by default ABAC policy", min_length=1)
    rules: List[ABACRule] = Field(default_factory=list)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class ABACAuthorizer:
    """Rule-driven ABAC authorizer plugin."""

    def __init__(
        self,
        rules: Sequence[ABACRule],
        default_allow: bool = False,
        default_reason: str = "Denied by default ABAC policy",
        version: str = "unknown",
    ) -> None:
        self._rules = list(rules)
        self._default_allow = default_allow
        self._default_reason = default_reason
        self._version = version

    @property
    def version(self) -> str:
        """Return loaded policy version string."""

        return self._version

    @classmethod
    def from_document(cls, document: ABACPolicyDocument) -> ABACAuthorizer:
        """Build ABAC authorizer from typed policy document."""

        return cls(
            rules=document.rules,
            default_allow=document.default_allow,
            default_reason=document.default_reason,
            version=document.version,
        )

    @classmethod
    def from_file(cls, policy_path: Path) -> ABACAuthorizer:
        """Build ABAC authorizer from JSON/YAML file."""

        raw = _load_policy_raw(policy_path)
        document = ABACPolicyDocument.parse_obj(raw)
        return cls.from_document(document)

    def authorize(
        self,
        principal: Principal,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Evaluate ABAC rules in declaration order."""

        for rule in self._rules:
            if not rule.matches(
                principal=principal, action=action, resource=resource, context=context
            ):
                continue
            return AccessDecision(
                allowed=rule.effect == "allow",
                reason=rule.reason,
                policy_id=rule.id,
            )
        return AccessDecision(
            allowed=self._default_allow,
            reason=self._default_reason,
            policy_id=None,
        )


class ReloadingABACAuthorizer(AuthorizationPlugin):
    """ABAC authorizer with file-based hot reload on change."""

    def __init__(
        self,
        policy_path: Path,
        on_policy_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._policy_path = policy_path
        self._last_mtime_ns: Optional[int] = None
        self._delegate: Optional[ABACAuthorizer] = None
        self._pending_events: List[Dict[str, Any]] = []
        self._on_policy_event = on_policy_event
        self._ensure_loaded()

    @property
    def policy_path(self) -> Path:
        """Return bound policy file path."""

        return self._policy_path

    @property
    def version(self) -> str:
        """Return current active policy version."""

        if self._delegate is None:
            return "unknown"
        return self._delegate.version

    def authorize(
        self,
        principal: Principal,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Authorize request with automatic reload on policy change."""

        self._ensure_loaded()
        if self._delegate is None:
            return AccessDecision(
                allowed=False,
                reason="ABAC policy is not loaded",
                policy_id=None,
            )
        return self._delegate.authorize(
            principal=principal,
            action=action,
            resource=resource,
            context=context,
        )

    def _ensure_loaded(self) -> None:
        if not self._policy_path.exists():
            raise ConfigurationError(f"ABAC policy file does not exist: {self._policy_path}")
        current_mtime_ns = self._policy_path.stat().st_mtime_ns
        if self._delegate is not None and self._last_mtime_ns == current_mtime_ns:
            return
        previous_delegate = self._delegate
        try:
            reloaded = ABACAuthorizer.from_file(self._policy_path)
        except Exception as exc:
            if previous_delegate is None:
                raise
            self._emit_event(
                {
                    "event_type": "abac_policy_reload_failed",
                    "policy_path": str(self._policy_path),
                    "active_version": previous_delegate.version,
                    "rollback_applied": True,
                    "error": str(exc),
                }
            )
            return

        self._delegate = reloaded
        self._last_mtime_ns = current_mtime_ns
        self._emit_event(
            {
                "event_type": "abac_policy_reloaded",
                "policy_path": str(self._policy_path),
                "active_version": reloaded.version,
                "rollback_applied": False,
            }
        )

    def pop_events(self) -> List[Dict[str, Any]]:
        """Pop buffered policy reload events."""

        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def _emit_event(self, event: Dict[str, Any]) -> None:
        self._pending_events.append(event)
        if self._on_policy_event is not None:
            self._on_policy_event(event)


class InsecureJWTDecoder:
    """Base JWT decoder without signature verification for local development.

    Do not use this decoder in production.
    """

    def decode(self, token: str) -> Dict[str, Any]:
        """Decode JWT payload segment without signature verification."""

        parts = token.split(".")
        if len(parts) < 2:
            raise ConfigurationError("JWT token format is invalid")
        payload = _urlsafe_b64decode(parts[1])
        try:
            raw_claims = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed token
            raise ConfigurationError("JWT payload is not valid JSON") from exc
        if not isinstance(raw_claims, dict):
            raise ConfigurationError("JWT payload must be a JSON object")
        return raw_claims


@dataclass(frozen=True)
class OidcIssuerConfig:
    """One OIDC issuer audience pair for multi-tenant IdP routing."""

    issuer: str
    audience: str
    jwks_url: Optional[str] = None
    tenant_claim: str = "tenant_id"
    roles_claim: str = "roles"
    subject_claim: str = "sub"


class MultiIssuerOIDCIdentityProvider:
    """Try each configured :class:`OIDCIdentityProvider` until one accepts the token."""

    def __init__(self, providers: Sequence[OIDCIdentityProvider]) -> None:
        if not providers:
            raise ConfigurationError("MultiIssuerOIDCIdentityProvider requires at least one issuer")
        self._providers = list(providers)

    def authenticate(self, token: str) -> Optional[Principal]:
        for provider in self._providers:
            principal = provider.authenticate(token)
            if principal is not None:
                return principal
        return None


class OIDCIdentityProvider:
    """OIDC identity provider with pluggable token decoder."""

    def __init__(
        self,
        decoder: TokenDecoder,
        issuer: str,
        audience: str,
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        subject_claim: str = "sub",
    ) -> None:
        self._decoder = decoder
        self._issuer = issuer
        self._audience = audience
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._subject_claim = subject_claim

    def authenticate(self, token: str) -> Optional[Principal]:
        """Authenticate and map OIDC claims to Principal."""

        try:
            claims = self._decoder.decode(token)
        except Exception:
            return None
        if claims.get("iss") != self._issuer:
            return None
        if not _audience_matches(claims.get("aud"), self._audience):
            return None
        subject = claims.get(self._subject_claim)
        tenant_id = claims.get(self._tenant_claim)
        if not isinstance(subject, str) or not subject:
            return None
        if not isinstance(tenant_id, str) or not tenant_id:
            return None

        raw_roles = claims.get(self._roles_claim, [])
        if isinstance(raw_roles, str):
            roles = {raw_roles}
        elif isinstance(raw_roles, list):
            roles = {item for item in raw_roles if isinstance(item, str)}
        else:
            roles = set()

        attributes = {
            key: value
            for key, value in claims.items()
            if key not in {"iss", "aud", self._subject_claim, self._tenant_claim}
        }
        return Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=roles,
            attributes=attributes,
        )


class JWKSJWTDecoder:
    """Production JWT decoder backed by remote JWKS key set."""

    def __init__(
        self,
        jwks_url: str,
        algorithms: Optional[List[str]] = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._algorithms = algorithms or ["RS256"]
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - dependency gate
            raise ConfigurationError(
                "PyJWT is required for JWKS decoding. Install dependency `PyJWT`."
            ) from exc
        self._jwt_module = jwt
        self._jwk_client = jwt.PyJWKClient(jwks_url)

    def decode(self, token: str) -> Dict[str, Any]:
        """Decode JWT using JWKS signing keys with signature verification."""

        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        decoded = self._jwt_module.decode(
            token,
            signing_key.key,
            algorithms=self._algorithms,
            options={"verify_signature": True},
        )
        if not isinstance(decoded, dict):
            raise ConfigurationError("Decoded JWT claims must be a JSON object")
        return decoded


class IAMAccessController:
    """Composable IAM access controller for context and token flows."""

    def __init__(
        self,
        authorization_plugin: AuthorizationPlugin,
        identity_provider: Optional[IdentityProvider] = None,
    ) -> None:
        self._authorization_plugin = authorization_plugin
        self._identity_provider = identity_provider

    def authorize_token(
        self,
        token: str,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Authorize request by authenticating token then evaluating policy."""

        if self._identity_provider is None:
            return AccessDecision(
                allowed=False,
                reason="Identity provider is not configured",
                policy_id=None,
            )
        principal = self._identity_provider.authenticate(token)
        if principal is None:
            return AccessDecision(
                allowed=False,
                reason="Token is invalid or expired",
                policy_id=None,
            )
        return self._authorization_plugin.authorize(
            principal=principal,
            action=action,
            resource=resource,
            context=context,
        )

    def authorize_context(
        self,
        request_context: RequestContext,
        action: str,
        resource: str,
        context: Dict[str, Any],
    ) -> AccessDecision:
        """Authorize using runtime request context and ABAC plugin."""

        principal = Principal(
            subject=request_context.user_id,
            tenant_id=request_context.tenant_id,
            roles={request_context.role},
            attributes={
                "tenant_id": request_context.tenant_id,
                "deployment_mode": request_context.deployment_mode,
            },
        )
        return self._authorization_plugin.authorize(
            principal=principal,
            action=action,
            resource=resource,
            context=context,
        )

    def collect_policy_events(self) -> List[Dict[str, Any]]:
        """Collect optional policy reload events from authorization plugin."""

        pop_events = getattr(self._authorization_plugin, "pop_events", None)
        if callable(pop_events):
            events = pop_events()
            if isinstance(events, list):
                return [event for event in events if isinstance(event, dict)]
        return []


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch(value, pattern) for pattern in patterns)


def _conditions_match(expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, list):
            if actual_value not in expected_value:
                return False
            continue
        if actual_value != expected_value:
            return False
    return True


def _load_policy_raw(policy_path: Path) -> Dict[str, Any]:
    if not policy_path.exists():
        raise ConfigurationError(f"ABAC policy file does not exist: {policy_path}")
    suffix = policy_path.suffix.lower()
    text = policy_path.read_text(encoding="utf-8")
    if suffix == ".json":
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ConfigurationError("ABAC JSON policy root must be a mapping")
        return raw
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise ConfigurationError("PyYAML is required for YAML ABAC policies")
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigurationError("ABAC YAML policy root must be a mapping")
        return loaded
    raise ConfigurationError("Unsupported ABAC policy file extension. Use JSON or YAML")


def _urlsafe_b64decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("utf-8"))


def _audience_matches(raw_audience: Any, expected_audience: str) -> bool:
    if isinstance(raw_audience, str):
        return raw_audience == expected_audience
    if isinstance(raw_audience, list):
        return expected_audience in raw_audience
    return False

