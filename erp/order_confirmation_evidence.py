"""Read-only C06 evidence consistency checks; never an authority verifier.

Matching database rows/hashes do not establish approved policy or trusted issuance.
No confirmation writer, grants, feature flag or test override is provided here.
The C06 service/provenance and D01/D02/D03 validators remain unimplemented.
"""
from dataclasses import dataclass
import re

from .models import ApprovalDecision, CommandReceipt
from .order_control_foundation import capture_snapshot, require_order_revision, require_version, verify_snapshot
from .order_drafts import CONTENT, DraftError, checked_revision


@dataclass(frozen=True)
class ConfirmationEvidenceCandidate:
    """Consistent persisted evidence only, NOT permission to create/produce."""
    order_id: int
    order_version: int
    revision_id: int
    content_hash: str
    decision_id: int
    command_id: str
    contributor_ids: tuple


def _required():
    raise DraftError('CONFIRMATION_EVIDENCE_REQUIRED')


def inspect_confirmation_evidence(order, revision_id, version):
    """Caller owns order lock for commands; reads confer no future authority.

    Conservative C06 binding: decision targets the reviewed pre-transition
    control version; its receipt binds the resulting control version and exact
    content revision. Content revision numbers are never used as root versions.
    Later root transitions require the eventual active-source validator.
    """
    require_version(version, order.control_version)
    # Fulfillment JSON status is not the commercial approval state (T02.1).
    if order.commercial_status != 'Confirmed':
        raise DraftError('ORDER_CONFIRMATION_REQUIRED')
    if type(revision_id) is not int or not 1 <= revision_id <= 2**63 - 1:
        raise DraftError('VALIDATION_ERROR', 400)
    revision = order.confirmed_revision
    if revision is None:
        raise DraftError('LEGACY_REVIEW_REQUIRED')
    require_order_revision(order.pk, revision.order_id)
    if revision.pk != revision_id or order.current_revision_id != revision_id:
        raise DraftError('SOURCE_REVISION_CHANGED')
    checked_revision(order)
    makers = set(revision.contributors.values_list('actor_id', flat=True))
    if not {order.created_by_id, revision.captured_by_id} <= makers:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    # Observed-at-cutover content is not a contemporaneous confirmation event.
    if revision.provenance != 'Contemporaneous':
        _required()
    decisions = list(ApprovalDecision.objects.filter(workflow_order=order, action='order.confirm',
        target_version=version - 1, content_hash=revision.content_hash).order_by('pk')[:2])
    if len(decisions) != 1:
        _required()  # No latest-row guessing when evidence is absent/ambiguous.
    decision = decisions[0]
    if (decision.outcome != 'Approved' or decision.checker_id in makers or
            not decision.reason.strip() or not decision.policy_evidence_reference.strip() or
            not re.fullmatch(r'[0-9a-f]{64}', decision.policy_evidence_hash)):
        _required()
    verify_snapshot(decision.snapshot, decision.content_hash)
    if (capture_snapshot(decision.snapshot).digest != revision.content_hash or
            decision.decided_at < revision.captured_at or
            revision.contributors.filter(captured_at__gt=decision.decided_at).exists()):
        _required()
    receipts = list(CommandReceipt.objects.filter(workflow_order=order, action='order.confirm',
        response_body__decisionId=decision.pk).order_by('created_at')[:2])
    if len(receipts) != 1:
        _required()
    receipt = receipts[0]
    body = receipt.response_body
    if (receipt.schema_version != 1 or receipt.actor_id != decision.checker_id or
            receipt.response_status != 200 or receipt.completed_at is None or
            receipt.created_at > decision.decided_at or receipt.completed_at < decision.decided_at or
            not re.fullmatch(r'[0-9a-f]{64}', receipt.payload_digest) or type(body) is not dict):
        _required()
    aggregate, result = body.get('aggregate'), body.get('result')
    if (body.get('commandId') != str(receipt.pk) or type(body.get('decisionId')) is not int or
            body['decisionId'] != decision.pk or type(aggregate) is not dict or
            aggregate != {'type': 'order', 'id': f'wf-order-{order.pk}', 'version': version} or
            type(aggregate.get('version')) is not int or type(result) is not dict):
        _required()
    for key, expected in [('id', f'wf-order-{order.pk}'), ('controlVersion', version),
                          ('currentRevision', revision.pk), ('confirmedRevision', revision.pk),
                          ('commercialStatus', 'Confirmed')]:
        if type(result.get(key)) is not type(expected) or result[key] != expected:
            _required()
    saved = {key: value for key, value in result.items() if key in {*CONTENT, 'items', 'orderTotal'}}
    if capture_snapshot(saved).digest != revision.content_hash:
        _required()
    return ConfirmationEvidenceCandidate(order.pk, version, revision.pk, revision.content_hash,
                                         decision.pk, str(receipt.pk), tuple(sorted(makers)))
