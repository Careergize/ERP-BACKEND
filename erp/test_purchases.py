import uuid
from copy import deepcopy
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient
from django.utils import timezone
from datetime import timedelta
from .models import FinishedStockItem, PurchaseOrder, PurchaseOrderLine


class PurchaseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='buyer', password='test-password')
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['view_purchaseorder', 'add_purchaseorder', 'change_purchaseorder']))
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.payload = {'request_id': str(uuid.uuid4()), 'supplier_name': 'Test supplier',
                        'order_date': '2026-10-21', 'delivery_date': '2026-10-28', 'currency': 'USD', 'receipt_method': 'immediate',
                        'lines': [{'description': 'Purchased finished item', 'quantity': '12.500', 'unit': 'kg', 'unit_price': '4.20',
                                   'stock_mode': 'manual', 'new_item': {'category': 'packing'}}]}

    def test_save_reload_total_and_safe_retry(self):
        response = self.client.post('/api/purchase-orders/', self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total'], '52.50')
        self.assertEqual(response.data['status'], 'received')
        self.assertEqual(response.data['created_by'], self.user.pk)
        self.assertTrue(response.data['number'].startswith('PO-'))
        self.assertEqual(self.client.post('/api/purchase-orders/', self.payload, format='json').data['id'], response.data['id'])
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrderLine.objects.count(), 1)
        fresh = APIClient(); fresh.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        detail = fresh.get(f"/api/purchase-orders/{response.data['id']}/")
        self.assertEqual(detail.data['lines'][0]['description'], 'Purchased finished item')
        self.assertTrue(fresh.get('/api/purchase-orders/').data['can_create'])
        changed = deepcopy(self.payload); changed['supplier_name'] = 'Other supplier'
        self.assertEqual(self.client.post('/api/purchase-orders/', changed, format='json').status_code, 409)
        self.assertEqual(self.client.patch(f"/api/purchase-orders/{response.data['id']}/", {}, format='json').status_code, 405)

    def test_validation_is_atomic(self):
        for mutate in ('quantity', 'unit_price', 'unit', 'description', 'lines', 'date', 'currency'):
            payload = deepcopy(self.payload)
            if mutate == 'quantity': payload['lines'][0]['quantity'] = '0'
            elif mutate == 'unit_price': payload['lines'][0]['unit_price'] = '-1'
            elif mutate in ('unit', 'description'): payload['lines'][0][mutate] = ' '
            elif mutate == 'lines': payload['lines'] = []
            elif mutate == 'date': payload['delivery_date'] = '2026-10-20'
            else: payload['currency'] = '123'
            self.assertEqual(self.client.post('/api/purchase-orders/', payload, format='json').status_code, 400, mutate)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_permissions_and_server_fields(self):
        stranger = get_user_model().objects.create_user(username='stranger', password='test-password', role='admin')
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get('/api/purchase-orders/').status_code, 403)
        self.assertEqual(self.client.post('/api/purchase-orders/', self.payload, format='json').status_code, 403)
        self.client.force_authenticate(self.user)
        self.payload.update(created_by=stranger.pk, status='approved', number='forged')
        response = self.client.post('/api/purchase-orders/', self.payload, format='json')
        self.assertEqual(response.data['created_by'], self.user.pk)
        self.assertEqual(response.data['status'], 'received')
        self.assertNotEqual(response.data['number'], 'forged')

    def test_manual_item_receipt_and_next_purchase_from_stock(self):
        response = self.client.post('/api/purchase-orders/', self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        item_id = response.data['lines'][0]['stock_item']
        self.assertTrue(response.data['lines'][0]['stock_posted'])
        item = FinishedStockItem.objects.get(pk=item_id)
        self.assertEqual(item.opening_stock, 0)
        stock = self.client.get('/api/finished-stock/').data
        self.assertEqual(next(p for p in stock['products'] if p['id'] == item_id)['itemDetails'], 'Purchased finished item')
        receipts = [m for m in stock['receipts'] if m['productId'] == item_id]
        self.assertEqual(sum(m['quantity'] for m in receipts), 12.5)
        next_purchase = deepcopy(self.payload)
        next_purchase['request_id'] = str(uuid.uuid4())
        next_purchase['lines'] = [{'description': item.name, 'stock_mode': 'existing', 'stock_item': item_id,
                                   'unit': 'kg', 'quantity': '7.500', 'unit_price': '4.50'}]
        result = self.client.post('/api/purchase-orders/', next_purchase, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        fresh = APIClient(); fresh.force_authenticate(self.user)
        receipts = [m for m in fresh.get('/api/finished-stock/').data['receipts'] if m['productId'] == item_id]
        self.assertEqual(sum(m['quantity'] for m in receipts), 20)
        self.assertEqual(FinishedStockItem.objects.filter(name=item.name).count(), 1)
        self.client.post('/api/purchase-orders/', next_purchase, format='json')
        self.assertEqual(PurchaseOrderLine.objects.filter(stock_item=item).count(), 2)

    def test_existing_variant_exact_unit_unknown_opening_and_raw_unchanged(self):
        from inventory.models import RawMovement, RawMaterial
        raw_counts = (RawMovement.objects.count(), RawMaterial.objects.count())
        item = FinishedStockItem.objects.filter(opening_stock__isnull=True).first()
        self.payload['lines'] = [{'description': item.name, 'stock_mode': 'existing', 'stock_item': item.pk,
                                 'unit': item.unit, 'quantity': '2', 'unit_price': '0'}]
        result = self.client.post('/api/purchase-orders/', self.payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        item.refresh_from_db(); self.assertIsNone(item.opening_stock)
        self.assertEqual((RawMovement.objects.count(), RawMaterial.objects.count()), raw_counts)
        self.payload['request_id'] = str(uuid.uuid4())
        self.payload['lines'][0]['unit'] = 'wrong-unit'
        self.assertEqual(self.client.post('/api/purchase-orders/', self.payload, format='json').status_code, 400)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_partial_invalid_order_rolls_back_all_stock_and_manual_masters(self):
        item_count = FinishedStockItem.objects.count()
        # Each row is valid alone; duplicate manual variants fail during transactional creation.
        self.payload['lines'].append(deepcopy(self.payload['lines'][0]))
        response = self.client.post('/api/purchase-orders/', self.payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        self.assertEqual(PurchaseOrderLine.objects.count(), 0)
        self.assertEqual(FinishedStockItem.objects.count(), item_count)

    def test_future_and_preopening_purchases_fail(self):
        self.payload['order_date'] = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.payload['delivery_date'] = None
        self.assertEqual(self.client.post('/api/purchase-orders/', self.payload, format='json').status_code, 400)
        item = FinishedStockItem.objects.first()
        self.payload['order_date'] = (item.opening_date - timedelta(days=1)).isoformat()
        self.payload['lines'] = [{'description': item.name, 'stock_mode': 'existing', 'stock_item': item.pk,
                                 'unit': item.unit, 'quantity': '2', 'unit_price': '1'}]
        self.assertEqual(self.client.post('/api/purchase-orders/', self.payload, format='json').status_code, 400)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_reference_variants_and_legacy_orders_are_preserved(self):
        import json
        from pathlib import Path
        rows = json.loads(Path(__file__).with_name('migrations').joinpath('finished_stock_reference.json').read_text(encoding='utf-8'))
        self.assertEqual(len(rows), 368)
        for row in rows:
            self.assertEqual(FinishedStockItem.objects.get(pk=row['id']).reference, row)
        legacy = PurchaseOrder.objects.create(request_id=uuid.uuid4(), supplier_name='Earlier supplier', order_date=timezone.localdate(), currency='USD', created_by=self.user)
        PurchaseOrderLine.objects.create(order=legacy, description='Earlier entry', unit='ctn', quantity=5, unit_price=2)
        self.assertEqual(self.client.get('/api/finished-stock/').data['receipts'], [])
