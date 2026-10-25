"""Role mapping explicitly approved by the workspace owner on 25 October 2026.

Evidence: Backend/PERSISTENCE_STATUS.md and the owner's approval of that table.
Permissions, scope, independence, quantity and idempotency checks remain required.
"""
from django.conf import settings
from rest_framework.exceptions import PermissionDenied

POLICY_VERSION = 'navapack-owner-approved-2026-10-25'
ROLES = {
    'order-approve': {'admin', 'production_manager'},
    'job-create': {'admin', 'receptionist'},
    'job-approve': {'admin', 'production_manager'},
    'job-issue': {'admin', 'store_keeper'},
    'costing-approve': {'admin', 'accounts', 'production_manager'},
    'production': {'admin', 'production_manager', 'production_operator'},
    'qc-record': {'admin', 'qc_inspector', 'qc_supervisor'},
    'qc-review': {'admin', 'qc_supervisor'},
    'stock-authorize': {'admin', 'production_manager'},
    'stock-receipt': {'admin', 'store_keeper'},
    'dispatch': {'admin', 'store_keeper', 'receptionist'},
    'invoice': {'admin', 'accounts', 'receptionist'},
}


def authorize(user, action):
    if getattr(settings, 'NAVAPACK_OPERATIONAL_POLICY', '') != POLICY_VERSION:
        raise PermissionDenied('Operational transition unavailable: approved authority mapping and policy evidence are required.')
    if not user.is_active or user.effective_role not in ROLES.get(action, set()):
        raise PermissionDenied('Your assigned backend role cannot perform this operational action.')
