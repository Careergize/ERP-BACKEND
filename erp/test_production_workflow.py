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

    def test_existing_item_manufacturing_requires_known_zero_stock(self):
        product = FinishedStockItem.objects.create(id='zero-stock-job-test', name='Existing bag', category='packing',
                    unit='ctn', opening_stock=0, opening_date=timezone.localdate())
        self.body['items'][0].update(itemId=product.pk, itemName=product.name)
        order, job = self.create_order()
        self.assertEqual(order['items'][0]['quantity'], 20)
        self.assertEqual(job['manufacturingItems'][0]['qty'], 20)
        for balance in (Decimal('1'), Decimal('-1'), None):
            product.opening_stock = balance
            product.save()
            body = deepcopy(self.body)
            body['creationKey'] = str(uuid4())
            self.prepare_quote(body)
            response = self.client.post('/api/production-workflow/orders/', body, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(WorkflowOrder.objects.count(), 1)

    def test_deferred_job_rechecks_existing_stock(self):
        product = FinishedStockItem.objects.create(id='deferred-zero-stock-test', name='Existing bag', category='packing',
                    unit='ctn', opening_stock=0, opening_date=timezone.localdate())
        self.body['items'][0].update(itemId=product.pk, itemName=product.name)
        self.body['allocateJobCard'] = False
        self.prepare_quote(self.body)
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        product.opening_stock = 5
        product.save()
        key = response.data['id'].replace('wf-order-', '')
        self.client.force_authenticate(self.users['production_manager'])
        response = self.client.post(f'/api/production-workflow/orders/{key}/approve/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertNotIn('salesOrderApproval', WorkflowOrder.objects.get().data)

    def prepare_quote(self, body):
        if not hasattr(self, 'quote_requests'): self.quote_requests = {}
        key = body['creationKey']
        if key in self.quote_requests:
            body.update(self.quote_requests[key])
            from quotations.models import QuotationDraft
            lines = QuotationDraft.objects.get(pk=body['quotationRevisionId']).editor_data['lines']
            for item, line in zip(body['items'], lines): item['quotationLineId'] = line['id']
            return
        from quotations.from_order import COMPANY, TERMS
        quote_id = str(uuid4())
        lines = []
        for item in body['items']:
            item['quotationLineId'] = str(uuid4())
            lines.append({'id': item['quotationLineId'], 'type': 'custom' if item['fulfillmentSource'] == 'manufacturing' else 'stock',
                'description': item['itemName'], 'group': '', 'specifications': item.get('specifications', ''), 'quantity': item['quantity'], 'unit': item['unit'], 'unitPrice': item['unitPrice'],
                **{target: item[source] for target, source in [('productId', 'itemId'), ('modelId', 'modelId'), ('modelRevision', 'modelRevision'), ('ledSnapshot', 'ledSnapshot')] if source in item}})
        pricing = body.get('commercialPricing', {})
        payload = {'id': quote_id, 'rootId': quote_id, 'number': 'QT-' + quote_id[:8], 'revision': 1, 'documentType': 'Sales Quotation',
            'date': body['date'], 'validityDays': 14, 'currency': body['currency'], 'taxRate': body['taxRate'],
            'taxMode': pricing.get('taxMode', 'exclusive'), 'discount': pricing.get('discount', 0),
            'customer': {**{field: '' for field in ('billingAddress', 'deliveryAddress', 'contactPerson', 'telephone', 'email', 'tin')}, **body.get('customerContact', {}), 'name': body['customerName'], 'customerType': body['customerType']},
            'company': COMPANY, 'terms': {**TERMS, **body.get('commercialTerms', {})}, 'salesRepresentative': body['marketingPersonName'], 'lines': lines}
        self.client.force_authenticate(self.users['receptionist'])
        response = self.client.post('/api/quotation-drafts/', {'editor_data': payload}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        document = response.data['editor_data']
        metadata = {'quotationId': quote_id, 'quotationRevisionId': quote_id, 'quotationNumber': document['number'], 'quotationRevision': 1,
                    'customerContact': document['customer'], 'commercialTerms': document['terms']}
        self.quote_requests[key] = metadata
        body.update(metadata)

    def approve_order(self, order):
        self.client.force_authenticate(self.users['production_manager'])
        key = order['id'].replace('wf-order-', '')
        result = self.client.post(f'/api/production-workflow/orders/{key}/approve/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.client.force_authenticate(self.users['receptionist'])
        return result.data

    def create_order(self, body=None):
        body = body if body is not None else self.body
        self.prepare_quote(body)
        result = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        order = self.approve_order(result.data)
        return order, self.client.get('/api/production-workflow/jobs/').data[0]

    def test_manual_job_without_order_is_persisted_idempotent_and_reviewable(self):
        body = {**deepcopy(self.body['items'][0]), 'creationKey': str(uuid4())}
        result = self.client.post('/api/production-workflow/jobs/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        job = result.data
        self.assertFalse(WorkflowOrder.objects.exists())
        self.assertIsNone(WorkflowJob.objects.get().order_id)
        self.assertEqual(job['origin'], 'production_for_stock')
        self.assertNotIn('salesOrderId', job)
        child = job['manufacturingItems'][0]
        self.assertNotIn('salesOrderItemId', child)
        self.assertEqual(child['qty'], 20)
        self.assertEqual(child['requiredMaterials'][0]['requiredQuantity'], 10)
        retry = self.client.post('/api/production-workflow/jobs/', body, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['id'], job['id'])
        changed = {**body, 'quantity': 21}
        self.assertEqual(self.client.post('/api/production-workflow/jobs/', changed, format='json').status_code, 409)
        edited = self.action(job, 'edit', 'production_manager', itemId=child['id'], change={'specifications': 'Manual job reviewed'})
        self.assertEqual(edited.status_code, 200, edited.data)
        approved = self.action(edited.data, 'approve', 'production_manager')
        self.assertEqual(approved.status_code, 200, approved.data)
        row = child['requiredMaterials'][0]
        issued = self.action(approved.data, 'issue', 'store_keeper', itemId=child['id'], quantities={row['id']: 10})
        self.assertEqual(issued.status_code, 200, issued.data)
        self.assertTrue(RawMovement.objects.filter(material=self.raw).exists())

    def test_manual_job_validation_and_permissions(self):
        base = {**deepcopy(self.body['items'][0]), 'creationKey': str(uuid4())}
        for change in ({'quantity': 0}, {'unit': 'pcs'}, {'modelRevision': 99}, {'requiredDate': '2000-01-01'}, {'jobRouting': []}):
            response = self.client.post('/api/production-workflow/jobs/', {**base, **change}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        product = FinishedStockItem.objects.create(id='manual-job-stock', name='Existing bag', category='packing',
                    unit='ctn', opening_stock=1, opening_date=timezone.localdate())
        response = self.client.post('/api/production-workflow/jobs/', {**base, 'itemId': product.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(WorkflowJob.objects.exists())
        self.client.force_authenticate(self.users['store_keeper'])
        self.assertEqual(self.client.post('/api/production-workflow/jobs/', base, format='json').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/production-workflow/jobs/', base, format='json').status_code, 401)

    def test_quotation_first_and_manager_approval_create_exactly_one_job(self):
        from quotations.models import QuotationDraft
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WorkflowOrder.objects.exists())
        self.prepare_quote(self.body)
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        order = response.data
        self.assertEqual(order['status'], 'Draft')
        self.assertEqual(order['jobCardIds'], [])
        self.assertFalse(WorkflowJob.objects.exists())
        self.assertNotIn('generatedQuotationId', order)
        self.assertEqual(QuotationDraft.objects.count(), 1)
        key = order['id'].replace('wf-order-', '')
        self.assertEqual(self.client.post(f'/api/production-workflow/orders/{key}/allocate/', {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(f'/api/production-workflow/orders/{key}/approve/', {}, format='json').status_code, 403)
        self.client.force_authenticate(self.users['store_keeper'])
        self.assertEqual(self.client.post(f'/api/production-workflow/orders/{key}/approve/', {}, format='json').status_code, 403)
        approved = self.approve_order(order)
        self.assertEqual(approved['status'], 'Approved')
        self.assertEqual(approved['salesOrderApproval']['approvedById'], self.users['production_manager'].pk)
        self.assertEqual(len(approved['jobCardIds']), 1)
        self.assertEqual(WorkflowJob.objects.get().data['manufacturingItems'][0]['plannedCosting']['status'], 'Draft')
        self.assertEqual(self.approve_order(order), approved)
        self.assertEqual(WorkflowJob.objects.count(), 1)
        self.assertEqual(QuotationDraft.objects.count(), 1)

    def test_admin_can_approve_sales_order(self):
        self.prepare_quote(self.body)
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        admin = get_user_model().objects.create_user('order-admin', role='admin', assigned_role=Role.objects.get(code='admin'))
        self.client.force_authenticate(admin)
        key = response.data['id'].replace('wf-order-', '')
        result = self.client.post(f'/api/production-workflow/orders/{key}/approve/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['salesOrderApproval']['approvedById'], admin.pk)
        self.assertEqual(WorkflowJob.objects.count(), 1)

    def test_saved_quotation_commercial_details_cannot_be_changed_in_order(self):
        self.prepare_quote(self.body)
        for change in ('price', 'quantity', 'unit', 'customer', 'source'):
            body = deepcopy(self.body)
            if change == 'price': body['items'][0]['unitPrice'] = 99
            elif change == 'quantity': body['items'][0]['quantity'] = 21
            elif change == 'unit': body['items'][0]['unit'] = 'kg'
            elif change == 'customer': body['customerName'] = 'Other customer'
            else: body['quotationRevisionId'] = str(uuid4())
            response = self.client.post('/api/production-workflow/orders/', body, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(WorkflowOrder.objects.exists())

    def test_quotation_conversion_does_not_create_second_quotation(self):
        from quotations.models import QuotationDraft
        self.prepare_quote(self.body)
        result = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertNotIn('generatedQuotationId', result.data)
        self.assertEqual(QuotationDraft.objects.count(), 1)

    def test_order_numbers_are_automatic_and_b2c_retains_quotation_prices(self):
        first, _ = self.create_order()
        body = deepcopy(self.body)
        body.update(creationKey=str(uuid4()), customerType='B2C', taxRate=0, orderNumber='MANUAL-NUMBER')
        self.prepare_quote(body)
        result = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertRegex(result.data['orderNumber'], r'^SO-W\d+$')
        self.assertNotEqual(result.data['orderNumber'], first['orderNumber'])
        self.assertEqual(result.data['taxRate'], 0)
        self.assertEqual(result.data['orderTotal'], 40)

    def test_manual_order_workflows_and_saved_machine_compatibility(self):
        process = self.client.post('/api/production-workflow/processes/', self.model['routing'][1], format='json')
        self.assertEqual(process.status_code, 201, process.data)
        machine = self.client.post('/api/production-workflow/machines/', {'name': 'Printer A', 'department': 'Printing', 'type': 'printer', 'processIds': [process.data['id']]}, format='json')
        self.assertEqual(machine.status_code, 201, machine.data)
        self.assertEqual(self.client.get('/api/production-workflow/machines/').data[0]['name'], 'Printer A')
        step = {**self.model['routing'][1], 'id': str(uuid4()), 'processId': process.data['id'], 'machineId': machine.data['id'], 'expectedOutput': 20}
        body = deepcopy(self.body); body['items'][0]['jobRouting'] = [step, {**step, 'id': str(uuid4()), 'name': 'Second print pass'}]
        order, job = self.create_order(body)
        stages = job['manufacturingItems'][0]['productionStages']
        self.assertEqual([s['sequence'] for s in stages], [1, 2])
        self.assertEqual(stages[0]['machine'], 'Printer A')
        self.assertEqual(stages[0]['expectedOutput'], 20)  # Per-order plan is not batch-scaled.
        self.assertEqual(self.client.post('/api/production-workflow/orders/', body, format='json').status_code, 200)
        self.assertEqual(WorkflowJob.objects.count(), 1)
        changed = self.action(job, 'edit', 'production_manager', itemId=job['manufacturingItems'][0]['id'], change={'productionStages': list(reversed(stages))})
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['manufacturingItems'][0]['productionStages'][0]['name'], 'Second print pass')
        self.assertEqual(self.action(changed.data, 'approve', 'production_manager').status_code, 200)

    def test_manual_workflow_rejects_empty_unknown_or_incompatible_machines(self):
        from .models import ProductionProcess, ProductionMachine
        process = ProductionProcess.objects.create(name='Print', data={})
        machine = ProductionMachine.objects.create(name='Print A', data={'active': True, 'processIds': [str(process.pk)]})
        other = ProductionProcess.objects.create(name='Cut', data={})
        for stages in ([], [{**self.model['routing'][0], 'machineId': 'unknown'}], [{**self.model['routing'][0], 'processId': str(other.pk), 'machineId': str(machine.pk)}]):
            body = deepcopy(self.body); body['items'][0]['jobRouting'] = stages
            self.client.force_authenticate(self.users['receptionist'])
            self.prepare_quote(body)
            result = self.client.post('/api/production-workflow/orders/', body, format='json')
            self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(WorkflowOrder.objects.exists())
        self.client.force_authenticate(self.users['store_keeper'])
        self.assertEqual(self.client.post('/api/production-workflow/machines/', {}, format='json').status_code, 403)

    def action(self, job, action, role, **kwargs):
        self.client.force_authenticate(self.users[role])
        # Existing production tests explicitly prepare/review costing before handoff.
        if action == 'approve' and role == 'production_manager':
            for child in job['manufacturingItems']:
                if child.get('plannedCosting', {}).get('status') == 'Approved':
                    continue
                inputs = deepcopy(child['plannedCosting']['input'])
                for row in inputs['materials']:
                    row.update(price=2, currency=inputs['currency'], exchangeRate=1)
                for row in inputs['processes']:
                    row.update(rate=1)
                response = self.action(job, 'costing', role, itemId=child['id'], input=inputs)
                self.assertEqual(response.status_code, 200, response.data)
                job = response.data
                response = self.action(job, 'approve-costing', role, itemId=child['id'])
                self.assertEqual(response.status_code, 200, response.data)
                job = response.data
        return self.client.post(f'/api/production-workflow/jobs/{job["workflowKey"]}/{action}/',
                                {'expectedVersion': job['workflowVersion'], **kwargs}, format='json')

    def test_planned_costing_is_created_and_blocks_unreviewed_production(self):
        _, job = self.create_order()
        child = job['manufacturingItems'][0]
        self.assertEqual(child['plannedCosting']['status'], 'Draft')
        self.assertIsNone(child['plannedCosting']['totals'])
        self.assertIsNone(child['plannedCosting']['actualCost'])
        self.client.force_authenticate(self.users['production_manager'])
        response = self.client.post(f'/api/production-workflow/jobs/{job["workflowKey"]}/approve/', {'expectedVersion': job['workflowVersion']}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.action(job, 'approve-costing', 'production_manager', itemId=child['id']).status_code, 400)
        inputs = deepcopy(child['plannedCosting']['input'])
        for row in inputs['materials']:
            row.update(price=2, currency='USD', exchangeRate=4000)
        for row in inputs['processes']:
            row.update(rate=5)
        saved = self.action(job, 'costing', 'receptionist', itemId=child['id'], input=inputs)
        self.assertEqual(saved.status_code, 200, saved.data)
        updated = saved.data['manufacturingItems'][0]['plannedCosting']
        self.assertIsNotNone(updated['totals'])
        self.assertEqual(updated['totals']['productionCost'], 80015)
        stale = self.action(saved.data, 'costing', 'receptionist', itemId=child['id'], input=inputs, expectedCostingRevision=child['plannedCosting']['revision'])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.action(saved.data, 'approve-costing', 'receptionist', itemId=child['id']).status_code, 403)
        reviewed = self.action(saved.data, 'approve-costing', 'production_manager', itemId=child['id'])
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        loaded = self.client.get('/api/production-workflow/jobs/').data[0]
        self.assertEqual(loaded['manufacturingItems'][0]['plannedCosting']['status'], 'Approved')
        revised = self.action(reviewed.data, 'edit', 'production_manager', itemId=child['id'], change={'requiredMaterials': child['requiredMaterials']})
        self.assertEqual(revised.status_code, 200, revised.data)
        revised_sheet = revised.data['manufacturingItems'][0]['plannedCosting']
        self.assertEqual(revised_sheet['status'], 'Draft')
        self.assertTrue(any(h.get('status') == 'Approved' for h in revised_sheet['history']))
        self.assertEqual(self.action(job, 'costing', 'store_keeper', itemId=child['id'], input=inputs).status_code, 403)

    def test_planned_costing_vat_exchange_units_and_led_higher_rule(self):
        from .job_costing import calculate
        child = {'qty': 10, 'unit': 'kg', 'requiredMaterials': [{'materialId': 'test', 'requiredQuantity': 10, 'unit': 'kg'}],
                 'productionStages': [{'id': 'stage', 'required': True}]}
        inputs = {'currency': 'UGX', 'materials': [{'materialId': 'test', 'quantity': 10, 'unit': 'kg', 'priceUnit': 'kg', 'price': 1.5,
                  'currency': 'USD', 'exchangeRate': 4000, 'priceIncludesVAT': True, 'purchaseTaxRate': 18}],
                  'processes': [{'stageId': 'stage', 'rate': 1000, 'quantity': 2, 'unit': 'kg', 'basis': 'per kg'}],
                  'overheads': 500, 'additionalCosts': 0, 'taxRate': 18, 'ledApplies': True, 'eligibleWeightKG': 10,
                  'usdToUGX': 4000, 'fxDate': timezone.localdate().isoformat(), 'ledBase': 'net-selling', 'vatOnLED': True}
        totals = calculate(inputs, child)
        self.assertEqual(totals['materialCost'], 50847.46)
        self.assertEqual(totals['productionCost'], 53347.46)
        self.assertEqual(totals['scenarios'][0]['led'], 2800)
        self.assertEqual([row['markup'] for row in totals['scenarios']], [10, 15, 20, 25])
        from rest_framework.exceptions import ValidationError
        for key, bad in [('exchangeRate', None), ('priceUnit', 'pcs'), ('quantity', 9)]:
            changed = deepcopy(inputs); changed['materials'][0][key] = bad
            with self.assertRaises(ValidationError):
                calculate(changed, child)

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
        self.prepare_quote(body)
        result = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['jobCardIds'], [])
        self.assertFalse(WorkflowJob.objects.exists())
        approved = self.approve_order(result.data)
        self.assertEqual(approved['status'], 'Approved')
        self.assertEqual(approved['jobCardIds'], [])
        self.assertFalse(WorkflowJob.objects.exists())
        body = deepcopy(self.body); body['creationKey'] = str(uuid4()); body['items'].append(stock)
        order, job = self.create_order(body)
        self.assertEqual(len(job['manufacturingItems']), 1)
        self.assertEqual(job['manufacturingItems'][0]['salesOrderItemId'], self.body['items'][0]['id'])
        self.assertFalse(RawMovement.objects.filter(material=self.raw).exists())

    def test_optional_allocation_and_safe_order_retry(self):
        self.body['allocateJobCard'] = False
        self.prepare_quote(self.body)
        response = self.client.post('/api/production-workflow/orders/', self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(WorkflowJob.objects.exists())
        key = response.data['id'].replace('wf-order-', '')
        self.assertEqual(self.client.post(f'/api/production-workflow/orders/{key}/allocate/', {}, format='json').status_code, 400)
        self.approve_order(response.data)
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
        self.prepare_quote(wrong)
        self.assertEqual(self.client.post('/api/production-workflow/orders/', wrong, format='json').status_code, 400)
        wrong = deepcopy(self.body); wrong['creationKey'] = str(uuid4()); wrong['items'][0]['modelRevision'] = 99
        self.prepare_quote(wrong)
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

    def test_sales_options_use_saved_customers_active_staff_and_latest_models(self):
        from .models import Customer
        active = Customer.objects.create(name='Saved customer')
        Customer.objects.create(name='Inactive customer', is_active=False)
        get_user_model().objects.create_user('inactive-staff', is_active=False)
        revised = deepcopy(self.model); revised['name'] = 'Latest saved model'
        self.client.post('/api/production-workflow/models/', revised, format='json')
        response = self.client.get('/api/production-workflow/order-options/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['customers'], [{'id': f'db:{active.pk}', 'name': active.name}])
        self.assertEqual(response.data['models'][0]['revision'], 2)
        self.assertEqual(len(response.data['models']), 1)
        self.assertFalse(any(row['name'] == 'inactive-staff' for row in response.data['staff']))
        self.assertTrue(all(set(row) == {'id', 'name', 'detail'} for row in response.data['staff']))
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/production-workflow/order-options/').status_code, 401)

    def test_converted_quotation_is_api_saved_with_metadata_and_does_not_duplicate(self):
        body = deepcopy(self.body); body['allocateJobCard'] = False
        body.update(quotationId=str(uuid4()), quotationNumber='QT-001', quotationRevisionId=str(uuid4()), quotationRevision=2,
                    customerContact={'contactPerson': 'Saved contact'}, commercialTerms={'paymentTerms': '30 days'})
        self.prepare_quote(body)
        result = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        saved = self.client.get('/api/production-workflow/orders/').data[0]
        for key in ('quotationId', 'quotationNumber', 'quotationRevisionId', 'quotationRevision', 'customerContact', 'commercialTerms'):
            self.assertEqual(saved[key], body[key])
        body['creationKey'] = str(uuid4())
        retried = self.client.post('/api/production-workflow/orders/', body, format='json')
        self.assertEqual(retried.data['id'], saved['id'])
        self.assertEqual(WorkflowOrder.objects.count(), 1)
        self.assertEqual(WorkflowJob.objects.count(), 0)
