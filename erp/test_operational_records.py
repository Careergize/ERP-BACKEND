from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import SharedEntry, EntryRevision, FinishedStockItem, WorkflowOrder, WorkflowJob
from .operational_records import quality_reference, qc_result


class OperationalRecordTests(APITestCase):
    def setUp(self):
        self.users = {code: get_user_model().objects.create_user('op-' + code, role=code, assigned_role=Role.objects.get(code=code))
                      for code in ('admin', 'receptionist', 'store_keeper', 'production_manager', 'production_operator', 'accounts', 'qc_inspector', 'qc_supervisor')}
        self.client.force_authenticate(self.users['store_keeper'])
        self.today = timezone.localdate().isoformat()
        self.product = FinishedStockItem.objects.create(id='persisted-fg', name='Exact bag', category='packing', unit='ctn', opening_stock=10, opening_date=timezone.localdate() - timedelta(days=5))
        self.order = WorkflowOrder.objects.create(request_id=uuid4(), created_by=self.users['receptionist'], data={})
        self.order.data = {'id': f'wf-order-{self.order.pk}', 'orderNumber': 'SO-PERSIST', 'date': self.today,
            'status': 'Approved', 'salesOrderApproval': {'approvedBy': 'Manager'}, 'customerName': 'Saved buyer',
            'currency': 'USD', 'taxRate': 18, 'items': [{'id': 'line1', 'itemId': self.product.pk, 'itemName': 'Exact bag',
            'quantity': 10, 'unit': 'ctn', 'unitPrice': 2, 'fulfillmentSource': 'stock'}], 'jobCardIds': []}
        self.order.save()

    def put(self, key, content, command=None, expected=None):
        entry = SharedEntry.objects.filter(pk=key).first()
        return self.client.put(f'/api/shared-entries/{key}/', {'content': content, 'expectedVersion': expected if expected is not None else entry.version if entry else 0, 'commandId': command or str(uuid4())}, format='json')

    def ledger(self, amount=2):
        dispatch = {'id': 'dispatch1', 'number': 'DSP-1', 'allocationId': 'allocation1', 'orderId': self.order.data['id'],
            'itemId': 'line1', 'productId': self.product.pk, 'batch': 'reference-stock', 'quantity': amount, 'unit': 'ctn',
            'date': self.today, 'challan': 'CH-1', 'destination': 'Buyer store', 'vehicle': '', 'remarks': '', 'status': 'Confirmed'}
        movement = {'id': 'out-dispatch1', 'type': 'OUT', 'movementPurpose': 'sale', 'productId': self.product.pk,
            'quantity': amount, 'unit': 'ctn', 'date': self.today, 'salesOrderId': self.order.data['id'], 'salesOrderItemId': 'line1',
            'dispatchId': 'dispatch1', 'batch': 'reference-stock', 'reference': 'DSP-1'}
        allocation = {'id': 'allocation1', 'orderId': self.order.data['id'], 'itemId': 'line1', 'productId': self.product.pk,
            'batch': 'reference-stock', 'quantity': amount, 'dispatched': amount, 'released': True}
        return {'movements': [movement], 'allocations': [allocation], 'dispatches': [dispatch]}

    def inspection(self):
        template = next(t for t in quality_reference()['templates'] if t['category'] == 'safety')
        return {'id': 'inspection1', 'number': 'QC-1', 'template': template, 'subject': {'kind': 'safety', 'label': 'Production area'},
            'date': self.today, 'time': '10:00', 'department': 'Safety', 'inspectorId': str(self.users['qc_inspector'].pk),
            'inspector': 'op-qc_inspector', 'assignedTo': str(self.users['qc_inspector'].pk), 'sampleCount': 1,
            'responses': [], 'metadata': {}, 'comments': '', 'status': 'Draft', 'approvals': [], 'events': []}

    def passing_answers(self, inspection):
        result = []
        for item in inspection['template']['items']:
            answer = {'itemId': item['id'], 'sample': 1, 'value': item.get('acceptable') or 'Recorded', 'remarks': 'Checked'}
            if item['type'] == 'numeric':
                answer.update(value='10', target=10, lowerTolerance=0, upperTolerance=0, unit='kg')
            result.append(answer)
        return result

    def test_dispatch_is_saved_across_accounts_and_retry_is_idempotent(self):
        body = self.ledger(); command = str(uuid4())
        result = self.put('inventory-ledger', body, command, 0); self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.put('inventory-ledger', body, command, 0).status_code, 200)
        self.client.force_authenticate(self.users['accounts'])
        saved = next(row for row in self.client.get('/api/shared-entries/').data['entries'] if row['key'] == 'inventory-ledger')
        self.assertEqual(saved['content']['dispatches'][0]['quantity'], 2)
        self.assertEqual(EntryRevision.objects.filter(entry_id='inventory-ledger').count(), 1)

    def test_stock_overdraw_mismatch_and_fake_receipts_are_rejected(self):
        for edit in ('overdraw', 'unit', 'missing-out', 'forged-purchase'):
            body = self.ledger(11 if edit == 'overdraw' else 2)
            if edit == 'unit': body['movements'][0]['unit'] = 'kg'
            if edit == 'missing-out': body['movements'] = []
            if edit == 'forged-purchase': body['movements'][0]['purchaseOrderId'] = 'fake'
            with self.subTest(edit=edit):
                result = self.put('inventory-ledger', body); self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(SharedEntry.objects.filter(pk='inventory-ledger').exists())

    def test_reservation_checks_physical_and_batch_stock_without_movements(self):
        body = self.ledger(); body['movements'] = []; body['dispatches'] = []
        body['allocations'][0].update(quantity=5, dispatched=0, released=False, batch='unknown-batch')
        self.assertEqual(self.put('inventory-ledger', body).status_code, 400)
        self.product.opening_stock = 1; self.product.save()
        body['allocations'][0]['batch'] = 'reference-stock'
        self.assertEqual(self.put('inventory-ledger', body).status_code, 400)

    def test_invoice_server_checks_totals_and_prevents_duplicate_dispatch_billing(self):
        self.assertEqual(self.put('inventory-ledger', self.ledger()).status_code, 200)
        self.client.force_authenticate(self.users['accounts'])
        invoice = {'id': 'invoice1', 'number': 'INV-1', 'orderId': self.order.data['id'], 'date': self.today,
            'dispatchIds': ['dispatch1'], 'seller': 'Seller', 'billingAddress': 'Buyer address', 'currency': 'USD',
            'taxRate': 18, 'discount': 0, 'subtotal': 4, 'tax': .72, 'total': 4.72,
            'items': [{'itemId': 'line1', 'description': 'Exact bag', 'quantity': 2, 'unit': 'ctn', 'rate': 2, 'amount': 4}]}
        bad = deepcopy(invoice); bad['total'] = 0
        self.assertEqual(self.put('invoices', [bad]).status_code, 400)
        result = self.put('invoices', [invoice]); self.assertEqual(result.status_code, 200, result.data)
        duplicate = {**invoice, 'id': 'invoice2', 'number': 'INV-2'}
        self.assertEqual(self.put('invoices', [invoice, duplicate]).status_code, 400)
        self.client.force_authenticate(self.users['store_keeper'])
        reversed_ledger = self.ledger(); reversed_ledger['dispatches'][0]['status'] = 'Cancelled'
        reversed_ledger['allocations'][0]['dispatched'] = 0
        reversed_ledger['movements'].append({**reversed_ledger['movements'][0], 'id': 'reverse-dispatch1', 'type': 'IN', 'reversalOf': 'out-dispatch1', 'remarks': 'Requested reversal'})
        self.assertEqual(self.put('inventory-ledger', reversed_ledger).status_code, 400)

    def test_qc_draft_submit_independent_review_release_and_reopen_rejection(self):
        self.client.force_authenticate(self.users['qc_inspector'])
        i = self.inspection(); state = {'inspections': [i], 'releases': [], 'nonConformances': []}
        result = self.put('quality-state', state); self.assertEqual(result.status_code, 200, result.data)
        i['responses'] = self.passing_answers(i); i.update(status='Submitted', result=qc_result(i))
        result = self.put('quality-state', state); self.assertEqual(result.status_code, 200, result.data)
        i.update(status='Passed', approvals=[{'id': 'review1', 'decision': 'Passed', 'userId': str(self.users['qc_inspector'].pk), 'remarks': 'Checked'}])
        self.assertEqual(self.put('quality-state', state).status_code, 403)
        self.client.force_authenticate(self.users['qc_supervisor'])
        i['approvals'][0]['userId'] = str(self.users['qc_supervisor'].pk)
        result = self.put('quality-state', state); self.assertEqual(result.status_code, 200, result.data)
        i['status'] = 'Approved/Released'; state['releases'] = [{'id': 'release1', 'inspectionId': i['id'], 'subject': i['subject'], 'purpose': 'quality', 'userId': str(self.users['qc_supervisor'].pk)}]
        result = self.put('quality-state', state); self.assertEqual(result.status_code, 200, result.data)
        i['status'] = 'Draft'; self.assertEqual(self.put('quality-state', state).status_code, 400)

    def test_wrong_role_cannot_submit_production_or_invoices(self):
        self.client.force_authenticate(self.users['qc_inspector'])
        self.assertEqual(self.put('inventory-ledger', self.ledger()).status_code, 403)
        self.assertEqual(self.put('invoices', []).status_code, 403)

    def test_tracking_persists_stage_progress_and_rejects_stale_version_and_scope_changes(self):
        stage = {'id': 'stage1', 'name': 'Extrusion', 'sequence': 1, 'required': True, 'kind': 'production',
            'status': 'Pending', 'inputUnit': 'kg', 'outputUnit': 'kg', 'wasteUnit': 'kg', 'inputQuantity': None, 'outputQuantity': None, 'wasteQuantity': None}
        child = {'id': 'child1', 'qty': 10, 'unit': 'kg', 'status': 'MaterialIssued', 'approval': {'approvedBy': 'Manager'}, 'productionStages': [stage], 'activityHistory': [], 'stages': []}
        job = WorkflowJob.objects.create(data={}, version=1)
        job.data = {'id': 'wf-job-tracking-fixture', 'workflowKey': job.pk, 'workflowVersion': 1, 'status': 'MaterialIssued', 'manufacturingItems': [child], 'activityHistory': []}; job.save()
        card = deepcopy(job.data); changed = card['manufacturingItems'][0]
        changed['status'] = 'ReadyForStock'; now = timezone.now().isoformat()
        changed['productionStages'][0].update(status='Completed', inputQuantity=10, outputQuantity=9, wasteQuantity=1, operator='Operator', startedAt=now, completedAt=now)
        self.client.force_authenticate(self.users['production_operator'])
        body = {job.data['id']: {'expectedVersion': 1, 'card': card}}
        result = self.put('job-tracking', body); self.assertEqual(result.status_code, 200, result.data)
        job.refresh_from_db(); self.assertEqual(job.version, 2); self.assertEqual(job.data['status'], 'ReadyForStock')
        stale = deepcopy(body); stale[job.data['id']]['card']['manufacturingItems'][0]['department'] = 'Changed'
        self.assertEqual(self.put('job-tracking', stale).status_code, 400)
        stale[job.data['id']]['expectedVersion'] = 2; stale[job.data['id']]['card']['manufacturingItems'][0]['qty'] = 999
        self.assertEqual(self.put('job-tracking', stale).status_code, 400)
        job.refresh_from_db(); self.assertEqual(job.version, 2)

    def test_multi_record_operation_is_atomic_and_bundle_retries_do_not_duplicate(self):
        commands = [{'key': 'inventory-ledger', 'expectedVersion': 0, 'commandId': str(uuid4()), 'content': self.ledger()},
                    {'key': 'commercial-company', 'expectedVersion': 0, 'commandId': str(uuid4()), 'content': {'tradingName': 'Not authorized'}}]
        denied = self.client.post('/api/shared-entry-commands/', {'commands': commands}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(SharedEntry.objects.filter(pk='inventory-ledger').exists())
        self.assertEqual(EntryRevision.objects.count(), 0)
        commands[1] = {'key': 'inventory-pieces', 'expectedVersion': 0, 'commandId': str(uuid4()), 'content': {self.product.pk: 50}}
        result = self.client.post('/api/shared-entry-commands/', {'commands': commands}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.post('/api/shared-entry-commands/', {'commands': commands}, format='json').status_code, 200)
        self.assertEqual(EntryRevision.objects.count(), 2)

    def test_manufactured_partial_receipts_cannot_exceed_the_same_authorized_lot(self):
        self.product.opening_stock = 0; self.product.save()
        subject = {'kind': 'production', 'manufacturingItemId': 'manufactured-child', 'batchNumber': 'LOT-A', 'unit': 'ctn'}
        qc = {'inspections': [], 'nonConformances': [], 'releases': [
            {'id': 'qrelease', 'inspectionId': 'qc-prod', 'purpose': 'quality', 'subject': subject},
            {'id': 'stock-auth', 'inspectionId': 'qc-prod', 'purpose': 'finished_stock', 'subject': subject, 'acceptedQuantity': 10, 'rejectedQuantity': 0, 'unit': 'ctn'}]}
        SharedEntry.objects.create(key='quality-state', content=qc, updated_by=self.users['admin'])
        movement = {'id': 'receipt1', 'productId': self.product.pk, 'unit': 'ctn', 'type': 'IN', 'date': self.today,
            'quantity': 5, 'batch': 'LOT-A', 'reference': 'JC-1', 'manufacturingItemId': 'manufactured-child', 'stockApprovalId': 'stock-auth', 'qualityReleaseId': 'qrelease'}
        body = {'movements': [movement], 'allocations': [], 'dispatches': []}
        result = self.put('inventory-ledger', body); self.assertEqual(result.status_code, 200, result.data)
        body['movements'].append({**movement, 'id': 'receipt2'})
        result = self.put('inventory-ledger', body); self.assertEqual(result.status_code, 200, result.data)
        body['movements'].append({**movement, 'id': 'receipt3', 'quantity': 1})
        self.assertEqual(self.put('inventory-ledger', body).status_code, 400)

    def test_costing_approval_freezes_the_server_checked_financial_snapshot(self):
        self.client.force_authenticate(self.users['accounts'])
        inputs = {'quantity': 10, 'product': 'Costed bag', 'unit': 'ctn', 'currency': 'UGX', 'recipeMode': 'quantities',
            'materials': [{'id': 'material1', 'materialId': 'raw1', 'quantity': 2, 'unit': 'kg', 'priceUnit': 'kg', 'price': 5, 'currency': 'UGX', 'costGroup': 'material'}],
            'processes': [], 'wastePercent': 0, 'wasteBasis': 'materials', 'overheads': 0, 'additionalCosts': 0,
            'markupPercent': 10, 'taxPercent': 0, 'chargePercent': 0, 'minimumCharge': 0, 'pricingMethod': 'markup', 'taxOnCharges': True}
        totals = {'materialCost': 10, 'inkCost': 0, 'processCost': 0, 'wasteCost': 0, 'costOfProduction': 10,
            'sellingBeforeTax': 11, 'profit': 1, 'charges': 0, 'tax': 0, 'grandTotal': 11, 'unitSellingPrice': 1.1}
        estimate = {'id': 'estimate1', 'number': 'EST-1', 'revision': 1, 'createdAt': timezone.now().isoformat(), 'status': 'Draft', 'input': inputs, 'totals': totals}
        result = self.put('costing-estimates', [estimate]); self.assertEqual(result.status_code, 200, result.data)
        estimate['status'] = 'Pending Review'; self.assertEqual(self.put('costing-estimates', [estimate]).status_code, 200)
        estimate.update(status='Approved', approvedBy='op-accounts', approvedAt=timezone.now().isoformat())
        result = self.put('costing-estimates', [estimate]); self.assertEqual(result.status_code, 200, result.data)
        estimate['input']['markupPercent'] = 20
        self.assertEqual(self.put('costing-estimates', [estimate]).status_code, 400)
