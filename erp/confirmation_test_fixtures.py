"""Synthetic persisted C06-shaped rows, NOT trusted operational confirmations.

No production module imports this file. It has no policy grants/authority mocks.
"""
from copy import deepcopy
from uuid import uuid4
from django.utils import timezone
from .models import ApprovalDecision, CommandReceipt
from .order_drafts import projection


def confirmation_rows(order, checker, *, decision_changes=None, result_changes=None,
                      body_changes=None, receipt_changes=None):
    values = {'command_id': uuid4(), 'actor': checker, 'action': 'order.confirm',
              'schema_version': 1, 'payload_digest': 'a' * 64}
    receipt = CommandReceipt.objects.create(**values)
    revision = order.confirmed_revision
    values = {'workflow_order': order, 'snapshot': deepcopy(revision.content),
              'content_hash': revision.content_hash, 'target_version': order.control_version - 1,
              'checker': checker, 'action': 'order.confirm', 'outcome': 'Approved',
              'reason': 'Synthetic consistency fixture; not operational authority',
              'policy_evidence_reference': 'synthetic-unverified-policy', 'policy_evidence_hash': 'b' * 64}
    values.update(decision_changes or {})
    decision = ApprovalDecision.objects.create(**values)
    result = projection(order)
    result.update(result_changes or {})
    body = {'commandId': str(receipt.pk), 'aggregate': {'type': 'order', 'id': f'wf-order-{order.pk}',
            'version': order.control_version}, 'result': result, 'decisionId': decision.pk}
    body.update(body_changes or {})
    receipt.workflow_order = order
    receipt.response_status = 200
    receipt.response_body = body
    receipt.completed_at = timezone.now()
    for key, value in (receipt_changes or {}).items():
        setattr(receipt, key, value)
    receipt.save()
    return decision, receipt
