from decimal import Decimal
from uuid import uuid4
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SavedDeliveryMigrationTests(TransactionTestCase):
    def test_saved_plan_and_delivery_survive_forward_and_reverse_migration(self):
        before = [('inventory', '0004_procurementdelivery_procurementplanrevision_and_more')]
        after = [('inventory', '0006_remove_procurementplan_deliveries')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            user = old.get_model('accounts', 'User').objects.create(username='migration-owner')
            material = old.get_model('inventory', 'RawMaterial').objects.create(
                id='migration-grade', name='Preserved exact grade', category='virgin', family='HDPE', unit='kg',
                opening_stock=20, opening_date='2026-10-01')
            delivery_id = str(uuid4())
            delivery = {'id': delivery_id, 'supplier': 'Existing supplier', 'date': '2026-12-01', 'quantity': 12.345678}
            old.get_model('inventory', 'ProcurementPlan').objects.create(
                material_id=material.pk, updated_by_id=user.pk, monthly=20, days=26, lead=75,
                comment='Keep this comment', version=4, deliveries=[delivery])
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            current = executor.loader.project_state(after).apps
            row = current.get_model('inventory', 'ProcurementDelivery').objects.get(pk=delivery_id)
            self.assertEqual(row.quantity, Decimal('12.345678'))
            self.assertEqual(row.plan_id, material.pk)
            self.assertEqual(row.supplier, 'Existing supplier')
            snapshot = current.get_model('inventory', 'ProcurementPlanRevision').objects.get(plan_id=material.pk, version=4).snapshot
            self.assertEqual(snapshot['deliveries'], [delivery])
            self.assertEqual(snapshot['comment'], 'Keep this comment')
            # Reverse restores the JSON from saved relational rows; reapplying is safe.
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            restored = executor.loader.project_state(before).apps.get_model('inventory', 'ProcurementPlan').objects.get(pk=material.pk)
            self.assertEqual(restored.deliveries, [delivery])
            MigrationExecutor(connection).migrate(after)
        finally:
            MigrationExecutor(connection).migrate(latest)
