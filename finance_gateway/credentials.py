"""Server-side resolver boundary. No provider or credential provisioning exists."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from django.utils import timezone


class CredentialsUnavailable(Exception):
    pass


@dataclass(frozen=True)
class NativeCredentials:
    native_username: str
    site_id: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    expires_at: datetime
    revoked: bool = False


class CredentialResolver(Protocol):
    def resolve(self, reference: str) -> NativeCredentials: ...


class UnavailableResolver:
    def resolve(self, reference):
        raise CredentialsUnavailable()


def get_resolver():
    # A real secret-store adapter needs a separate reviewed implementation.
    return UnavailableResolver()


def resolve_credentials(binding):
    try:
        if not binding.credential_reference:
            raise CredentialsUnavailable()
        result = get_resolver().resolve(binding.credential_reference)
        if (not isinstance(result, NativeCredentials) or result.revoked
                or result.expires_at <= timezone.now()
                or result.native_username != binding.native_username
                or result.site_id != str(binding.site_id)
                or not result.api_key or not result.api_secret
                or any(c.isspace() for c in result.api_key + result.api_secret)):
            raise CredentialsUnavailable()
        return result
    except Exception:
        # Provider exceptions may contain secret references or token values.
        raise CredentialsUnavailable() from None
