"""Verify both existing schema histories converge without losing their data."""
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class IntegrationMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def verify_upgrade(self, erp_leaf, quotation_leaf, has_machine):
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        other = [node for node in leaves if node[0] not in {'erp', 'quotations'}]
        original = [('erp', erp_leaf), ('quotations', quotation_leaf)] + other
        try:
            # Earlier migration tests may leave only one branch applied.
            self.migrate(leaves)
            apps = self.migrate(original)
            actor = apps.get_model('accounts', 'User').objects.create(username='integration-history', password='!')
            payload = {'status': 'Draft', 'items': [], 'legacy': {'preserved': True}}
            order = apps.get_model('erp', 'WorkflowOrder').objects.create(
                request_id=uuid4(), created_by=actor, data=payload)
            if has_machine:
                machine = apps.get_model('erp', 'ProductionMachine').objects.create(
                    name='Existing machine', data={'active': True, 'processIds': ['1']})
            else:
                revision = apps.get_model('erp', 'WorkflowOrderRevision').objects.create(
                    order=order, revision=1, content={'items': []}, content_hash='a' * 64,
                    captured_by=actor, reason='Existing evidence', provenance='Contemporaneous')
            merged = self.migrate(leaves)
            self.assertEqual(merged.get_model('erp', 'WorkflowOrder').objects.get(pk=order.pk).data, payload)
            if has_machine:
                saved = merged.get_model('erp', 'ProductionMachine').objects.get(pk=machine.pk)
                self.assertEqual(saved.data, machine.data)
                self.assertIsNone(merged.get_model('erp', 'WorkflowOrder').objects.get(pk=order.pk).control_version)
            else:
                saved = merged.get_model('erp', 'WorkflowOrderRevision').objects.get(pk=revision.pk)
                self.assertEqual(saved.content_hash, revision.content_hash)
                self.assertEqual(saved.content, revision.content)
                self.assertEqual(merged.get_model('erp', 'ProductionMachine').objects.count(), 0)
        finally:
            self.migrate(leaves)

    def test_upgrade_from_main_preserves_machine_and_order(self):
        self.verify_upgrade('0013_productionmachine', '0005_quotationdraft', True)

    def test_upgrade_from_task03a_preserves_evidence_and_order(self):
        self.verify_upgrade('0013_inactive_control_foundation', '0006_inactive_control_foundation', False)
