from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from .models import RawMaterial, RawMovement, ProcurementPlan, ProcurementDelivery, ProcurementPlanRevision


class InventoryTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('inventory-admin', password='Test-inventory-991!')
        self.client.force_authenticate(self.user)
        self.material = RawMaterial.objects.create(id='test-grade', name='Exact Grade', unit='kg', category='virgin', family='HDPE',
                                                  opening_stock=100, opening_date=timezone.localdate() - timedelta(days=30))
        self.entry = {'id': str(uuid4()), 'materialId': self.material.pk, 'unit': 'kg', 'date': timezone.localdate().isoformat(),
                      'movementType': 'OUT', 'quantity': 3, 'reference': 'Daily use', 'remarks': ''}
        self.plan = {'expected_version': 0, 'monthly': 78, 'days': 26, 'lead': 75, 'comment': 'Saved plan',
                     'deliveries': [{'id': str(uuid4()), 'supplier': 'Supplier', 'date': timezone.localdate().isoformat(), 'quantity': 25}]}

    def test_post_and_reload_from_another_session(self):
        response = self.client.post('/api/raw-inventory/movements/', self.entry, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(RawMovement.objects.get(pk=self.entry['id']).created_by, self.user)
        self.client.post('/api/raw-inventory/movements/', {**self.entry, 'id': str(uuid4()), 'movementType': 'IN', 'quantity': 10}, format='json')
        client = APIClient()
        client.force_authenticate(self.user)
        snapshot = client.get('/api/raw-inventory/').data
        material = next(m for m in snapshot['materials'] if m['id'] == self.material.pk)
        rows = [m for m in snapshot['movements'] if m['materialId'] == self.material.pk]
        self.assertEqual(material['openingStock'] + sum(m['quantity'] * (1 if m['movementType'] == 'IN' else -1) for m in rows), 107)
        self.assertEqual(len(rows), 2)

    def test_retries_do_not_double_post_and_conflicts_fail(self):
        for expected in [201, 200]:
            response = self.client.post('/api/raw-inventory/movements/', self.entry, format='json')
            self.assertEqual(response.status_code, expected, response.data)
        self.assertEqual(RawMovement.objects.filter(material=self.material).count(), 1)
        response = self.client.post('/api/raw-inventory/movements/', {**self.entry, 'quantity': 4}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_invalid_movements_never_change_inventory(self):
        for changes in [{'unit': 'tonnes'}, {'quantity': 101}, {'quantity': 0}, {'quantity': 'NaN'},
                        {'date': (timezone.localdate() + timedelta(days=1)).isoformat()}, {'date': '2000-01-01'}, {'movementType': 'ADJUST'}]:
            result = self.client.post('/api/raw-inventory/movements/', {**self.entry, **changes}, format='json')
            self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(RawMovement.objects.filter(material=self.material).exists())
        self.material.opening_stock = None
        self.material.save()
        self.assertEqual(self.client.post('/api/raw-inventory/movements/', self.entry, format='json').status_code, 400)

    def test_plan_survives_reload_and_prevents_lost_update(self):
        url = f'/api/procurement-plans/{self.material.pk}/'
        response = self.client.put(url, self.plan, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['version'], 1)
        self.assertEqual(self.client.put(url, self.plan, format='json').status_code, 200)
        response = self.client.put(url, {**self.plan, 'monthly': 100}, format='json')
        self.assertEqual(response.status_code, 409)
        response = self.client.put(url, {**self.plan, 'monthly': 100, 'expected_version': 1}, format='json')
        self.assertEqual(response.data['version'], 2)
        client = APIClient()
        client.force_authenticate(self.user)
        saved = next(p for p in client.get('/api/raw-inventory/').data['plans'] if p['materialId'] == self.material.pk)
        self.assertEqual(saved['monthly'], 100)
        self.assertEqual(saved['deliveries'][0]['supplier'], 'Supplier')
        self.assertFalse(RawMovement.objects.filter(material=self.material).exists())

    def test_delivery_fields_and_removed_values_are_retained_in_history(self):
        url = f'/api/procurement-plans/{self.material.pk}/'
        response = self.client.put(url, self.plan, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        delivery = ProcurementDelivery.objects.get(pk=self.plan['deliveries'][0]['id'])
        self.assertEqual(delivery.plan_id, self.material.pk)
        self.assertEqual(delivery.supplier, 'Supplier')
        self.assertEqual(delivery.quantity, Decimal('25'))
        self.assertEqual(delivery.created_by, self.user)
        self.assertEqual(str(delivery.expected_date), self.plan['deliveries'][0]['date'])
        # A retry does not create a delivery or a second revision.
        self.client.put(url, self.plan, format='json')
        self.assertEqual(ProcurementPlanRevision.objects.filter(plan_id=self.material.pk).count(), 1)
        changed = {**self.plan, 'expected_version': 1, 'comment': 'Changed note', 'days': 27, 'lead': 80, 'monthly': 90,
                   'deliveries': [{**self.plan['deliveries'][0], 'quantity': 30, 'supplier': 'Updated supplier'}]}
        response = self.client.put(url, changed, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        delivery.refresh_from_db()
        self.assertEqual(delivery.quantity, Decimal('30'))
        self.assertEqual(delivery.supplier, 'Updated supplier')
        response = self.client.put(url, {**changed, 'expected_version': 2, 'deliveries': []}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(ProcurementDelivery.objects.filter(plan_id=self.material.pk).exists())
        history = list(ProcurementPlanRevision.objects.filter(plan_id=self.material.pk).order_by('version'))
        self.assertEqual([r.version for r in history], [1, 2, 3])
        self.assertEqual(history[0].snapshot['deliveries'][0]['quantity'], 25)
        self.assertEqual(history[1].snapshot['deliveries'][0]['quantity'], 30)
        self.assertEqual(history[1].snapshot['comment'], 'Changed note')
        self.assertEqual(history[2].snapshot['deliveries'], [])
        self.assertTrue(all(r.changed_by == self.user for r in history))

    def test_delivery_cannot_move_between_materials(self):
        self.client.put(f'/api/procurement-plans/{self.material.pk}/', self.plan, format='json')
        other = RawMaterial.objects.exclude(pk=self.material.pk).first()
        result = self.client.put(f'/api/procurement-plans/{other.pk}/', self.plan, format='json')
        self.assertEqual(result.status_code, 409)
        self.assertFalse(ProcurementPlan.objects.filter(material=other).exists())
        self.assertEqual(ProcurementDelivery.objects.get(pk=self.plan['deliveries'][0]['id']).plan_id, self.material.pk)

    def test_reordered_delivery_list_is_an_idempotent_retry(self):
        body = {**self.plan, 'deliveries': [*self.plan['deliveries'], {**self.plan['deliveries'][0], 'id': str(uuid4()), 'supplier': 'Other'}]}
        url = f'/api/procurement-plans/{self.material.pk}/'
        first = self.client.put(url, body, format='json')
        retry = self.client.put(url, {**body, 'deliveries': list(reversed(body['deliveries']))}, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data, retry.data)
        self.assertEqual(ProcurementPlanRevision.objects.filter(plan_id=self.material.pk).count(), 1)

    def test_procurement_manual_stock_never_depletes_or_touches_raw_material(self):
        stock_date = timezone.localdate() - timedelta(days=3)
        body = {**self.plan, 'stock': 100, 'asOfDate': stock_date.isoformat(), 'monthly': 26}
        url = f'/api/procurement-plans/{self.material.pk}/'
        saved = self.client.put(url, body, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['stock'], 100)
        self.assertEqual(saved.data['currentStock'], 100)
        plan = ProcurementPlan.objects.get(material=self.material)
        self.assertEqual(plan.remaining_stock(stock_date), Decimal('100'))
        self.assertEqual(plan.remaining_stock(stock_date + timedelta(days=1)), Decimal('100'))
        self.assertEqual(plan.remaining_stock(stock_date + timedelta(days=5)), Decimal('100'))
        self.assertEqual(plan.remaining_stock(stock_date + timedelta(days=101)), Decimal('100'))
        for _ in range(2):
            reloaded = self.client.get('/api/procurement-plans/').data['plans'][0]
            self.assertEqual(reloaded['currentStock'], 100)
            self.assertEqual(reloaded['stock'], 100)
        self.assertEqual(ProcurementPlanRevision.objects.filter(plan=plan).count(), 1)
        self.assertFalse(RawMovement.objects.filter(material=self.material).exists())
        # Independently changing Raw Material does not change the procurement balance.
        self.client.post('/api/raw-inventory/movements/', self.entry, format='json')
        self.assertEqual(self.client.get('/api/procurement-plans/').data['plans'][0]['currentStock'], 100)
        self.material.refresh_from_db()
        self.assertEqual(self.material.opening_stock, Decimal('100'))

    def test_material_choice_includes_current_raw_stock_for_new_plan(self):
        self.client.post('/api/raw-inventory/movements/', self.entry, format='json')
        self.client.post('/api/raw-inventory/movements/', {**self.entry, 'id': str(uuid4()), 'movementType': 'IN', 'quantity': 10}, format='json')
        data = self.client.get('/api/procurement-plans/').data
        choice = next(m for m in data['materials'] if m['id'] == self.material.pk)
        self.assertEqual(choice['currentStock'], 107)
        self.assertEqual(choice['unit'], 'kg')
        self.assertFalse(ProcurementPlan.objects.filter(material=self.material).exists())
        self.material.opening_stock = None
        self.material.save()
        choice = next(m for m in self.client.get('/api/procurement-plans/').data['materials'] if m['id'] == self.material.pk)
        self.assertIsNone(choice['currentStock'])

    def test_stock_changes_only_when_manually_updated(self):
        anchor = timezone.localdate() - timedelta(days=3)
        body = {**self.plan, 'stock': 100, 'asOfDate': anchor.isoformat(), 'monthly': 26}
        url = f'/api/procurement-plans/{self.material.pk}/'
        self.client.put(url, body, format='json')
        revised = {**body, 'expected_version': 1, 'stock': 97, 'asOfDate': timezone.localdate().isoformat(), 'monthly': 52}
        result = self.client.put(url, revised, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['currentStock'], 97)
        plan = ProcurementPlan.objects.get(material=self.material)
        self.assertEqual(plan.remaining_stock(timezone.localdate() + timedelta(days=1)), Decimal('97'))
        self.assertEqual(plan.revisions.count(), 2)
        plan.monthly = 0
        self.assertEqual(plan.remaining_stock(timezone.localdate() + timedelta(days=10)), Decimal('97'))
        plan.stock_in_store = None
        self.assertIsNone(plan.remaining_stock())

    def test_editing_only_consumption_preserves_stock_date_and_all_ledgers(self):
        from erp.models import PurchaseOrderLine, FinishedStockItem
        date = (timezone.localdate() - timedelta(days=30)).isoformat()
        url = f'/api/procurement-plans/{self.material.pk}/'
        self.client.put(url, {**self.plan, 'stock': 100, 'asOfDate': date}, format='json')
        counts = (RawMovement.objects.count(), PurchaseOrderLine.objects.count(), FinishedStockItem.objects.count())
        changed = self.client.put(url, {**self.plan, 'expected_version': 1, 'monthly': 260, 'days': 20}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['stock'], 100)
        self.assertEqual(changed.data['currentStock'], 100)
        self.assertEqual(changed.data['asOfDate'], date)
        self.assertEqual(counts, (RawMovement.objects.count(), PurchaseOrderLine.objects.count(), FinishedStockItem.objects.count()))

    def test_procurement_stock_date_and_quantity_are_validated(self):
        url = f'/api/procurement-plans/{self.material.pk}/'
        for fields in [{'stock': -1}, {'stock': 'NaN'}, {'asOfDate': (timezone.localdate()+timedelta(days=1)).isoformat()}]:
            self.assertEqual(self.client.put(url, {**self.plan, **fields}, format='json').status_code, 400)
        self.assertFalse(ProcurementPlan.objects.filter(material=self.material).exists())

    def test_actual_permissions_not_display_role(self):
        user = get_user_model().objects.create_user('no-permissions', role='admin')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/raw-inventory/').status_code, 403)
        self.assertEqual(self.client.post('/api/raw-inventory/movements/', self.entry, format='json').status_code, 403)
        self.assertEqual(self.client.put(f'/api/procurement-plans/{self.material.pk}/', self.plan, format='json').status_code, 403)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='inventory', codename__in=['view_rawmaterial', 'view_rawmovement']))
        self.client.force_authenticate(get_user_model().objects.get(pk=user.pk))
        snapshot = self.client.get('/api/raw-inventory/')
        self.assertEqual(snapshot.status_code, 200)
        self.assertFalse(snapshot.data['canPost'])
        self.assertFalse(snapshot.data['canPlan'])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/raw-inventory/').status_code, 401)

    def test_reference_seed_is_not_repeated_on_reads(self):
        self.assertGreater(RawMaterial.objects.count(), 90)
        before = RawMovement.objects.count()
        self.client.get('/api/raw-inventory/')
        self.client.get('/api/raw-inventory/')
        self.assertEqual(RawMovement.objects.count(), before)

    def test_plan_validation_and_immutable_ledger(self):
        url = f'/api/procurement-plans/{self.material.pk}/'
        for changes in [{'days': 0}, {'lead': -1}, {'monthly': -2}, {'deliveries': [*self.plan['deliveries'], *self.plan['deliveries']]}]:
            self.assertEqual(self.client.put(url, {**self.plan, **changes}, format='json').status_code, 400)
        self.assertFalse(ProcurementPlan.objects.filter(material=self.material).exists())
        self.assertEqual(self.client.delete('/api/raw-inventory/movements/').status_code, 405)
