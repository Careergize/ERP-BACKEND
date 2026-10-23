"""Evidence consistency is testable; trusted confirmation is deliberately not faked."""
from copy import deepcopy
from uuid import uuid4
from django.test import TestCase
from . import test_job_drafts as fixtures
from .confirmation_test_fixtures import confirmation_rows
from .models import ApprovalDecision, CommandReceipt, WorkflowJob, WorkflowJobRevision, ContributorEvidence, WorkflowOrderRevision
from .order_confirmation_evidence import inspect_confirmation_evidence
from .order_control_foundation import OrderControlError, capture_snapshot
from .order_drafts import DraftError
from .job_drafts import _save_preparation
from inventory.models import RawMaterial, RawMovement

BASE = fixtures.BASE


class ConfirmationEvidenceTests(TestCase):
    # Reuse synthetic source setup; do not inherit/re-run its test methods.
    setUp = fixtures.JobDraftTests.setUp
    user = fixtures.JobDraftTests.user
    fixture = fixtures.JobDraftTests.fixture
    command = fixtures.JobDraftTests.command

    def inspect(self, order=None):
        order = order or self.order
        return inspect_confirmation_evidence(order, order.confirmed_revision_id, order.control_version)

    def counts(self):
        return tuple(model.objects.count() for model in
            [WorkflowJob, WorkflowJobRevision, ContributorEvidence, CommandReceipt, ApprovalDecision])

    def assert_blocked(self, code, order=None):
        order = order or self.order
        body = self.command()
        body.update(orderId=order.pk, expectedOrderVersion=order.control_version,
                    sourceConfirmedRevision=order.confirmed_revision_id)
        before = self.counts()
        materials, movements = list(RawMaterial.objects.values()), list(RawMovement.objects.values())
        response = self.client.post(BASE, body, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], code)
        self.assertEqual(self.counts(), before)
        self.assertEqual(list(RawMaterial.objects.values()), materials)
        self.assertEqual(list(RawMovement.objects.values()), movements)
        order.refresh_from_db()
        self.assertEqual(order.data['jobCardIds'], [])

    def replace_revision(self, order, *, content=None, provenance='Contemporaneous'):
        content = deepcopy(order.current_revision.content if content is None else content)
        snapshot = capture_snapshot(content)
        revision = WorkflowOrderRevision.objects.create(order=order, revision=2, content=content,
            content_hash=snapshot.digest, captured_by=self.actor, reason='Synthetic new source', provenance=provenance)
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=revision,
                                          actor=self.actor, action='Synthetic source author')
        order.current_revision = order.confirmed_revision = revision
        order.data.update(content)
        order.save()
        return revision

    def test_consistent_persisted_rows_are_candidates_not_authority(self):
        candidate = self.inspect()
        decision = ApprovalDecision.objects.get(workflow_order=self.order)
        self.assertEqual(candidate.revision_id, self.order.confirmed_revision_id)
        self.assertEqual(candidate.content_hash, self.order.confirmed_revision.content_hash)
        self.assertEqual(candidate.decision_id, decision.pk)
        self.assertEqual(candidate.contributor_ids, (self.actor.pk,))
        # Fulfillment labels are neither confirmation proof nor a competing
        # commercial state. Only the separate commercial control is checked.
        self.order.data['status'] = 'Awaiting dispatch'
        self.order.save()
        self.assertEqual(self.inspect().decision_id, candidate.decision_id)
        # A synthetically fabricated yet perfectly consistent decision/receipt
        # cannot cause operational creation, even for a superuser.
        self.assert_blocked('AUTHORITY_NOT_CONFIGURED')
        self.actor.is_superuser = self.actor.is_staff = True
        self.actor.save()
        self.assert_blocked('AUTHORITY_NOT_CONFIGURED')
        self.assertEqual(self.client.get(BASE).data['capabilities']['create'], False)

    def test_draft_cancelled_unreviewed_and_legacy_are_ineligible(self):
        for state in ['Draft', 'InReview', 'Cancelled', None]:
            with self.subTest(state=state):
                order = self.fixture()
                order.commercial_status = state
                order.data['status'] = 'Confirmed'  # A label cannot rescue Draft/unknown controls.
                order.save()
                self.assert_blocked('ORDER_CONFIRMATION_REQUIRED', order)
        order = self.fixture()
        order.control_version = None
        order.save()
        # Command still supplies a syntactically valid expected version.
        body = self.command()
        body.update(orderId=order.pk, expectedOrderVersion=3, sourceConfirmedRevision=order.confirmed_revision_id)
        response = self.client.post(BASE, body, format='json')
        self.assertEqual(response.data['code'], 'LEGACY_REVIEW_REQUIRED')

    def test_merely_status_labelled_and_missing_receipt_are_rejected(self):
        self.replace_revision(self.order, content={**self.order.current_revision.content, 'customerName': 'No decision'})
        self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED')
        revision = self.order.confirmed_revision
        ApprovalDecision.objects.create(workflow_order=self.order, action='order.confirm', outcome='Approved',
            snapshot=revision.content, content_hash=revision.content_hash, target_version=2,
            checker=self.checker, reason='Synthetic decision without receipt',
            policy_evidence_reference='fabricated reference', policy_evidence_hash='c' * 64)
        self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED')

    def test_wrong_action_outcome_version_checker_or_snapshot_is_rejected(self):
        variants = [{'action': 'order.review'}, {'outcome': 'Rejected'}, {'outcome': 'Verified'},
                    {'target_version': 1}, {'checker': self.actor},
                    {'snapshot': {'forged': True}}, {'content_hash': '0' * 64}]
        for changes in variants:
            with self.subTest(changes=changes):
                order = self.fixture()
                self.replace_revision(order, content={**order.current_revision.content, 'customerName': str(uuid4())})
                confirmation_rows(order, self.checker, decision_changes=changes)
                with self.assertRaises((DraftError, OrderControlError)):
                    self.inspect(order)
                # Hash mismatch may report REVISION_CONFLICT; all stop before writes.
                body = self.command()
                body.update(orderId=order.pk, sourceConfirmedRevision=order.confirmed_revision_id)
                response = self.client.post(BASE, body, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertFalse(WorkflowJob.objects.exists())

    def test_receipt_exact_decision_actor_revision_content_and_version_binding(self):
        variants = [({'confirmedRevision': 999999}, {}, {}), ({'controlVersion': True}, {}, {}),
                    ({'currentRevision': 999999}, {}, {}), ({'items': []}, {}, {}),
                    ({'commercialStatus': 'Draft'}, {}, {}),
                    ({}, {'commandId': str(uuid4())}, {}),
                    ({}, {'aggregate': {'type': 'order', 'id': 'wf-order-99999', 'version': 3}}, {}),
                    ({}, {}, {'actor': self.actor}), ({}, {}, {'schema_version': 2}),
                    ({}, {}, {'response_status': 201}), ({}, {}, {'action': 'order.review'})]
        for result_changes, body_changes, receipt_changes in variants:
            with self.subTest(result=result_changes, body=body_changes, receipt=receipt_changes):
                order = self.fixture()
                self.replace_revision(order, content={**order.current_revision.content, 'customerName': str(uuid4())})
                # Immutable receipt identity cannot be overwritten; actor/action/schema
                # variants are constructed as distinct original reservations below.
                if receipt_changes:
                    receipt = CommandReceipt.objects.create(command_id=uuid4(), actor=receipt_changes.get('actor', self.checker),
                        action=receipt_changes.get('action', 'order.confirm'), schema_version=receipt_changes.get('schema_version', 1), payload_digest='a' * 64)
                    decision = ApprovalDecision.objects.create(workflow_order=order, snapshot=order.confirmed_revision.content,
                        content_hash=order.confirmed_revision.content_hash, target_version=2, checker=self.checker,
                        action='order.confirm', outcome='Approved', reason='Synthetic',
                        policy_evidence_reference='unverified', policy_evidence_hash='b' * 64)
                    from django.utils import timezone
                    from .order_drafts import projection
                    receipt.workflow_order = order
                    receipt.completed_at = timezone.now()
                    receipt.response_status = receipt_changes.get('response_status', 200)
                    receipt.response_body = {'commandId': str(receipt.pk), 'decisionId': decision.pk,
                        'aggregate': {'type': 'order', 'id': f'wf-order-{order.pk}', 'version': 3}, 'result': projection(order)}
                    receipt.save()
                else:
                    confirmation_rows(order, self.checker, result_changes=result_changes, body_changes=body_changes)
                self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED', order)

    def test_ambiguous_decisions_stale_root_cross_order_and_cutover_not_inferred(self):
        confirmation_rows(self.order, self.checker)
        self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED')
        other = self.fixture()
        other.control_version += 1
        other.save()
        self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED', other)
        cross = self.fixture()
        cross.confirmed_revision = self.order.confirmed_revision
        cross.save()
        self.assert_blocked('REVISION_CONFLICT', cross)
        historical = self.fixture()
        self.replace_revision(historical, provenance='ObservedAtCutover')
        confirmation_rows(historical, self.checker)
        self.assert_blocked('CONFIRMATION_EVIDENCE_REQUIRED', historical)

    def test_private_preparation_binds_decision_content_and_preserves_makers(self):
        # No policy/confirmation patches; this is private Draft persistence only.
        body = self.command()
        result, status, _ = _save_preparation(self.actor, body)
        self.assertEqual(status, 201)
        job = WorkflowJob.objects.get()
        candidate = self.inspect()
        self.assertEqual(job.data['sourceConfirmationDecision'], candidate.decision_id)
        self.assertEqual(job.data['sourceConfirmationCommand'], candidate.command_id)
        self.assertEqual(job.data['sourceOrderSnapshot'], self.order.confirmed_revision.content)
        self.assertEqual(set(job.control_revisions.get().contributors.values_list('actor_id', flat=True)), {self.actor.pk})
        self.assertEqual(result['status'], 'Draft')
        self.assertEqual(result['capabilities'], {'edit': False, 'approve': False, 'reject': False, 'issue': False})
        before = self.counts()
        replay = self.client.post(BASE, body, format='json')
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(replay.data, result)
        self.assertEqual(self.counts(), before)
        # Existing receipt replay is retrieval only; a fresh edit is still blocked.
        self.assert_blocked_edit(job)

    def assert_blocked_edit(self, job):
        response = self.client.post(f'{BASE}{job.pk}/', self.command(1), format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'AUTHORITY_NOT_CONFIGURED')
        job.refresh_from_db()
        self.assertEqual(job.version, 1)

    def test_http_permissions_and_client_supplied_decision_cannot_bypass(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(BASE, self.command(), format='json').status_code, 401)
        from django.contrib.auth import get_user_model
        self.client.force_authenticate(get_user_model().objects.create_user('no-source-permission'))
        self.assertEqual(self.client.post(BASE, self.command(), format='json').status_code, 403)
        self.client.force_authenticate(self.actor)
        for field in ['confirmationDecision', 'confirmed', 'policyApproved', 'skipConfirmation']:
            body = {**self.command(), field: True}
            response = self.client.post(BASE, body, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(WorkflowJob.objects.exists())
