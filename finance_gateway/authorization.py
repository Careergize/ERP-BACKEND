"""Fresh explicit grants only; no role/staff/superuser shortcuts or grant cache."""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from accounts.applications import finance_eligible
from accounts.models import FinanceEntitlement
from .credentials import resolve_credentials, CredentialsUnavailable
from .destinations import validate_destination
from .models import CAPABILITIES, FinanceCompanyGrant, FinanceIdentityBinding


class GatewayDenied(Exception):
    def __init__(self, code='access_denied', status=403):
        self.code, self.status = code, status


def current():
    return Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())


def require_user(user):
    if not user.is_authenticated:
        raise GatewayDenied('authentication_required', 401)
    if not finance_eligible(user):
        raise GatewayDenied()
    if not settings.FINANCE_GATEWAY_ACCESS_ENABLED:
        raise GatewayDenied('integration_disabled', 503)


def effective_companies(user):
    require_user(user)
    entitlement = FinanceEntitlement.objects.get(user_id=user.pk)
    result = []
    grants = FinanceCompanyGrant.objects.filter(current(), user_id=user.pk, enabled=True,
        company__enabled=True, company__site__enabled=True).select_related('company__site')
    for grant in grants:
        capabilities = sorted(grant.capabilities.filter(current(), enabled=True,
            capability__in=CAPABILITIES).values_list('capability', flat=True))
        if 'finance.access.read' not in capabilities:
            continue
        company = grant.company
        binding = FinanceIdentityBinding.objects.filter(current(), user_id=user.pk,
            site_id=company.site_id, enabled=True).first()
        if not binding or binding.native_username != entitlement.frappe_user.strip():
            continue
        if binding.native_username.strip().casefold() in {'', 'administrator', 'guest'}:
            continue
        try:
            validate_destination(company.site.base_url, company.site.native_site_name)
        except Exception:
            raise GatewayDenied('destination_denied', 503) from None
        try:
            resolve_credentials(binding)
        except CredentialsUnavailable:
            raise GatewayDenied('credentials_unavailable', 503) from None
        result.append({'reference': str(company.reference), 'display_name': company.display_name,
                       'capabilities': capabilities})
    return sorted(result, key=lambda c: c['reference'])


def require_company_capability(user, reference, capability):
    """Boundary for future fixed GET handlers; never trust discovery DTOs."""
    require_user(user)
    if capability not in CAPABILITIES or capability == 'finance.access.read':
        raise GatewayDenied()
    if not settings.FINANCE_GATEWAY_READS_ENABLED:
        raise GatewayDenied('integration_disabled', 503)
    for company in effective_companies(user):
        if company['reference'] == str(reference) and capability in company['capabilities']:
            return company
    # Inaccessible and unknown references have identical outcomes.
    raise GatewayDenied()
