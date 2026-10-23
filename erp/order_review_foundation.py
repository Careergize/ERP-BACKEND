"""Inactive review custody/issuer-consumer consistency primitives.

These candidates prove internal consistency ONLY. No route/writer imports this
module. Supplied document bytes, maker sets and source projections still require
trusted repositories, complete scoped policy and locked service reads. Even
perfectly fabricated candidates do not authorise confirmation or Job Cards.
Candidate serialization is engineering version 1, not an approved C06 contract.
"""
from dataclasses import dataclass
import hashlib
import re

from .models import ApprovalDecision, CommandReceipt
from .order_control_foundation import (OrderSnapshot, capture_snapshot, require_order_revision,
                                      require_version, verify_snapshot)
from .order_drafts import DraftError, checked_revision
from .order_confirmation_evidence import ConfirmationEvidenceCandidate, inspect_confirmation_evidence


def positive(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise DraftError('VALIDATION_ERROR', 400)
    return value


def identities(values):
    if type(values) not in (tuple, set, frozenset) or not values:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    result = tuple(sorted(positive(value) for value in values))
    if len(set(result)) != len(result):
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    return result


@dataclass(frozen=True)
class DocumentCandidate:
    reference: str
    version: int
    order_id: int
    revision_id: int
    digest: str
    body: bytes


@dataclass(frozen=True)
class FrozenReviewPackage:
    """Detached immutable custody value, NOT a persisted submission event."""
    order_id: int
    revision_id: int
    content_revision: int
    reviewed_version: int
    submitter_id: int
    maker_ids: tuple[int, ...]
    content: OrderSnapshot
    document_manifest: OrderSnapshot
    manifest: OrderSnapshot


@dataclass(frozen=True)
class ActiveSourceProjection:
    order_id: int
    revision_id: int
    control_version: int
    # None means unknown, never an inferred absence of suspension.
    suspension_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ReviewBoundConfirmationCandidate:
    review_package_hash: str
    evidence: ConfirmationEvidenceCandidate


def document_manifest(documents, order_id, revision_id):
    if type(documents) is not tuple or not documents:
        raise DraftError('REVIEW_EVIDENCE_REQUIRED')
    result, references = [], set()
    for doc in documents:
        if type(doc) is not DocumentCandidate:
            raise DraftError('REVIEW_EVIDENCE_REQUIRED')
        positive(doc.version)
        require_order_revision(order_id, doc.order_id)
        if positive(doc.revision_id) != revision_id:
            raise DraftError('REVISION_CONFLICT')
        if (type(doc.reference) is not str or not doc.reference.strip() or len(doc.reference) > 2000 or
                doc.reference in references or type(doc.body) is not bytes or not doc.body or
                type(doc.digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', doc.digest) or
                hashlib.sha256(doc.body).hexdigest() != doc.digest):
            raise DraftError('REVIEW_EVIDENCE_REQUIRED')
        references.add(doc.reference)
        result.append({'reference': doc.reference, 'version': doc.version, 'order': doc.order_id,
                       'revision': doc.revision_id, 'digest': doc.digest})
    # Manifest is a set of unique references. No content item/array reordering.
    return capture_snapshot(sorted(result, key=lambda row: row['reference']))


def package_manifest(package):
    return capture_snapshot({'schema': 'order-review-candidate-v1', 'order': package.order_id,
        'revision': package.revision_id, 'contentRevision': package.content_revision,
        'reviewedVersion': package.reviewed_version, 'submitter': package.submitter_id,
        'makers': list(package.maker_ids), 'contentHash': package.content.digest,
        'documentsHash': package.document_manifest.digest})


def freeze_review_package(order, expected_version, submitter_id, documents, complete_maker_ids):
    """Freeze a candidate only; no state transition, evidence insert or authority.

    Complete maker IDs are an explicit trusted-caller projection to be verified
    later under D02, not inferred from a role or an empty contributor queryset.
    """
    require_version(expected_version, order.control_version)
    if order.commercial_status not in ('Draft', 'InReview'):
        raise DraftError('ORDER_NOT_EDITABLE')
    revision = checked_revision(order)
    submitter_id = positive(submitter_id)
    stored_makers = set(revision.contributors.values_list('actor_id', flat=True))
    if not {order.created_by_id, revision.captured_by_id} <= stored_makers:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    makers = identities(complete_maker_ids)
    if set(makers) != stored_makers | {submitter_id}:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    content = verify_snapshot(revision.content, revision.content_hash)
    docs = document_manifest(documents, order.pk, revision.pk)
    package = FrozenReviewPackage(order.pk, revision.pk, revision.revision, expected_version,
        submitter_id, makers, content, docs, capture_snapshot({}))
    return FrozenReviewPackage(**{**package.__dict__, 'manifest': package_manifest(package)})


def verify_review_package(order, package, documents, current_maker_ids, *, resulting=False):
    if type(package) is not FrozenReviewPackage:
        raise DraftError('REVIEW_EVIDENCE_REQUIRED')
    require_order_revision(order.pk, package.order_id)
    positive(package.reviewed_version)
    positive(package.revision_id)
    positive(package.content_revision)
    positive(package.submitter_id)
    require_version(package.reviewed_version + (1 if resulting else 0), order.control_version)
    if order.commercial_status not in (('Confirmed',) if resulting else ('Draft', 'InReview')):
        raise DraftError('ORDER_CONFIRMATION_REQUIRED' if resulting else 'ORDER_NOT_EDITABLE')
    revision = checked_revision(order)
    if revision.pk != package.revision_id or revision.revision != package.content_revision:
        raise DraftError('SOURCE_REVISION_CHANGED')
    if identities(package.maker_ids) != package.maker_ids or identities(current_maker_ids) != package.maker_ids:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    persisted = set(revision.contributors.values_list('actor_id', flat=True))
    if (not {order.created_by_id, revision.captured_by_id} <= persisted or
            persisted | {package.submitter_id} != set(package.maker_ids)):
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    for snapshot in (package.content, package.document_manifest, package.manifest):
        if type(snapshot) is not OrderSnapshot:
            raise DraftError('REVISION_CONFLICT')
        verified = verify_snapshot(snapshot.decode(), snapshot.digest)
        if verified.canonical != snapshot.canonical:
            raise DraftError('REVISION_CONFLICT')
    if (package.content.digest != revision.content_hash or
            document_manifest(documents, order.pk, revision.pk) != package.document_manifest or
            package_manifest(package) != package.manifest):
        raise DraftError('REVISION_CONFLICT')
    return package


def validate_checker(package, checker_id, complete_maker_ids):
    positive(checker_id)
    if identities(complete_maker_ids) != package.maker_ids:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    if checker_id in package.maker_ids:
        raise DraftError('SELF_APPROVAL_FORBIDDEN', 403)
    # No role/staff/superuser input can reduce the conflict set.


def validate_active_source(order, source):
    if type(source) is not ActiveSourceProjection:
        raise DraftError('COMMITMENT_EVIDENCE_REQUIRED')
    require_order_revision(order.pk, source.order_id)
    positive(source.revision_id)
    require_version(source.control_version, order.control_version)
    if order.commercial_status != 'Confirmed':
        raise DraftError('ORDER_CONFIRMATION_REQUIRED')
    if source.revision_id != order.confirmed_revision_id or source.revision_id != order.current_revision_id:
        raise DraftError('SOURCE_REVISION_CHANGED')
    if source.suspension_ids is None:
        raise DraftError('COMMITMENT_EVIDENCE_REQUIRED')
    if type(source.suspension_ids) is not tuple or any(type(value) is not str or not value.strip() for value in source.suspension_ids):
        raise DraftError('VALIDATION_ERROR', 400)
    if source.suspension_ids:
        raise DraftError('ORDER_SUSPENDED')
    # This comparison cannot establish authoritative suspension discovery.


def confirmation_candidate_digest(package, checker_id, command_id, reason):
    """Provisional exact binding for test-only issuer/consumer consistency.

    No persisted writer uses this format; a matching digest is never authority.
    """
    from uuid import UUID
    if type(command_id) is not str:
        raise DraftError('VALIDATION_ERROR', 400)
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError()
    except ValueError:
        raise DraftError('VALIDATION_ERROR', 400)
    if type(reason) is not str or not reason.strip() or len(reason) > 2000:
        raise DraftError('VALIDATION_ERROR', 400)
    validate_checker(package, checker_id, package.maker_ids)
    return capture_snapshot({'schema': 'order-confirmation-candidate-v1', 'action': 'order.confirm',
        'commandId': command_id, 'actor': checker_id, 'order': package.order_id,
        'expectedVersion': package.reviewed_version, 'revision': package.revision_id,
        'reviewPackageHash': package.manifest.digest, 'reason': reason}).digest


def inspect_review_bound_confirmation(order, package, documents, current_maker_ids, source):
    """Read-only typed consistency bridge; intentionally NOT a trusted issuer."""
    verify_review_package(order, package, documents, current_maker_ids, resulting=True)
    validate_active_source(order, source)
    candidate = inspect_confirmation_evidence(order, package.revision_id, order.control_version)
    decision = ApprovalDecision.objects.get(pk=candidate.decision_id)
    receipt = CommandReceipt.objects.get(pk=candidate.command_id)
    validate_checker(package, decision.checker_id, current_maker_ids)
    if (decision.target_version != package.reviewed_version or
            receipt.response_body.get('reviewPackageHash') != package.manifest.digest or
            receipt.payload_digest != confirmation_candidate_digest(package, decision.checker_id,
                                                                   candidate.command_id, decision.reason)):
        raise DraftError('CONFIRMATION_EVIDENCE_REQUIRED')
    return ReviewBoundConfirmationCandidate(package.manifest.digest, candidate)
