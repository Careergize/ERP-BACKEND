from rest_framework.permissions import BasePermission
from accounts.applications import finance_eligible


class CanInspectFinanceStatus(BasePermission):
    message = 'Your account cannot inspect ERPNext Finance status.'

    def has_permission(self, request, view):
        # Reuse the existing grant-inspection permission for this diagnostic action.
        # Superuser/role permission shortcuts never bypass the explicit entitlement.
        return (finance_eligible(request.user)
                and request.user.has_perm('accounts.view_financeentitlement'))
