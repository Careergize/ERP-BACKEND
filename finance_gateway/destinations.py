"""Independent destination policy. No HTTP transport is enabled by this module.

Future transport MUST connect only to the returned IPs while verifying TLS for
the approved hostname; resolving again in requests would reopen DNS rebinding.
"""
import ipaddress
import socket
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ValidationError


def validate_destination(base_url, native_site_name):
    try:
        parsed = urlsplit(base_url)
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or any(c.isspace() for c in base_url) or '\\' in base_url
                or any(c.isspace() for c in native_site_name)):
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        policy = settings.FINANCE_GATEWAY_DESTINATIONS.get(base_url)
        if not policy or policy.get('site_name') != native_site_name:
            raise ValueError()
        local = policy.get('local_development') is True
        if parsed.scheme != 'https' and not (local and settings.DEBUG and parsed.scheme == 'http'):
            raise ValueError()
        if local and not settings.DEBUG:
            raise ValueError()
        approved = {ipaddress.ip_address(v) for v in policy['addresses']}
        if not approved:
            raise ValueError()
        # Private addresses require exact operator approval AND DEBUG. Link-local,
        # multicast and unspecified addresses are never valid destinations.
        for address in approved:
            if (address.is_link_local or address.is_multicast or address.is_unspecified
                    or (not address.is_global and not local)):
                raise ValueError()
        resolved = {ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM)}
        if not resolved or not resolved <= approved:
            raise ValueError()
        return tuple(sorted(str(v) for v in resolved))
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise ValidationError('Finance destination is not approved.') from None
