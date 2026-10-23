from copy import deepcopy
from datetime import date
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.response import Response
from rest_framework.exceptions import APIException
from rest_framework import serializers
from inventory.models import RawMaterial, RawMovement
from .models import (WorkflowOrder, WorkflowOrderRevision, WorkflowJob, WorkflowJobRevision,
                     ProductionModelRevision, ContributorEvidence, CommandReceipt)
from .order_control_foundation import capture_snapshot
from .order_control_foundation import OrderControlError
from .order_drafts import DraftError
from .job_drafts import _save_preparation
from .confirmation_test_fixtures import confirmation_rows

BASE = '/api/production-workflow/job-drafts/'


class JobDraftTests(TestCase):
    def setUp(self):
        self.actor = self.user('job-maker')
        self.editor = self.user('job-editor')
        self.checker = self.user('synthetic-source-checker')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        RawMaterial.objects.create(id='synthetic-raw', name='Synthetic resin', unit='kg',
            category='virgin', family='Synthetic', opening_stock=100, opening_date=date(2026, 10, 1))
        self.model = ProductionModelRevision.objects.create(model_id='synthetic-model', revision=1,
            data={'name': 'Synthetic bag', 'productionBatchQuantity': 10, 'productionBatchUnit': 'ctn',
                  'recipeMode': 'quantities', 'recipe': [{'materialId': 'synthetic-raw', 'unit': 'kg', 'quantity': 5}],
                  'routing': [{'name': 'Synthetic process', 'department': 'Production', 'required': True,
                               'inputUnit': 'kg', 'outputUnit': 'ctn', 'wasteUnit': 'kg'}]})
        self.order = self.fixture()
        self.receipts_before = list(CommandReceipt.objects.values())

    def user(self, name):
        user = get_user_model().objects.create_user(name)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=[
            'view_workfloworder', 'view_workflowjob', 'add_workflowjob', 'change_workflowjob',
            'approve_workflowjob', 'issue_workflowjob']))
        return user

    def fixture(self):
        content = {'customerName': 'Synthetic customer', 'date': '2026-10-22', 'orderTotal': 100,
            'items': [{'id': 'line-' + str(i), 'itemName': 'Synthetic product ' + str(i),
                       'quantity': str(q), 'unit': 'ctn', 'fulfillmentSource': 'manufacturing',
                       'modelId': self.model.model_id, 'modelRevision': 1, 'requiredDate': '2026-10-25'}
                      for i, q in enumerate([20, 30])]}
        order = WorkflowOrder.objects.create(request_id=uuid4(), created_by=self.actor, control_version=3,
            commercial_status='Confirmed', data={})
        snap = capture_snapshot(content)
        revision = WorkflowOrderRevision.objects.create(order=order, revision=1, content=content,
            content_hash=snap.digest, captured_by=self.actor, reason='Synthetic confirmed fixture only',
            provenance='Contemporaneous')
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=revision,
            actor=self.actor, action='Synthetic maker')
        order.current_revision = order.confirmed_revision = revision
        order.data = {**content, 'id': f'wf-order-{order.pk}', 'orderNumber': f'SYN-{order.pk}',
                      'status': 'Confirmed', 'jobCardIds': []}
        order.save()
        confirmation_rows(order, self.checker)
        return order

    def command(self, version=0):
        return {'commandId': str(uuid4()), 'expectedVersion': version, 'reason': 'Synthetic correction',
            'evidenceIds': [], 'orderId': self.order.pk, 'expectedOrderVersion': self.order.control_version,
            'sourceConfirmedRevision': self.order.confirmed_revision_id, 'corrections': {}}

    def post(self, body=None, pk=None):
        # Direct private-engine unit tests preserve persistence/rollback coverage.
        # They cannot certify operational eligibility: HTTP is tested separately.
        actor = self.client.handler._force_user
        self.assertIsNotNone(actor)  # Authentication is tested against real HTTP below.
        try:
            data, status, replay = _save_preparation(actor, body or self.command(), pk)
            return Response(data, status=status, headers={'Idempotency-Replayed': str(replay).lower()})
        except OrderControlError as error:
            error = DraftError(error.code, 400 if error.code == 'VALIDATION_ERROR' else 409)
            return Response(error.detail, status=error.status_code)
        except serializers.ValidationError:
            error = DraftError('VALIDATION_ERROR', 400)
            return Response(error.detail, status=400)
        except APIException as error:
            return Response(error.detail, status=error.status_code)

    def create(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        return WorkflowJob.objects.get(), response

    def error(self, response, code, status=409):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data['code'], code)

    def test_operational_confirmation_guard_and_draft_rejection(self):
        self.error(self.client.post(BASE, self.command(), format='json'), 'AUTHORITY_NOT_CONFIGURED')
        self.order.commercial_status = 'Draft'
        self.order.data['status'] = 'Draft'
        self.order.save()
        self.error(self.post(), 'ORDER_CONFIRMATION_REQUIRED')
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertEqual(list(CommandReceipt.objects.values()), self.receipts_before)

    def test_multi_product_materials_source_history_and_no_stock(self):
        before = list(RawMaterial.objects.values())
        movements = list(RawMovement.objects.values())
        job, response = self.create()
        self.assertEqual(job.data['status'], 'Draft')
        self.assertEqual([c['requiredMaterials'][0]['requiredQuantity'] for c in job.data['manufacturingItems']], [10, 15])
        self.assertEqual(job.data['sourceOrderSnapshot'], self.order.confirmed_revision.content)
        self.assertEqual(response.data['sourceConfirmedRevision'], self.order.confirmed_revision_id)
        self.assertFalse(response.data['capabilities']['approve'])
        self.assertFalse(response.data['capabilities']['issue'])
        self.assertEqual(self.client.get(f'{BASE}{job.pk}/').status_code, 200)
        history = self.client.get(f'{BASE}{job.pk}/history/').data['revisions']
        self.assertEqual(history[0]['contributors'], [self.actor.pk])
        self.assertEqual(history[0]['content'], job.data)
        self.assertEqual(list(RawMaterial.objects.values()), before)
        self.assertEqual(list(RawMovement.objects.values()), movements)
        for action in ['approve', 'issue', 'edit']:
            result = self.client.post(f'/api/production-workflow/jobs/{job.pk}/{action}/',
                {'expectedVersion': 1}, format='json')
            self.assertEqual(result.status_code, 409, result.data)

    def test_edit_contributors_stale_replay_and_payload_binding(self):
        body = self.command()
        first = self.post(body)
        job = WorkflowJob.objects.get()
        self.client.force_authenticate(self.editor)
        edit = self.command(1)
        edit['corrections'] = {'line-0': {'specifications': 'Reviewed specification'}}
        saved = self.post(edit, job.pk)
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['workflowVersion'], 2)
        self.assertEqual(set(job.control_revisions.get(job_version=2).contributors.values_list('actor_id', flat=True)),
                         {self.actor.pk, self.editor.pk})
        from .job_drafts import validate_review_preparation
        from .order_drafts import DraftError
        job.refresh_from_db()
        with self.assertRaises(DraftError) as error:
            validate_review_preparation(self.editor, job)
        self.assertEqual(error.exception.detail['code'], 'SELF_APPROVAL_FORBIDDEN')
        self.error(self.post(self.command(1), job.pk), 'VERSION_CONFLICT')
        replay = self.client.post(f'{BASE}{job.pk}/', edit, format='json')
        self.assertEqual(replay.data, saved.data)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        edit['reason'] = 'Changed'
        self.error(self.post(edit, job.pk), 'COMMAND_PAYLOAD_CONFLICT')
        self.client.force_authenticate(self.actor)
        replay = self.client.post(BASE, body, format='json')
        self.assertEqual(replay.data, first.data)
        self.assertEqual(WorkflowJob.objects.count(), 1)

    def test_wrong_parent_and_stale_source_rejected(self):
        other = self.fixture()
        self.order.confirmed_revision = other.confirmed_revision
        self.order.save()
        self.error(self.post(), 'REVISION_CONFLICT')
        self.order.confirmed_revision = self.order.current_revision
        self.order.save()
        body = self.command()
        body['sourceConfirmedRevision'] = other.confirmed_revision_id
        self.error(self.post(body), 'SOURCE_REVISION_CHANGED')
        body = self.command()
        body['expectedOrderVersion'] = 2
        self.error(self.post(body), 'VERSION_CONFLICT')
        original = self.order.current_revision
        incomplete = WorkflowOrderRevision.objects.create(order=self.order, revision=2,
            content=original.content, content_hash=original.content_hash, captured_by=self.editor,
            reason='Synthetic incomplete maker evidence', provenance='Contemporaneous')
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=incomplete,
            actor=self.actor, action='Synthetic incomplete source')
        self.order.confirmed_revision = self.order.current_revision = incomplete
        self.order.save()
        self.error(self.post(), 'MAKER_EVIDENCE_REQUIRED')

    def test_rollback_create_and_edit_including_receipt(self):
        original = CommandReceipt.save
        def fail(receipt, *args, **kwargs):
            if receipt.completed_at:
                raise RuntimeError('Synthetic failure')
            return original(receipt, *args, **kwargs)
        with patch.object(CommandReceipt, 'save', fail):
            with self.assertRaises(RuntimeError):
                self.post()
        self.order.refresh_from_db()
        self.assertEqual(self.order.data['jobCardIds'], [])
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertFalse(WorkflowJobRevision.objects.exists())
        self.assertEqual(list(CommandReceipt.objects.values()), self.receipts_before)
        job, _ = self.create()
        before = deepcopy(job.data)
        with patch.object(CommandReceipt, 'save', fail):
            with self.assertRaises(RuntimeError):
                self.post(self.command(1), job.pk)
        job.refresh_from_db()
        self.assertEqual(job.data, before)
        self.assertEqual(job.version, 1)
        self.assertEqual(job.control_revisions.count(), 1)
        self.assertEqual(CommandReceipt.objects.count(), len(self.receipts_before) + 1)

    def test_invalid_corrections_references_and_quantities(self):
        job, _ = self.create()
        for corrections in [{'other-line': {}}, {'line-0': {'qty': 0}}, {'line-0': {'specifications': 4}},
                            {'line-0': {'requiredMaterials': []}}, []]:
            body = self.command(1)
            body['corrections'] = corrections
            self.error(self.post(body, job.pk), 'VALIDATION_ERROR', 400)
        for field, value in [('expectedVersion', True), ('orderId', 'bad'), ('orderId', 2**100), ('evidenceIds', {}),
                             ('unknown', 1), ('sourceConfirmedRevision', False)]:
            body = self.command()
            body[field] = value
            self.error(self.post(body), 'VALIDATION_ERROR', 400)

    def test_unauthorized_and_legacy_unknown(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(BASE, self.command(), format='json').status_code, 401)
        unauthorized = get_user_model().objects.create_user('no-permissions')
        self.client.force_authenticate(unauthorized)
        self.error(self.client.post(BASE, self.command(), format='json'), 'PERMISSION_DENIED', 403)
        job = WorkflowJob.objects.create(order=self.order, data={'status': 'HistoricalUnknown'})
        self.error(self.client.get(f'{BASE}{job.pk}/'), 'PERMISSION_DENIED', 403)
        self.client.force_authenticate(self.actor)
        detail = self.client.get(f'{BASE}{job.pk}/')
        self.assertIsNone(detail.data['workflowVersion'])
        self.assertEqual(detail.data['reviewStatus'], 'LegacyUnknown')
        self.assertEqual(detail.data['status'], 'HistoricalUnknown')
        self.error(self.post(self.command(1), job.pk), 'LEGACY_REVIEW_REQUIRED')

    def test_review_preparation_cannot_approve_maker_or_checker(self):
        from .job_drafts import validate_review_preparation
        from .order_drafts import DraftError
        job, _ = self.create()
        for actor, code in [(self.actor, 'SELF_APPROVAL_FORBIDDEN'), (self.editor, 'AUTHORITY_NOT_CONFIGURED')]:
            with self.assertRaises(DraftError) as error:
                validate_review_preparation(actor, job)
            self.assertEqual(error.exception.detail['code'], code)
        job.refresh_from_db()
        self.assertEqual(job.data['status'], 'Draft')

    def test_duplicate_parent_revoked_replay_and_invalid_source_quantity(self):
        job, _ = self.create()
        self.error(self.post(), 'JOB_ALREADY_EXISTS')
        other = self.fixture()
        content = deepcopy(other.current_revision.content)
        content['items'][0]['quantity'] = 0
        snap = capture_snapshot(content)
        revision = WorkflowOrderRevision.objects.create(order=other, revision=2, content=content,
            content_hash=snap.digest, captured_by=self.actor, reason='Synthetic invalid quantity', provenance='Contemporaneous')
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=revision, actor=self.actor, action='Synthetic source')
        other.confirmed_revision = other.current_revision = revision
        other.data.update(content)
        other.save()
        confirmation_rows(other, self.checker)
        receipt_count = CommandReceipt.objects.count()
        body = self.command()
        body.update(orderId=other.pk, sourceConfirmedRevision=revision.pk)
        self.error(self.post(body), 'VALIDATION_ERROR', 400)
        self.assertEqual(WorkflowJob.objects.count(), 1)
        self.assertEqual(CommandReceipt.objects.count(), receipt_count)
        receipt = CommandReceipt.objects.get(action='job.draft.create')
        self.client.force_authenticate(get_user_model().objects.create_user('revoked'))
        self.error(self.post({**self.command(), 'commandId': str(receipt.pk)}), 'PERMISSION_DENIED', 403)
