"""Build :class:`~agentium.governance.access_control.IdentityProvider` from settings."""

from __future__ import annotations

from typing import Optional

from agentium.app.settings import AppSettings
from agentium.governance.access_control import (
    IdentityProvider,
    InsecureJWTDecoder,
    JWKSJWTDecoder,
    MultiIssuerOIDCIdentityProvider,
    OIDCIdentityProvider,
    OidcIssuerConfig,
)


def build_identity_provider(settings: AppSettings) -> Optional[IdentityProvider]:
    """Return OIDC provider(s) when ``AGENTIUM_OIDC_ISSUERS_JSON`` is set."""

    configs = settings.oidc_issuer_configs
    if not configs:
        return None
    providers: list[OIDCIdentityProvider] = []
    for cfg in configs:
        decoder = _decoder_for_issuer(cfg, settings.profile)
        providers.append(
            OIDCIdentityProvider(
                decoder,
                cfg.issuer,
                cfg.audience,
                tenant_claim=cfg.tenant_claim,
                roles_claim=cfg.roles_claim,
                subject_claim=cfg.subject_claim,
            )
        )
    if len(providers) == 1:
        return providers[0]
    return MultiIssuerOIDCIdentityProvider(providers)


def _decoder_for_issuer(cfg: OidcIssuerConfig, profile: str) -> InsecureJWTDecoder | JWKSJWTDecoder:
    if cfg.jwks_url:
        return JWKSJWTDecoder(cfg.jwks_url)
    if profile == "prod":
        raise ValueError(
            "OIDC jwks_url is required for each issuer when AGENTIUM_PROFILE=prod "
            f"(missing for issuer {cfg.issuer!r})"
        )
    return InsecureJWTDecoder()


__all__ = ["build_identity_provider"]
