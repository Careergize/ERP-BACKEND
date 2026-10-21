from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import Role
from inventory.models import RawMaterial, RawMovement
from .models import WorkflowOrder, WorkflowJob, FinishedStockItem


class ProductionWorkflowTests(TestCase):
    def setUp(self):
        self.users = {role: get_user_model().objects.create_user(role, password='Workflow-test-123!', role=role,
                     assigned_role=Role.objects.get(code=role)) for role in ('receptionist', 'production_manager', 'store_keeper')}
        self.client = APIClient(); self.client.force_authenticate(self.users['receptionist'])
        self.raw = RawMaterial.objects.create(id='workflow-test-raw', name='Workflow raw grade', unit='kg', category='virgin', family='Test', opening_stock=1000, opening_date=timezone.localdate())
        stage = lambda name: {'name': name, 'department': name, 'inputUnit': 'kg', 'outputUnit': 'ctn', 'wasteUnit': 'kg', 'required': True}
        self.model = {'id': str(uuid4()), 'name': 'Product-specific route', 'revision': 0, 'defaultUnit': 'ctn',
                      'productionBatchQuantity': 10, 'productionBatchUnit': 'ctn', 'recipeMode': 'quantities',
                      'routing': [stage('Extrusion'), stage('Printing'), stage('Cutting')],
                      'recipe': [{'materialId': self.raw.pk, 'unit': 'kg', 'quantity': 5}]}
        result = self.client.post('/api/production-workflow/models/', self.model, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.model = result.data
        self.body = {'creationKey': str(uuid4()), 'customerName': 'Workflow customer', 'marketingPersonName': 'Sales person',
                     'customerType': 'B2B', 'currency': 'USD', 'taxRate': 18, 'date': timezone.localdate().isoformat(), 'allocateJobCard': True,
                     'items': [{'id': str(uuid4()), 'itemName': 'Custom bag', 'quantity': 20, 'unit': 'ctn', 'unitPrice': 2,
                                'fulfillmentSource': 'manufacturing', 'modelId': self.model['id'], 'modelRevision': 1,
                                'requiredDate': (timezone.localdate() + timedelta(days=5)).isoformat()}]}

    def create_order(self, body=None):
        self.client.force_authenticate(self.users['receptionist'])
        result = self.client.post('/api/production-workflow/orders/', body or self.body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        return result.data, self.client.get('/api/production-workflow/jobs/').data[0]

    def action(self, job, action, role, **kwargs):
        self.client.force_authenticate(self.users[role])
        return self.client.post(f'/api/production-workflow/jobs/{job["workflowKey"]}/{action}/',
                                {'expectedVersion': job['workflowVersion'], **kwargs}, format='json')

    def test_receptionist_model_requirements_pm_edit_approve_and_store_issue_across_sessions(self):
        order, job = self.create_order()
        child = job['manufacturingItems'][0]
        self.assertEqual(order['jobCardIds'], [job['id']])
        self.assertEqual(child['status'], 'PendingApproval')
        self.assertEqual([s['name'] for s in child['productionStages']], ['Extrusion', 'Printing', 'Cutting'])
        self.assertEqual(child['requiredMaterials'][0]['requiredQuantity'], 10)
        self.assertEqual(self.action(job, 'approve', 'receptionist').status_code, 403)
        materials = deepcopy(child['requiredMaterials']); materials[0]['requiredQuantity'] = 12
        changed = self.action(job, 'edit', 'production_manager', itemId=child['id'], change={'requiredMaterials': materials, 'specifications': 'Corrected size'})
        self.assertEqual(changed.status_code, 200, changed.data)
        approved = self.action(changed.data, 'approve', 'production_manager')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], 'StoreIssuePending')
        self.client.force_authenticate(self.users['store_keeper'])
        loaded = self.client.get('/api/production-workflow/jobs/').data[0]
        self.assertEqual(loaded['status'], 'StoreIssuePending')
        row = loaded['manufacturingItems'][0]['requiredMaterials'][0]
        issued = self.action(loaded, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 6})
        self.assertEqual(issued.status_code, 200, issued.data)
        self.assertEqual(issued.data['manufacturingItems'][0]['requiredMaterials'][0]['issuedQuantity'], 6)
        self.assertEqual(RawMovement.objects.get(material=self.raw).quantity, Decimal(6))
        again = self.action(loaded, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 6})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(RawMovement.objects.filter(material=self.raw).count(), 1)
        completed = self.action(issued.data, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 6})
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(sum(m.quantity for m in RawMovement.objects.filter(material=self.raw)), 12)
        self.assertEqual(self.action(completed.data, 'edit', 'production_manager', itemId=child['id'], change={'specifications': 'Late change'}).status_code, 400)

    def test_stock_only_and_mixed_orders_never_manufacture_stock_lines(self):
        product = FinishedStockItem.objects.filter(unit='ctn').first()
        stock = {'id': str(uuid4()), 'itemId': product.pk, 'itemName': product.name, 'quantity': 2, 'unit': product.unit, 'unitPrice': 3, 'fulfillmentSource': 'stock'}
        body = {**self.body, 'items': [stock]}
        result = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['jobCardIds'], [])
        self.assertFalse(WorkflowJob.objects.exists())
        body = deepcopy(self.body); body['creationKey'] = str(uuid4()); body['items'].append(stock)
        order, job = self.create_order(body)
        self.assertEqual(len(job['manufacturingItems']), 1)
        self.assertEqual(job['manufacturingItems'][0]['salesOrderItemId'], self.body['items'][0]['id'])
        self.assertFalse(RawMovement.objects.filter(material=self.raw).exists())

    def test_optional_allocation_and_safe_order_retry(self):
        self.body['allocateJobCard'] = False
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(WorkflowJob.objects.exists())
        key = response.data['id'].replace('wf-order-', '')
        job = self.client.post(f'/api/production-workflow/orders/{key}/allocate/', {}, format='json')
        self.assertEqual(job.status_code, 200, job.data)
        self.assertEqual(self.client.post(f'/api/production-workflow/orders/{key}/allocate/', {}, format='json').data['id'], job.data['id'])
        self.assertEqual(self.client.post('/api/production-workflow/orders/', self.body, format='json').data['id'], response.data['id'])
        changed = deepcopy(self.body); changed['customerName'] = 'Changed'
        self.assertEqual(self.client.post('/api/production-workflow/orders/', changed, format='json').status_code, 409)
        self.assertEqual(WorkflowOrder.objects.count(), 1)

    def test_issue_before_approval_overissue_unknown_stock_and_unauthorized_actors_fail(self):
        order, job = self.create_order(); child = job['manufacturingItems'][0]; row = child['requiredMaterials'][0]
        self.assertEqual(self.action(job, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 1}).status_code, 400)
        approved = self.action(job, 'approve', 'production_manager').data
        self.assertEqual(self.action(approved, 'issue', 'receptionist', itemId=child['id'], quantities={row['id']: 1}).status_code, 403)
        self.assertEqual(self.action(approved, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 11}).status_code, 400)
        self.raw.opening_stock = 2; self.raw.save()
        self.assertEqual(self.action(approved, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 3}).status_code, 400)
        self.raw.opening_stock = None; self.raw.save()
        self.assertEqual(self.action(approved, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 1}).status_code, 400)
        self.assertFalse(RawMovement.objects.filter(material=self.raw).exists())

    def test_unconfigured_or_wrong_output_units_rollback_order_creation(self):
        wrong = deepcopy(self.body); wrong['items'][0]['unit'] = 'pcs'
        self.assertEqual(self.client.post('/api/production-workflow/orders/', wrong, format='json').status_code, 400)
        wrong = deepcopy(self.body); wrong['items'][0]['modelRevision'] = 99
        self.assertEqual(self.client.post('/api/production-workflow/orders/', wrong, format='json').status_code, 400)
        self.assertFalse(WorkflowOrder.objects.exists())

    def test_reusable_manual_processes_and_product_specific_order(self):
        process = {'name': 'Custom sealing step', 'department': 'Sealing', 'inputUnit': 'kg', 'outputUnit': 'ctn', 'wasteUnit': 'kg'}
        created = self.client.post('/api/production-workflow/processes/', process, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(len(self.client.get('/api/production-workflow/processes/').data), 1)
        revised = deepcopy(self.model); revised['routing'] = [created.data, *list(reversed(revised['routing']))]
        result = self.client.post('/api/production-workflow/models/', revised, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['revision'], 2)
        self.assertEqual([s['name'] for s in result.data['routing']], ['Custom sealing step', 'Cutting', 'Printing', 'Extrusion'])
        self.assertEqual([s['sequence'] for s in result.data['routing']], [1, 2, 3, 4])
        # Existing order snapshots still use their explicitly selected revision.
        order, job = self.create_order()
        self.assertEqual(len(job['manufacturingItems'][0]['productionStages']), 3)
