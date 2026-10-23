"""Unregistered structural candidates; no synthetic authority is enabled."""
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from inventory.models import RawMovement
from . import test_job_drafts as fixtures
from .models import (ApprovalDecision, CommandReceipt, ContributorEvidence, FinishedStockItem,
                     WorkflowJob, WorkflowOrder, WorkflowOrderRevision)
from .order_control_foundation import OrderControlError, capture_snapshot
from .order_drafts import DraftError, projection
from .order_review_foundation import (ActiveSourceProjection, DocumentCandidate,
    confirmation_candidate_digest, freeze_review_package, inspect_review_bound_confirmation,
    validate_active_source, validate_checker, verify_review_package)


class ReviewFoundationTests(TestCase):
    setUp = fixtures.JobDraftTests.setUp
    user = fixtures.JobDraftTests.user
    fixture = fixtures.JobDraftTests.fixture
    command = fixtures.JobDraftTests.command

    def package(self, order=None, submitter=None):
        order = order or self.order
        reviewed = copy(order)
        reviewed.control_version = 2
        reviewed.commercial_status = 'InReview'
        reviewed.confirmed_revision = None
        body = b'Synthetic customer evidence; not an adopted document'
        docs = (DocumentCandidate('synthetic-doc', 1, order.pk, order.current_revision_id,
                                  hashlib.sha256(body).hexdigest(), body),)
        makers = tuple(sorted({self.actor.pk, submitter or self.actor.pk}))
        return reviewed, docs, freeze_review_package(reviewed, 2, submitter or self.actor.pk, docs, makers)

    def source(self, order=None, suspension=()):
        order = order or self.order
        return ActiveSourceProjection(order.pk, order.confirmed_revision_id, order.control_version, suspension)

    def blocked(self, function, *args, code=None, **kwargs):
        with self.assertRaises((DraftError, OrderControlError)) as caught:
            function(*args, **kwargs)
        actual = caught.exception.detail['code'] if isinstance(caught.exception, DraftError) else caught.exception.code
        if code:
            self.assertEqual(actual, code)

    def counts(self):
        return tuple(model.objects.count() for model in
                     (WorkflowOrder, WorkflowOrderRevision, ContributorEvidence, ApprovalDecision, CommandReceipt, WorkflowJob))

    def fresh_bound_rows(self, *, digest_override=None, manifest_override=None):
        # New root reuses immutable content but has its own parent/revision and no previous decision.
        content = deepcopy(self.order.current_revision.content)
        order = WorkflowOrder.objects.create(request_id=uuid4(), created_by=self.actor, control_version=2,
                                             commercial_status='InReview', data={})
        snapshot = capture_snapshot(content)
        revision = WorkflowOrderRevision.objects.create(order=order, revision=1, content=content,
            content_hash=snapshot.digest, captured_by=self.actor, reason='Synthetic review only', provenance='Contemporaneous')
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=revision, actor=self.actor, action='Synthetic maker')
        order.current_revision = revision
        order.data = {**content, 'id': f'wf-order-{order.pk}', 'status': 'Draft', 'jobCardIds': []}
        order.save()
        reviewed, docs, package = self.package(order)
        command_id = str(uuid4())
        reason = 'Synthetic consistency fixture, never authority'
        digest = confirmation_candidate_digest(package, self.checker.pk, command_id, reason)
        receipt = CommandReceipt.objects.create(command_id=command_id, actor=self.checker,
            action='order.confirm', schema_version=1, payload_digest=digest_override or digest)
        decision = ApprovalDecision.objects.create(workflow_order=order, action='order.confirm', outcome='Approved',
            snapshot=content, content_hash=snapshot.digest, target_version=2, checker=self.checker,
            reason=reason, policy_evidence_reference='unverified-synthetic-policy', policy_evidence_hash='b' * 64)
        # Test-only simulated transition, no production writer is implemented.
        order.commercial_status = 'Confirmed'; order.control_version = 3
        order.confirmed_revision = revision; order.save()
        receipt.workflow_order = order; receipt.completed_at = timezone.now(); receipt.response_status = 200
        receipt.response_body = {'commandId': command_id, 'decisionId': decision.pk,
            'aggregate': {'type': 'order', 'id': f'wf-order-{order.pk}', 'version': 3},
            'result': projection(order), 'reviewPackageHash': manifest_override or package.manifest.digest}
        receipt.save()
        return order, reviewed, docs, package, decision, receipt

    def test_frozen_custody_exact_binding_and_detached_content(self):
        reviewed, docs, package = self.package()
        before = self.counts()
        self.assertIs(verify_review_package(reviewed, package, docs, package.maker_ids), package)
        package.content.decode()['items'].clear()
        self.assertEqual(len(package.content.decode()['items']), 2)
        with self.assertRaises(FrozenInstanceError):
            package.reviewed_version = 99
        with self.assertRaises(FrozenInstanceError):
            docs[0].body = b'changed'
        self.assertEqual(self.counts(), before)

    def test_missing_altered_duplicate_cross_order_and_wrong_revision_documents(self):
        reviewed, docs, package = self.package()
        variants = [(), (docs[0], docs[0]), (replace(docs[0], body=b'altered'),),
            (replace(docs[0], digest='0' * 64),), (replace(docs[0], version=2),),
            (replace(docs[0], reference='other-doc'),), (replace(docs[0], revision_id=999999),),
            (replace(docs[0], order_id=999999),)]
        for current in variants:
            with self.subTest(current=current):
                self.blocked(verify_review_package, reviewed, package, current, package.maker_ids)

    def test_stale_version_content_revision_parent_and_manifest_rejected(self):
        reviewed, docs, package = self.package()
        wrong = copy(reviewed); wrong.control_version = 3
        self.blocked(verify_review_package, wrong, package, docs, package.maker_ids, code='VERSION_CONFLICT')
        for forged in (replace(package, order_id=999999), replace(package, revision_id=999999),
                       replace(package, content_revision=True), replace(package, revision_id=True),
                       replace(package, manifest=capture_snapshot({'forged': True})),
                       replace(package, content=capture_snapshot({'items': []}))):
            self.blocked(verify_review_package, reviewed, forged, docs, package.maker_ids)
        wrong = copy(reviewed); wrong.data = {**reviewed.data, 'items': []}
        self.blocked(verify_review_package, wrong, package, docs, package.maker_ids, code='REVISION_CONFLICT')

    def test_submitter_and_all_contributors_conflict_even_with_superuser(self):
        reviewed, docs, package = self.package(submitter=self.editor.pk)
        for maker in package.maker_ids:
            self.blocked(validate_checker, package, maker, package.maker_ids, code='SELF_APPROVAL_FORBIDDEN')
        self.actor.is_staff = self.actor.is_superuser = True; self.actor.save()
        self.blocked(validate_checker, package, self.actor.pk, package.maker_ids, code='SELF_APPROVAL_FORBIDDEN')
        validate_checker(package, self.checker.pk, package.maker_ids)
        for makers in ((), None, (self.actor.pk,), (self.actor.pk, self.actor.pk)):
            self.blocked(verify_review_package, reviewed, package, docs, makers, code='MAKER_EVIDENCE_REQUIRED')
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=reviewed.current_revision,
                                          actor=self.checker, action='Synthetic late correction')
        self.blocked(verify_review_package, reviewed, package, docs, package.maker_ids, code='MAKER_EVIDENCE_REQUIRED')

    def test_active_source_unknown_suspension_cancelled_stale_and_cross_order(self):
        validate_active_source(self.order, self.source())
        self.blocked(validate_active_source, self.order, self.source(suspension=None), code='COMMITMENT_EVIDENCE_REQUIRED')
        self.blocked(validate_active_source, self.order, self.source(suspension=('synthetic-change-1',)), code='ORDER_SUSPENDED')
        for change in ({'order_id': 999999}, {'revision_id': 999999}, {'revision_id': True}, {'control_version': 2}):
            self.blocked(validate_active_source, self.order, replace(self.source(), **change))
        for state in ('Draft', 'InReview', 'Cancelled', None):
            order = copy(self.order); order.commercial_status = state
            self.blocked(validate_active_source, order, self.source(), code='ORDER_CONFIRMATION_REQUIRED')

    def test_candidate_command_identity_exact_duplicates_and_conflicts(self):
        _, _, package = self.package()
        command_id = str(uuid4())
        digest = confirmation_candidate_digest(package, self.checker.pk, command_id, 'Synthetic reason')
        self.assertEqual(digest, confirmation_candidate_digest(package, self.checker.pk, command_id, 'Synthetic reason'))
        for actor, command, reason in ((self.editor.pk, command_id, 'Synthetic reason'),
                                       (self.checker.pk, str(uuid4()), 'Synthetic reason'),
                                       (self.checker.pk, command_id, 'Changed reason')):
            self.assertNotEqual(digest, confirmation_candidate_digest(package, actor, command, reason))

    def test_bound_rows_are_still_candidates_and_operational_creation_is_closed(self):
        order, _, docs, package, _, _ = self.fresh_bound_rows()
        before = self.counts()
        stock, movement = list(FinishedStockItem.objects.values()), list(RawMovement.objects.values())
        candidate = inspect_review_bound_confirmation(order, package, docs, package.maker_ids, self.source(order))
        self.assertEqual(candidate.review_package_hash, package.manifest.digest)
        body = self.command(); body.update(orderId=order.pk, sourceConfirmedRevision=order.confirmed_revision_id)
        response = self.client.post(fixtures.BASE, body, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'AUTHORITY_NOT_CONFIGURED')
        self.assertEqual(self.counts(), before)
        self.assertEqual(list(FinishedStockItem.objects.values()), stock)
        self.assertEqual(list(RawMovement.objects.values()), movement)
        self.assertFalse(WorkflowJob.objects.exists())

    def test_old_structural_receipts_without_document_command_binding_are_rejected(self):
        _, docs, package = self.package()
        self.blocked(inspect_review_bound_confirmation, self.order, package, docs, package.maker_ids,
                     self.source(), code='CONFIRMATION_EVIDENCE_REQUIRED')
        for changes in ({'digest_override': '0' * 64}, {'manifest_override': '0' * 64}):
            order, _, docs, package, _, _ = self.fresh_bound_rows(**changes)
            self.blocked(inspect_review_bound_confirmation, order, package, docs, package.maker_ids,
                         self.source(order), code='CONFIRMATION_EVIDENCE_REQUIRED')

    def test_ambiguous_decision_and_transactional_rollback_of_synthetic_rows(self):
        before = self.counts()
        with self.assertRaises(DraftError), transaction.atomic():
            order, _, docs, package, decision, _ = self.fresh_bound_rows()
            ApprovalDecision.objects.create(workflow_order=order, action=decision.action, outcome=decision.outcome,
                snapshot=decision.snapshot, content_hash=decision.content_hash, target_version=decision.target_version,
                checker=self.checker, reason='Ambiguous synthetic decision', policy_evidence_reference='synthetic',
                policy_evidence_hash='c' * 64)
            inspect_review_bound_confirmation(order, package, docs, package.maker_ids, self.source(order))
        self.assertEqual(self.counts(), before)
        # This proves the synthetic caller transaction rolls back, not a production issuer.
