import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from erp.models import Customer, FinishedStockItem, ProductionModelRevision, WorkflowOrder, WorkflowJob
from inventory.models import RawMaterial
from .models import Quotation, QuotationDraft


class ApprovalFulfillmentTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('quote-sales', password='test-only-password')
        self.manager = get_user_model().objects.create_user('quote-manager', password='test-only-password',
            role='project_manager', assigned_role=Role.objects.get(code='project_manager'))
        self.customer = Customer.objects.create(name='Approval buyer')
        self.stock = FinishedStockItem.objects.create(id='approval-stock', name='Stock bag', category='packing',
            unit='ctn', opening_stock=50, opening_date=timezone.localdate())
        raw = RawMaterial.objects.create(id='approval-raw', name='Raw', unit='kg', category='virgin', family='Test',
            opening_stock=100, opening_date=timezone.localdate())
        self.model = ProductionModelRevision.objects.create(model_id='approval-model', revision=1, data={
            'name': 'New custom bag', 'productionBatchQuantity': 10, 'productionBatchUnit': 'ctn',
            'recipe': [{'materialId': raw.pk, 'unit': 'kg', 'quantity': 5}],
            'routing': [{'name': 'Cutting', 'department': 'Cutting', 'inputUnit': 'kg', 'outputUnit': 'ctn', 'wasteUnit': 'kg'}]})
        self.payload = json.loads(Path(__file__).with_name('sample.json').read_text())
        self.payload.update(id=str(uuid4()), rootId=str(uuid4()), date=timezone.localdate().isoformat(), salesRepresentative='Sales person')
        self.payload['customer'].update(customerId=f'db:{self.customer.pk}', customerType='B2B')
        self.payload['lines'] = [dict(self.payload['lines'][0], id=str(uuid4()), type='stock', productId=self.stock.pk,
                                      description=self.stock.name, quantity=2, unit='ctn', unitPrice=100)]
        self.due = (timezone.localdate() + timedelta(days=5)).isoformat()

    def custom_line(self):
        return dict(self.payload['lines'][0], id=str(uuid4()), type='custom', description='New custom bag',
                    productId=None, modelId=self.model.model_id, modelRevision=1)

    def submit(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post('/api/quotation-drafts/', {'editor_data': self.payload, 'expected_version': 0}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        public = deepcopy(self.payload)
        public.update(source_id=self.payload['id'], customer_id=self.customer.pk)
        response = self.client.post('/api/quotations/', public, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.quote_id = response.data['id']
        self.assertEqual(self.client.post(f'/api/quotations/{self.quote_id}/submit/').status_code, 200)
        self.client.force_authenticate(self.manager)

    def approve(self, **data):
        return self.client.post(f'/api/quotations/{self.quote_id}/approve/', data, format='json')

    def test_stock_approval_creates_one_order_without_job_or_stock_change(self):
        self.submit()
        response = self.approve()
        self.assertEqual(response.status_code, 200, response.data)
        order = WorkflowOrder.objects.get()
        self.assertEqual(order.data['status'], 'Approved')
        self.assertEqual(order.data['items'][0]['itemId'], self.stock.pk)
        self.assertEqual(order.created_by, self.owner)
        self.assertFalse(WorkflowJob.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.opening_stock, 50)
        self.assertEqual(response.data['fulfillment']['sales_order']['id'], order.data['id'])
        self.assertEqual(self.approve().status_code, 200)
        self.assertEqual(WorkflowOrder.objects.count(), 1)

    def test_mixed_order_creates_job_for_custom_line_only(self):
        custom = self.custom_line()
        custom.pop('productId')
        self.payload['lines'].append(custom)
        self.submit()
        response = self.approve(required_date=self.due)
        self.assertEqual(response.status_code, 200, response.data)
        job = WorkflowJob.objects.get()
        self.assertEqual(len(job.data['manufacturingItems']), 1)
        child = job.data['manufacturingItems'][0]
        self.assertEqual(child['product'], 'New custom bag')
        self.assertEqual(child['requiredDate'], self.due)
        self.assertEqual(child['status'], 'PendingApproval')
        self.assertEqual(self.approve(required_date=self.due).status_code, 200)
        self.assertEqual(WorkflowJob.objects.count(), 1)

    def test_missing_delivery_date_rolls_back_approval_and_order(self):
        custom = self.custom_line(); custom.pop('productId')
        self.payload['lines'] = [custom]
        self.submit()
        self.assertEqual(self.approve().status_code, 400)
        self.assertFalse(WorkflowOrder.objects.exists())
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertIsNone(Quotation.objects.get(pk=self.quote_id).approved_at)

    def test_rejection_and_unauthorized_approval_do_not_create_orders(self):
        self.submit()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.approve().status_code, 403)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.post(f'/api/quotations/{self.quote_id}/reject/', {'reason': 'Revise price'}).status_code, 200)
        self.assertFalse(WorkflowOrder.objects.exists())

    def test_changed_draft_cannot_create_order_from_old_snapshot(self):
        self.submit()
        draft = QuotationDraft.objects.get(pk=self.payload['id'])
        draft.editor_data['lines'][0]['unitPrice'] = '999'
        draft.save()
        self.assertEqual(self.approve().status_code, 400)
        self.assertFalse(WorkflowOrder.objects.exists())
        self.assertIsNone(Quotation.objects.get(pk=self.quote_id).approved_at)
