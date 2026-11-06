"""Fail-closed identity prerequisites. There is deliberately no SSO transport.

No Frappe release/site has been verified. Do not add a ready flag without a real
per-user adapter that rechecks native user status and permissions on every session.
"""
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import FinanceEntitlement


def finance_eligible(user):
    if not user.is_authenticated or not user.is_active:
        return False
    # Query on every request: disabling or expiring a grant must not depend on
    # cached login claims or the lifetime of the operations access token.
    grant = FinanceEntitlement.objects.filter(user_id=user.pk, enabled=True).first()
    return bool(grant and grant.frappe_user.strip()
                and grant.frappe_user.strip().casefold() not in {'administrator', 'guest'}
                and (grant.expires_at is None or grant.expires_at > timezone.now()))


def private_response(data, status=200):
    response = Response(data, status=status)
    response['Cache-Control'] = 'no-store'
    response['Vary'] = 'Authorization, Cookie'
    return response


class ApplicationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return private_response({
            # Informational existing-role visibility, not a new operational ACL.
            'operations': {'available': bool(request.user.is_active and request.user.effective_role)},
            'finance': {
                'entitled': finance_eligible(request.user),
                'available': False,
                'reason': 'frappe_sso_not_configured',
            },
        })


class FinanceLaunchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not finance_eligible(request.user):
            return private_response({'code': 'finance_access_denied'}, status=403)
        # No code, redirect, cookie or session is issued while infrastructure is
        # unavailable. User-supplied redirect/identity/role fields are never used.
        return private_response({
            'code': 'frappe_sso_not_configured',
            'detail': 'A verified ERPNext site and secure per-user SSO are required.',
        }, status=503)
