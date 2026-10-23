"""Inactive Task 03A schema tests. Run only through test_baseline_runner.py."""
from copy import deepcopy
from datetime import date
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from quotations.models import Quotation
from .grn import GRNSerializer
from .models import (ApprovalDecision, CommandReceipt, ContributorEvidence,
                     GoodsReceivedNote, PurchaseOrder, PurchaseOrderRevision,
                     WorkflowJob, WorkflowJobRevision, WorkflowOrder, WorkflowOrderRevision)
from .purchases import PurchaseSerializer


EVIDENCE = (WorkflowOrderRevision, WorkflowJobRevision, PurchaseOrderRevision,
            ContributorEvidence, ApprovalDecision, CommandReceipt)


class ControlFoundationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser('control-schema-test')
        self.order = WorkflowOrder.objects.create(request_id=uuid4(), data={'status': 'Draft', 'items': [], 'legacy': {'unchanged': True}}, created_by=self.actor)
        self.job = WorkflowJob.objects.create(order=self.order, data={'workflowVersion': 1, 'legacy': []})
        self.po = PurchaseOrder.objects.create(request_id=uuid4(), supplier_name='Synthetic supplier', order_date=date(2026, 10, 1), currency='USD', created_by=self.actor, status='partially_received', receipt_method='grn')
        self.grn = GoodsReceivedNote.objects.create(request_id=uuid4(), purchase_order=self.po, receipt_date=date(2026, 10, 2), supplier_name='Synthetic supplier', received_by=self.actor, receiver_name='Synthetic receiver')
        self.quote = Quotation.objects.create(source_id='synthetic', fingerprint='a' * 64, document={'legacy': ['approved is not inferred']}, created_by=self.actor)
        self.revision_data = dict(content={'items': [], 'route': ['Printing', 'Gusseting']}, content_hash='a' * 64, captured_by=self.actor, reason='Synthetic schema evidence', provenance='Contemporaneous')
        self.revision = WorkflowOrderRevision.objects.create(order=self.order, revision=1, **self.revision_data)

    def assert_db_rejects(self, model, **values):
        with self.assertRaises(IntegrityError), transaction.atomic():
            # bulk_create deliberately bypasses save guards to exercise the database.
            model.objects.bulk_create([model(**values)])
            connection.check_constraints()

    def decision(self, **changes):
        values = dict(workflow_order=self.order, snapshot={'synthetic': True}, target_version=1, content_hash='b' * 64,
                      checker=self.actor, action='synthetic-test', outcome='Approved', reason='Schema test only',
                      policy_evidence_reference='synthetic-only/not-an-adopted-policy', policy_evidence_hash='c' * 64)
        values.update(changes)
        return values

    def receipt(self, **changes):
        values = dict(command_id=uuid4(), actor=self.actor, action='synthetic-test', schema_version=1, payload_digest='d' * 64)
        values.update(changes)
        return values

    def test_legacy_controls_stay_null_and_old_payloads_are_unchanged(self):
        for row, names in [(self.order, ('control_version', 'commercial_status', 'current_revision_id', 'confirmed_revision_id')),
                           (self.job, ('source_confirmed_revision_id',)),
                           (self.po, ('control_version', 'approval_status', 'current_revision_id', 'approved_revision_id')),
                           (self.grn, ('control_version', 'verification_status')), (self.quote, ('control_version',))]:
            row.refresh_from_db()
            self.assertTrue(all(getattr(row, name) is None for name in names))
        self.assertEqual(self.job.version, 1)
        self.assertEqual(self.po.status, 'partially_received')
        self.assertEqual(self.po.receipt_method, 'grn')
        for result in [PurchaseSerializer(self.po).data, GRNSerializer(self.grn).data]:
            self.assertTrue({'control_version', 'approval_status', 'verification_status', 'current_revision', 'approved_revision'}.isdisjoint(result))
        client = APIClient()
        client.force_authenticate(self.actor)
        self.assertEqual(client.get('/api/production-workflow/orders/').data, [self.order.data])
        self.assertEqual(client.get('/api/production-workflow/jobs/').data, [self.job.data])
        result = client.get(f'/api/quotations/{self.quote.pk}/').data
        self.assertNotIn('control_version', result)
        self.assertFalse(result['approved'])
        self.assertEqual(result['document'], self.quote.document)

    def test_revision_identity_hash_provenance_and_reason_constraints(self):
        self.assert_db_rejects(WorkflowOrderRevision, order=self.order, revision=1, **self.revision_data)
        for change in [{'revision': 0}, {'content_hash': 'not-a-hash'}, {'reason': ' \t'}, {'provenance': 'InventedApproval'}]:
            values = dict(self.revision_data, order=self.order, revision=2)
            values.update(change)
            self.assert_db_rejects(WorkflowOrderRevision, **values)
        purchase_revision = PurchaseOrderRevision.objects.create(order=self.po, revision=1, **self.revision_data)
        self.assert_db_rejects(PurchaseOrderRevision, order=self.po, revision=1, **self.revision_data)
        job_values = dict(self.revision_data, job=self.job, source_order_revision=self.revision, job_version=1)
        WorkflowJobRevision.objects.create(**job_values)
        self.assert_db_rejects(WorkflowJobRevision, **job_values)
        self.assertIsNotNone(purchase_revision.pk)

    def test_all_immutable_evidence_rejects_instance_queryset_and_bulk_changes(self):
        rows = [self.revision,
                PurchaseOrderRevision.objects.create(order=self.po, revision=1, **self.revision_data),
                WorkflowJobRevision.objects.create(job=self.job, source_order_revision=self.revision, job_version=1, **self.revision_data),
                ContributorEvidence.objects.create(event_id=uuid4(), order_revision=self.revision, actor=self.actor, action='create'),
                ApprovalDecision.objects.create(**self.decision())]
        for row in rows:
            with self.subTest(model=type(row).__name__):
                old = deepcopy(type(row).objects.values().get(pk=row.pk))
                with self.assertRaises(ValidationError):
                    row.save()
                with self.assertRaises(ValidationError):
                    row.delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).update(pk=row.pk)
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_update([row], ['action' if hasattr(row, 'action') else 'reason'])
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_create([row], update_conflicts=True, update_fields=['id'], unique_fields=['id'])
                self.assertEqual(type(row).objects.values().get(pk=row.pk), old)
        replacement = WorkflowOrderRevision(pk=self.revision.pk, order=self.order, revision=2, **self.revision_data)
        with self.assertRaises(ValidationError):
            replacement.save()

    def test_contributor_event_identity_and_exactly_one_revision(self):
        values = dict(event_id=uuid4(), actor=self.actor, action='edit')
        self.assert_db_rejects(ContributorEvidence, **values)
        purchase_revision = PurchaseOrderRevision.objects.create(order=self.po, revision=1, **self.revision_data)
        self.assert_db_rejects(ContributorEvidence, order_revision=self.revision, purchase_revision=purchase_revision, **values)
        ContributorEvidence.objects.create(order_revision=self.revision, **values)
        self.assert_db_rejects(ContributorEvidence, order_revision=self.revision, **values)

    def test_decisions_require_one_typed_target_and_policy_evidence(self):
        self.assert_db_rejects(ApprovalDecision, **self.decision(workflow_order=None))
        self.assert_db_rejects(ApprovalDecision, **self.decision(purchase_order=self.po))
        for changes in [{'target_version': 0}, {'policy_evidence_reference': ' '}, {'policy_evidence_hash': ''}, {'reason': ''}, {'outcome': 'Unknown'}]:
            self.assert_db_rejects(ApprovalDecision, **self.decision(**changes))
        for target, row in [('workflow_order', self.order), ('workflow_job', self.job), ('purchase_order', self.po), ('goods_received_note', self.grn), ('quotation', self.quote)]:
            values = self.decision(workflow_order=None)
            values[target] = row
            ApprovalDecision.objects.create(**values)

    def test_protected_parents_actors_revisions_and_invalid_foreign_keys(self):
        self.order.current_revision = self.revision
        self.order.save(update_fields=['current_revision'])
        self.job.source_confirmed_revision = self.revision
        self.job.save(update_fields=['source_confirmed_revision'])
        for row in [self.actor, self.order]:
            with self.assertRaises(ProtectedError):
                row.delete()
        with self.assertRaises(ValidationError):
            self.revision.delete()
        values = dict(self.revision_data, order_id=99999999, revision=1)
        self.assert_db_rejects(WorkflowOrderRevision, **values)

    def test_command_slot_can_complete_once_but_never_publish_missing_target(self):
        with transaction.atomic():
            row = CommandReceipt.objects.create(**self.receipt())
            row.workflow_order = self.order
            row.completed_at = timezone.now()
            row.response_status = 201
            row.response_body = {'historical': 'result'}
            row.save()
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            CommandReceipt.objects.filter(pk=row.pk).update(response_body={})
        with self.assertRaises(ValidationError):
            row.delete()
        self.assert_db_rejects(CommandReceipt, **self.receipt(completed_at=timezone.now(), response_status=200, response_body={}))
        self.assert_db_rejects(CommandReceipt, **self.receipt(workflow_order=self.order, purchase_order=self.po))
        self.assert_db_rejects(CommandReceipt, **self.receipt(workflow_order=self.order, response_status=200))
        self.assert_db_rejects(CommandReceipt, **self.receipt(workflow_order=self.order, completed_at=timezone.now(), response_status=400, response_body={}))
        self.assert_db_rejects(CommandReceipt, **self.receipt(command_id=row.pk))

    def test_command_identity_cannot_change_during_completion(self):
        row = CommandReceipt.objects.create(**self.receipt(workflow_order=self.order))
        row.completed_at, row.response_status, row.response_body = timezone.now(), 200, {}
        row.action = 'changed-action'
        with self.assertRaises(ValidationError):
            row.save()
        row.action = 'synthetic-test'
        row.workflow_order, row.purchase_order = None, self.po
        with self.assertRaises(ValidationError):
            row.save()

    def test_evidence_has_no_admin_registration_or_default_permission_grants(self):
        for model in EVIDENCE:
            self.assertNotIn(model, admin.site._registry)
            self.assertEqual(model._meta.default_permissions, ())


