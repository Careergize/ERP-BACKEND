from copy import deepcopy
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import RawMovement
from .models import (CommandReceipt, ContributorEvidence, FinishedStockItem,
                     ProductionModelRevision, WorkflowJob, WorkflowOrder, WorkflowOrderRevision)
from .order_control_foundation import capture_snapshot, verify_snapshot


BASE = '/api/production-workflow/order-drafts/'


class OrderDraftTests(TestCase):
    def setUp(self):
        self.actor = self.user('draft-maker', 'add_workfloworder', 'change_workfloworder', 'view_workfloworder')
        self.editor = self.user('draft-editor', 'add_workfloworder', 'change_workfloworder', 'view_workfloworder')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        for key, unit in (('draft-boxes', 'ctn'), ('draft-bags', 'kg')):
            FinishedStockItem.objects.create(id=key, name=key, unit=unit, category='packing',
                opening_stock=123, opening_date=date(2026, 10, 1))
        self.content = {'customerName': 'Synthetic customer', 'customerId': 'synthetic-source-id',
            'date': '2026-10-22', 'marketingPersonName': 'Synthetic sales', 'customerType': 'B2B',
            'currency': 'USD', 'taxRate': 18, 'quotationId': 'source-quote', 'quotationRevision': 2,
            'commercialPricing': {'taxMode': 'exclusive', 'discount': 1},
            'items': [{'id': 'line-1', 'itemId': 'draft-boxes', 'itemName': 'Boxes', 'unit': 'ctn',
                       'quantity': '2.50', 'unitPrice': '4.00', 'fulfillmentSource': 'stock'},
                      {'id': 'line-2', 'itemId': 'draft-bags', 'itemName': 'Bags', 'unit': 'kg',
                       'quantity': 3, 'unitPrice': 2, 'fulfillmentSource': 'stock'}]}
        self.body = self.command(0)
        self.stock_before = list(FinishedStockItem.objects.values())
        self.movements_before = list(RawMovement.objects.values())

    def user(self, name, *permissions):
        actor = get_user_model().objects.create_user(name)
        actor.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=permissions))
        return actor

    def command(self, version, content=None):
        return {'commandId': str(uuid4()), 'expectedVersion': version, 'reason': 'Synthetic Draft save',
                'evidenceIds': [], 'content': deepcopy(self.content if content is None else content)}

    def create(self):
        result = self.client.post(BASE, self.body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        return result, WorkflowOrder.objects.get(request_id=self.body['commandId'])

    def edit_url(self, order):
        return f'{BASE}{order.pk}/'

    def assert_error(self, result, code, status=409):
        self.assertEqual(result.status_code, status, result.data)
        self.assertEqual(result.data['code'], code)
        self.assertNotIn('currentVersion', result.data)

    def counts(self):
        return tuple(model.objects.count() for model in
                     (WorkflowOrder, WorkflowOrderRevision, ContributorEvidence, CommandReceipt))

    def test_multi_product_create_reopen_and_history_without_side_effects(self):
        response, order = self.create()
        self.assertEqual(response.data['controlVersion'], 1)
        self.assertEqual(response.data['items'], self.content['items'])
        self.assertEqual(response.data['quotationId'], 'source-quote')
        self.assertEqual(response.data['orderTotal'], 17.70)
        fresh = APIClient(); fresh.force_authenticate(self.actor)
        self.assertEqual(fresh.get(self.edit_url(order)).data, response.data)
        revision = order.current_revision
        self.assertEqual(verify_snapshot(revision.content, revision.content_hash).decode()['items'], self.content['items'])
        history = fresh.get(self.edit_url(order) + 'history/')
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data['revisions'][0]['content'], revision.content)
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertEqual(list(FinishedStockItem.objects.values()), self.stock_before)
        self.assertEqual(list(RawMovement.objects.values()), self.movements_before)
        receipt = CommandReceipt.objects.get()
        self.assertEqual(receipt.actor_id, self.actor.pk)
        self.assertEqual(receipt.workflow_order_id, order.pk)
        self.assertEqual(receipt.response_body, response.data)
        self.assertIsNotNone(receipt.completed_at)

    def test_edit_appends_revision_inherits_contributors_and_preserves_original(self):
        original, order = self.create()
        first = deepcopy(order.current_revision.content)
        content = deepcopy(self.content); content['items'][0]['quantity'] = '5.25'
        self.client.force_authenticate(self.editor)
        result = self.client.post(self.edit_url(order), self.command(1, content), format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['controlVersion'], 2)
        order.refresh_from_db()
        self.assertEqual(order.current_revision.revision, 2)
        revisions = list(order.control_revisions.order_by('revision'))
        self.assertEqual(revisions[0].content, first)
        self.assertEqual(set(revisions[1].contributors.values_list('actor_id', flat=True)), {self.actor.pk, self.editor.pk})
        self.assertEqual(result.data['entryRequest'], original.data['entryRequest'])
        self.assertEqual(order.created_by_id, self.actor.pk)
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertEqual(list(FinishedStockItem.objects.values()), self.stock_before)
        self.assertEqual(list(RawMovement.objects.values()), self.movements_before)
        for revision in revisions:
            verify_snapshot(revision.content, revision.content_hash)
            with self.assertRaises(ValidationError):
                revision.save()
        self.assertEqual(len(self.client.get(self.edit_url(order) + 'history/').data['revisions']), 2)

    def test_create_and_edit_exact_retries_replay_original_status_body(self):
        first, order = self.create()
        replay = self.client.post(BASE, self.body, format='json')
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.content, first.content)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        command = self.command(1)
        edit = self.client.post(self.edit_url(order), command, format='json')
        replay = self.client.post(self.edit_url(order), command, format='json')
        self.assertEqual(edit.status_code, 200, edit.data)
        self.assertEqual(replay.content, edit.content)
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        # Original create receipt is stable even after later edits.
        self.assertEqual(self.client.post(BASE, self.body, format='json').content, first.content)
        self.assertEqual(order.control_revisions.count(), 2)
        self.assertEqual(CommandReceipt.objects.count(), 2)

    def test_changed_payload_actor_action_and_target_conflict(self):
        _, order = self.create()
        before = self.counts()
        changed = deepcopy(self.body); changed['content']['customerName'] = 'Changed'
        self.assert_error(self.client.post(BASE, changed, format='json'), 'COMMAND_PAYLOAD_CONFLICT')
        changed = deepcopy(self.body); changed['reason'] += ' changed'
        self.assert_error(self.client.post(BASE, changed, format='json'), 'COMMAND_PAYLOAD_CONFLICT')
        self.client.force_authenticate(self.editor)
        self.assert_error(self.client.post(BASE, self.body, format='json'), 'COMMAND_PAYLOAD_CONFLICT')
        self.client.force_authenticate(self.actor)
        edit = self.command(1); edit['commandId'] = self.body['commandId']
        self.assert_error(self.client.post(self.edit_url(order), edit, format='json'), 'COMMAND_PAYLOAD_CONFLICT')
        self.assertEqual(self.counts(), before)

    def test_stale_and_strict_versions_leave_no_receipts(self):
        _, order = self.create()
        self.assertEqual(self.client.post(self.edit_url(order), self.command(1), format='json').status_code, 200)
        before = self.counts()
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'VERSION_CONFLICT')
        for version in (True, '2', 2.0, -1, 0, None, 2**63):
            self.assert_error(self.client.post(self.edit_url(order), self.command(version), format='json'),
                              'VALIDATION_ERROR', 400)
        self.assertEqual(self.counts(), before)

    def test_unknown_fields_and_invalid_quantities_rejected_without_partial_evidence(self):
        wrong = []
        for key, value in (('status', 'Confirmed'), ('allocateJobCard', True), ('createdBy', self.editor.pk)):
            body = deepcopy(self.body); body['content'][key] = value; wrong.append(body)
        for quantity in (0, -1, True, 'NaN', 'Infinity', '999999999999999', {}, None):
            body = deepcopy(self.body); body['content']['items'][0]['quantity'] = quantity; wrong.append(body)
        for key, value in (('unknown', 1), ('unit', 'pcs'), ('itemId', 'missing'), ('unitPrice', -1)):
            body = deepcopy(self.body); body['content']['items'][0][key] = value; wrong.append(body)
        body = deepcopy(self.body); body['content']['commercialPricing']['unknown'] = 1; wrong.append(body)
        for items in (None, {}, ['bad'], [], [deepcopy(self.content['items'][0])] * 2):
            body = deepcopy(self.body); body['content']['items'] = items; wrong.append(body)
        for body in wrong:
            with self.subTest(body=body):
                self.assert_error(self.client.post(BASE, body, format='json'), 'VALIDATION_ERROR', 400)
                self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_duplicate_json_keys_rejected_at_all_depths(self):
        for body in ('{"commandId":"a","commandId":"b"}', '{"content":{"items":[],"items":[]}}'):
            self.assert_error(self.client.post(BASE, body, content_type='application/json'), 'VALIDATION_ERROR', 400)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_rollback_after_evidence_and_root_write_on_create_and_edit(self):
        # Failure completing receipt occurs after all domain/evidence writes.
        original_save = CommandReceipt.save
        def fail_completion(receipt, *args, **kwargs):
            if receipt.completed_at:
                raise RuntimeError('Synthetic completion failure')
            return original_save(receipt, *args, **kwargs)
        with patch.object(CommandReceipt, 'save', fail_completion), self.assertRaises(RuntimeError):
            self.client.post(BASE, self.body, format='json')
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        _, order = self.create()
        before = self.counts(); old_data = deepcopy(order.data)
        with patch.object(CommandReceipt, 'save', fail_completion), self.assertRaises(RuntimeError):
            self.client.post(self.edit_url(order), self.command(1), format='json')
        order.refresh_from_db()
        self.assertEqual(self.counts(), before)
        self.assertEqual(order.control_version, 1)
        self.assertEqual(order.data, old_data)

    def test_wrong_parent_revision_rejected(self):
        _, order = self.create()
        self.body = self.command(0)
        _, other = self.create()
        order.current_revision = other.current_revision
        order.save(update_fields=['current_revision'])
        before = self.counts()
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'REVISION_CONFLICT')
        self.assert_error(self.client.get(self.edit_url(order)), 'REVISION_CONFLICT')
        self.assertEqual(self.counts(), before)

    def test_hash_or_root_content_tampering_blocks_edit(self):
        _, order = self.create()
        order.data['items'][0]['quantity'] = 99
        order.save(update_fields=['data'])
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'REVISION_CONFLICT')
        # Insert deliberately invalid hash in synthetic evidence, not an operational overwrite.
        snapshot = capture_snapshot({'items': []})
        revision = WorkflowOrderRevision.objects.create(order=order, revision=2, content=snapshot.decode(),
            content_hash='0' * 64, captured_by=self.actor, reason='Synthetic corruption', provenance='Contemporaneous')
        order.current_revision = revision; order.save(update_fields=['current_revision'])
        self.assert_error(self.client.get(self.edit_url(order)), 'REVISION_CONFLICT')

    def test_legacy_unknown_version_is_readable_and_never_initialised(self):
        order = WorkflowOrder.objects.create(request_id=uuid4(), created_by=self.actor,
                                            data={'status': 'Draft', 'items': []})
        response = self.client.get(self.edit_url(order))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['controlVersion'])
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'LEGACY_REVIEW_REQUIRED')
        order.refresh_from_db()
        self.assertIsNone(order.control_version)
        self.assertEqual(self.counts(), (1, 0, 0, 0))

    def test_non_draft_or_allocated_orders_not_editable(self):
        _, order = self.create()
        order.commercial_status = 'InReview'; order.save(update_fields=['commercial_status'])
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'ORDER_NOT_EDITABLE')
        order.commercial_status = 'Draft'; order.save(update_fields=['commercial_status'])
        WorkflowJob.objects.create(order=order, data={})
        self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'ORDER_NOT_EDITABLE')
        self.assertEqual(CommandReceipt.objects.count(), 1)

    def test_unauthorised_access_and_revoked_replay(self):
        _, order = self.create()
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(BASE, self.body, format='json').status_code, 401)
        self.assertEqual(self.client.get(self.edit_url(order)).status_code, 401)
        for actor in (self.user('display-admin'), self.user('reader-only', 'view_workfloworder')):
            self.client.force_authenticate(actor)
            self.assert_error(self.client.post(BASE, self.body, format='json'), 'PERMISSION_DENIED', 403)
            self.assert_error(self.client.post(self.edit_url(order), self.command(1), format='json'), 'PERMISSION_DENIED', 403)
        self.client.force_authenticate(self.user('no-visibility', 'add_workfloworder'))
        self.assert_error(self.client.get(self.edit_url(order)), 'PERMISSION_DENIED', 403)
        self.assert_error(self.client.get(self.edit_url(order) + 'history/'), 'PERMISSION_DENIED', 403)
        self.actor.is_active = False; self.actor.save(update_fields=['is_active'])
        self.client.force_authenticate(self.actor)
        # JWT enforcement may reject inactive users before the view.
        self.assertIn(self.client.post(BASE, self.body, format='json').status_code, (401, 403))

    def test_legacy_creation_key_collision_does_not_initialise_or_modify_row(self):
        order = WorkflowOrder.objects.create(request_id=self.body['commandId'], data={'legacy': True}, created_by=self.actor)
        self.assert_error(self.client.post(BASE, self.body, format='json'), 'COMMAND_PAYLOAD_CONFLICT')
        order.refresh_from_db()
        self.assertEqual(order.data, {'legacy': True})
        self.assertEqual(self.counts(), (1, 0, 0, 0))

    def test_manufacturing_source_revision_units_and_quantity_preserved(self):
        from inventory.models import RawMaterial
        raw = RawMaterial.objects.create(id='draft-raw', name='Synthetic raw', family='Test', category='virgin',
            unit='kg', opening_stock=100, opening_date=date(2026, 10, 1))
        model = ProductionModelRevision.objects.create(model_id='draft-model', revision=1,
            data={'name': 'Synthetic model', 'productionBatchQuantity': 10, 'productionBatchUnit': 'ctn',
                  'recipe': [{'materialId': raw.pk, 'unit': 'kg', 'quantity': 5}],
                  'routing': [{'name': 'Packing', 'department': 'Production', 'inputUnit': 'kg',
                               'outputUnit': 'ctn', 'wasteUnit': 'kg'}]})
        line = self.body['content']['items'][0]
        line.update(fulfillmentSource='manufacturing', modelId=model.model_id, modelRevision=1, requiredDate='2026-10-24')
        result, order = self.create()
        self.assertEqual(result.data['items'][0], line)
        wrong = self.command(1, self.body['content']); wrong['content']['items'][0]['unit'] = 'pcs'
        self.assert_error(self.client.post(self.edit_url(order), wrong, format='json'), 'VALIDATION_ERROR', 400)
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertEqual(list(RawMovement.objects.values()), self.movements_before)
