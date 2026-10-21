from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import GoodsReceivedNote, GoodsReceivedLine, PurchaseOrder


class GRNTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='receiver', password='test-grn-password')
        self.client = APIClient(); self.client.force_authenticate(self.user)
        self.po_body = {'request_id': str(uuid4()), 'supplier_name': 'GRN test supplier',
                        'order_date': timezone.localdate().isoformat(), 'currency': 'USD',
                        'lines': [{'description': 'GRN test finished bags', 'unit': 'ctn', 'quantity': '10', 'unit_price': '25',
                                   'stock_mode': 'manual', 'new_item': {'category': 'carrier-bags', 'brand': 'Test brand'}}]}
        response = self.client.post('/api/purchase-orders/', self.po_body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.po = response.data
        self.body = {'request_id': str(uuid4()), 'purchase_order': self.po['id'],
                     'receipt_date': timezone.localdate().isoformat(), 'authorized_person': 'Manager name', 'notes': 'Delivery checked',
                     'lines': [{'purchase_line': self.po['lines'][0]['id'], 'quantity_received': '8',
                                'damaged_quantity': '1', 'missing_quantity': '2', 'remarks': 'One damaged carton'}]}

    def receipts(self):
        return [row for row in self.client.get('/api/finished-stock/').data['receipts'] if row.get('purchaseOrderId') == str(self.po['id'])]

    def test_default_po_waits_for_grn_and_partial_receipts_post_only_accepted_stock(self):
        self.assertEqual(self.po['receipt_method'], 'grn')
        self.assertEqual(self.po['status'], 'awaiting_receipt')
        self.assertTrue(self.po['can_receive'])
        self.assertFalse(self.po['lines'][0]['stock_posted'])
        self.assertEqual(self.receipts(), [])
        first = self.client.post('/api/goods-received-notes/', self.body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertTrue(first.data['number'].startswith('GRN-'))
        self.assertEqual(first.data['supplier_name'], self.po_body['supplier_name'])
        self.assertEqual(first.data['received_by'], self.user.pk)
        self.assertEqual(first.data['receiver_name'], self.user.username)
        self.assertEqual(Decimal(first.data['lines'][0]['quantity_accepted']), 7)
        self.assertEqual(self.receipts()[0]['quantity'], 7)
        self.assertEqual(self.receipts()[0]['goodsReceivedNoteId'], str(first.data['id']))
        order = self.client.get(f"/api/purchase-orders/{self.po['id']}/").data
        self.assertEqual(order['status'], 'partially_received')
        self.assertEqual(Decimal(order['lines'][0]['quantity_remaining']), 2)
        second_body = deepcopy(self.body); second_body['request_id'] = str(uuid4())
        second_body['lines'][0].update(quantity_received='2', damaged_quantity='0', missing_quantity='0')
        result = self.client.post('/api/goods-received-notes/', second_body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(Decimal(result.data['lines'][0]['previously_received']), 8)
        self.assertEqual(sum(row['quantity'] for row in self.receipts()), 9)
        order = self.client.get(f"/api/purchase-orders/{self.po['id']}/").data
        self.assertEqual(order['status'], 'received'); self.assertFalse(order['can_receive'])
        self.assertEqual(self.client.post('/api/purchase-orders/', self.po_body, format='json').data['id'], self.po['id'])
        self.assertEqual(sum(row['quantity'] for row in self.receipts()), 9)

    def test_retry_reload_and_immutable_note(self):
        first = self.client.post('/api/goods-received-notes/', self.body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post('/api/goods-received-notes/', self.body, format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(GoodsReceivedNote.objects.count(), 1)
        self.assertEqual(GoodsReceivedLine.objects.count(), 1)
        self.assertEqual(len(self.receipts()), 1)
        fresh = APIClient(); fresh.force_authenticate(self.user)
        detail = fresh.get(f"/api/goods-received-notes/{first.data['id']}/")
        self.assertEqual(detail.data['lines'][0]['description'], self.po_body['lines'][0]['description'])
        self.assertTrue(fresh.get('/api/goods-received-notes/').data['can_create'])
        changed = deepcopy(self.body); changed['lines'][0]['quantity_received'] = '7'
        self.assertEqual(self.client.post('/api/goods-received-notes/', changed, format='json').status_code, 409)
        self.assertEqual(fresh.patch(f"/api/goods-received-notes/{first.data['id']}/", {}, format='json').status_code, 405)

    def test_invalid_quantities_or_dates_never_post_a_receipt(self):
        for fields in [{'quantity_received': '-1'}, {'quantity_received': '11', 'missing_quantity': '0'},
                       {'damaged_quantity': '9'}, {'missing_quantity': '3'}, {'missing_quantity': '-1'},
                       {'quantity_received': 'NaN'}, {'quantity_received': '0', 'damaged_quantity': '0', 'missing_quantity': '0'}]:
            body = deepcopy(self.body); body['lines'][0].update(fields)
            self.assertEqual(self.client.post('/api/goods-received-notes/', body, format='json').status_code, 400, fields)
        for date in (timezone.localdate() + timedelta(days=1), timezone.localdate() - timedelta(days=1)):
            self.assertEqual(self.client.post('/api/goods-received-notes/', {**self.body, 'receipt_date': date.isoformat()}, format='json').status_code, 400)
        self.assertEqual(GoodsReceivedNote.objects.count(), 0)
        self.assertEqual(self.receipts(), [])

    def test_immediate_purchase_cannot_be_received_again(self):
        body = deepcopy(self.po_body); body['request_id'] = str(uuid4()); body['receipt_method'] = 'immediate'
        body['lines'] = [{**{key: self.po_body['lines'][0][key] for key in ('description', 'unit', 'quantity', 'unit_price')},
                          'stock_mode': 'existing', 'stock_item': self.po['lines'][0]['stock_item']}]
        immediate = self.client.post('/api/purchase-orders/', body, format='json')
        self.assertEqual(immediate.status_code, 201, immediate.data)
        grn = {**self.body, 'purchase_order': immediate.data['id'],
               'lines': [{**self.body['lines'][0], 'purchase_line': immediate.data['lines'][0]['id']}]}
        self.assertEqual(self.client.post('/api/goods-received-notes/', grn, format='json').status_code, 400)
        self.assertEqual(GoodsReceivedNote.objects.count(), 0)

    def test_partial_and_multi_item_receipts_are_atomic_and_recheck_outstanding(self):
        self.client.post('/api/goods-received-notes/', self.body, format='json')
        body = deepcopy(self.body); body['request_id'] = str(uuid4())
        self.assertEqual(self.client.post('/api/goods-received-notes/', body, format='json').status_code, 400)
        self.assertEqual(GoodsReceivedNote.objects.count(), 1)
        self.assertEqual(sum(row['quantity'] for row in self.receipts()), 7)
        duplicate = deepcopy(self.body); duplicate['request_id'] = str(uuid4())
        duplicate['lines'].append(deepcopy(duplicate['lines'][0]))
        self.assertEqual(self.client.post('/api/goods-received-notes/', duplicate, format='json').status_code, 400)

    def test_foreign_po_line_and_permission_spoofing_fail(self):
        other = deepcopy(self.po_body); other['request_id'] = str(uuid4())
        other['lines'][0]['description'] = 'Other stock item'
        other_po = self.client.post('/api/purchase-orders/', other, format='json').data
        invalid = deepcopy(self.body); invalid['lines'].append({**invalid['lines'][0], 'purchase_line': other_po['lines'][0]['id']})
        self.assertEqual(self.client.post('/api/goods-received-notes/', invalid, format='json').status_code, 400)
        self.assertEqual(GoodsReceivedNote.objects.count(), 0)
        self.assertEqual(GoodsReceivedLine.objects.count(), 0)
        user = get_user_model().objects.create_user('no-grn-permission', role='admin')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/goods-received-notes/').status_code, 403)
        self.assertEqual(self.client.post('/api/goods-received-notes/', self.body, format='json').status_code, 403)
        self.client.force_authenticate(self.user)
        result = self.client.post('/api/goods-received-notes/', {**self.body, 'received_by': user.pk, 'receiver_name': 'Spoofed', 'supplier_name': 'Wrong'}, format='json')
        self.assertEqual(result.data['received_by'], self.user.pk)
        self.assertEqual(result.data['receiver_name'], self.user.username)
        self.assertEqual(result.data['supplier_name'], self.po_body['supplier_name'])

    def test_missing_only_and_fully_damaged_receipts_add_no_stock_or_raw_movements(self):
        from inventory.models import RawMovement
        raw_count = RawMovement.objects.count()
        body = deepcopy(self.body)
        body['lines'][0].update(quantity_received='0', damaged_quantity='0', missing_quantity='10')
        self.assertEqual(self.client.post('/api/goods-received-notes/', body, format='json').status_code, 201)
        self.assertEqual(self.receipts(), [])
        body['request_id'] = str(uuid4()); body['lines'][0].update(quantity_received='10', damaged_quantity='10', missing_quantity='0')
        self.assertEqual(self.client.post('/api/goods-received-notes/', body, format='json').status_code, 201)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(RawMovement.objects.count(), raw_count)
        self.assertEqual(PurchaseOrder.objects.get(pk=self.po['id']).status, 'received')
