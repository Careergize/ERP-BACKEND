"""Fixed, authenticated GET only. Never returns or logs upstream bodies/errors."""
import json
from urllib.parse import urlsplit
import requests
from django.conf import settings


def connection_status():
    base = settings.FINANCE_FRAPPE_URL.strip()
    identity = settings.FINANCE_FRAPPE_IDENTITY.strip()
    key = settings.FINANCE_FRAPPE_API_KEY
    secret = settings.FINANCE_FRAPPE_API_SECRET
    try:
        parsed = urlsplit(base)
        parsed.port  # Validate malformed/out-of-range ports before any request.
    except ValueError:
        return 'not_configured'
    local_http = (settings.DEBUG and parsed.scheme == 'http'
                  and parsed.hostname in {'finance.localhost', 'localhost', '127.0.0.1'})
    if (not base or not identity or not key or not secret
            or identity.casefold() in {'administrator', 'guest'}
            or (parsed.scheme != 'https' and not local_http)
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
            or any(c in key + secret for c in '\r\n')):
        return 'not_configured'
    try:
        # Do not inherit proxy credentials or .netrc authentication from the host.
        with requests.Session() as session:
            session.trust_env = False
            with session.get(
                base.rstrip('/') + '/api/method/frappe.auth.get_logged_user',
                headers={'Authorization': f'token {key}:{secret}', 'Accept': 'application/json'},
                timeout=(3, 5), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code in {401, 403}:
                    return 'permission_denied'
                if response.status_code != 200:
                    return 'unavailable'
                content = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    content.extend(chunk)
                    if len(content) > 16384:
                        return 'invalid_response'
                data = json.loads(content)
                if not isinstance(data, dict) or data.get('message') != identity:
                    return 'invalid_response'
                # An integration identity response is not per-user authorization.
                return 'authenticated'
    except requests.Timeout:
        return 'timeout'
    except (requests.RequestException, ValueError, UnicodeError):
        return 'unavailable'