class AdditiveControlMigrationTests(TransactionTestCase):
    unchanged = [('inventory', '0009_procurementplan_procurement_stock_nonnegative'), ('accounts', '0003_seed_role_master')]
    old = [('erp', '0012_workflow_permissions'), ('quotations', '0005_quotationdraft')] + unchanged
    new = [('erp', '0013_inactive_control_foundation'), ('quotations', '0006_inactive_control_foundation')] + unchanged

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_legacy_data_survives_forward_reverse_and_forward_before_evidence(self):
        try:
            apps = self.migrate(self.old)
            actor = apps.get_model('accounts', 'User').objects.create(username='migration-synthetic', password='!')
            order = apps.get_model('erp', 'WorkflowOrder').objects.create(request_id=uuid4(), created_by=actor, data={'entryRequest': {'legacy': [None, 0, 'original']}, 'status': 'Draft'})
            apps.get_model('erp', 'WorkflowJob').objects.create(order=order, data={'workflowVersion': 8, 'original': []}, version=8)
            item = apps.get_model('erp', 'FinishedStockItem').objects.first()
            po = apps.get_model('erp', 'PurchaseOrder').objects.create(request_id=uuid4(), created_by=actor, supplier_name='Synthetic', order_date=date(2026, 10, 1), currency='USD', status='partially_received', receipt_method='grn')
            line = apps.get_model('erp', 'PurchaseOrderLine').objects.create(order=po, description='Synthetic', unit=item.unit, quantity='10', unit_price='2', stock_item=item)
            grn = apps.get_model('erp', 'GoodsReceivedNote').objects.create(request_id=uuid4(), purchase_order=po, receipt_date=date(2026, 10, 2), supplier_name='Synthetic', received_by=actor, receiver_name='Original receiver')
            apps.get_model('erp', 'GoodsReceivedLine').objects.create(note=grn, purchase_line=line, previously_received=0, quantity_received=4, damaged_quantity=1, missing_quantity=2)
            raw = apps.get_model('inventory', 'RawMaterial').objects.first()
            apps.get_model('inventory', 'RawMovement').objects.create(id=str(uuid4()), material=raw, created_by=actor, date=date(2026, 10, 2), movement_type='IN', quantity='3.125', reference='original synthetic receipt')
            quote = apps.get_model('quotations', 'Quotation').objects.create(created_by=actor, source_id='synthetic', fingerprint='a'*64, document={'original': {'totals': ['12.50']}}, approved_by=actor, approved_at=timezone.now())
            apps.get_model('quotations', 'Delivery').objects.create(quotation=quote, request_id=uuid4(), channel='email', recipient='synthetic@example.invalid', status='accepted', created_by=actor)
            labels = [('erp', name) for name in ['WorkflowOrder', 'WorkflowJob', 'PurchaseOrder', 'PurchaseOrderLine', 'GoodsReceivedNote', 'GoodsReceivedLine', 'FinishedStockItem', 'ProductionModelRevision']]
            labels += [('inventory', name) for name in ['RawMaterial', 'RawMovement', 'ProcurementPlan', 'ProcurementDelivery', 'ProcurementPlanRevision']]
            labels += [('quotations', name) for name in ['Quotation', 'QuotationDraft', 'Delivery']]
            fields = {key: [f.attname for f in apps.get_model(*key)._meta.concrete_fields] for key in labels}

            def snapshot(registry):
                return {key: list(registry.get_model(*key).objects.order_by('pk').values(*fields[key])) for key in labels}

            original = snapshot(apps)
            forward = self.migrate(self.new)
            self.assertEqual(snapshot(forward), original)
            for model in EVIDENCE:
                self.assertEqual(forward.get_model('erp', model.__name__).objects.count(), 0)
            for name, controls in [('WorkflowOrder', ['control_version', 'commercial_status', 'confirmed_revision_id', 'current_revision_id']), ('WorkflowJob', ['source_confirmed_revision_id']), ('PurchaseOrder', ['control_version', 'approval_status', 'current_revision_id', 'approved_revision_id']), ('GoodsReceivedNote', ['control_version', 'verification_status'])]:
                for values in forward.get_model('erp', name).objects.values(*controls):
                    self.assertTrue(all(value is None for value in values.values()))
            self.assertIsNone(forward.get_model('quotations', 'Quotation').objects.get(pk=quote.pk).control_version)
            reversed_apps = self.migrate(self.old)
            self.assertEqual(snapshot(reversed_apps), original)
            recovered = self.migrate(self.new)
            self.assertEqual(snapshot(recovered), original)
        finally:
            self.migrate(self.new)
