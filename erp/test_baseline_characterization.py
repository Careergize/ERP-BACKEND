"""Task 01 regression evidence; unsafe observations are NOT approved requirements.

Synthetic records only. Existing tests remain unchanged. Replace unsafe observations
with denial tests when the corresponding approved Phase 1 correction is implemented.
"""
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import RawMaterial, RawMovement
from .models import FinishedStockItem, WorkflowOrder, WorkflowJob
from . import test_production_workflow as workflow_fixtures


class WorkflowBaselineTests(TestCase):
    def setUp(self):
        workflow_fixtures.ProductionWorkflowTests.setUp(self)
        self.initial_movements = list(RawMovement.objects.values())

    create_order = workflow_fixtures.ProductionWorkflowTests.create_order
    action = workflow_fixtures.ProductionWorkflowTests.action

    def test_two_manufacturing_lines_and_stock_share_one_snapshot_parent(self):
        second = deepcopy(self.body['items'][0])
        second.update(id=str(uuid4()), quantity=10, itemName='Second manufactured line')
        stock = FinishedStockItem.objects.filter(unit='ctn').first()
        self.assertIsNotNone(stock)
        stock_line = {'id': str(uuid4()), 'itemId': stock.pk, 'itemName': stock.name,
                      'quantity': 1, 'unit': stock.unit, 'unitPrice': 2, 'fulfillmentSource': 'stock'}
        self.body['items'].extend([second, stock_line])
        order, job = self.create_order()
        self.assertEqual(len(order['items']), 3)
        self.assertEqual(WorkflowJob.objects.count(), 1)
        self.assertEqual(order['jobCardIds'], [job['id']])
        self.assertEqual(len(job['manufacturingItems']), 2)
        self.assertEqual([c['requiredMaterials'][0]['requiredQuantity'] for c in job['manufacturingItems']], [10, 5])
        self.assertTrue(all(c['parentJobCardId'] == job['id'] for c in job['manufacturingItems']))
        self.assertEqual(list(RawMovement.objects.values()), self.initial_movements)
        revised = deepcopy(self.model)
        revised['name'] = 'New revision name'
        response = self.client.post('/api/production-workflow/models/', revised, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        original = WorkflowJob.objects.get().data
        self.assertTrue(all(c['modelRevision'] == 1 for c in original['manufacturingItems']))
        self.assertEqual(original, job)

    def test_multi_material_failure_rolls_back_earlier_movement_and_job_json(self):
        _, job = self.create_order()
        child = job['manufacturingItems'][0]
        # First item sorts before the insufficient second item, exercising rollback
        # after a successful first insert, not only validation before any insert.
        extra = RawMaterial.objects.create(id='zz-baseline-insufficient', name='Synthetic second grade',
            family='Test', category='virgin', unit='kg', opening_stock=0, opening_date=timezone.localdate())
        rows = deepcopy(child['requiredMaterials'])
        rows.append({'id': str(uuid4()), 'materialId': extra.pk, 'name': extra.name,
                     'unit': 'kg', 'requiredQuantity': 1, 'issuedQuantity': 0})
        edited = self.action(job, 'edit', 'production_manager', itemId=child['id'], change={'requiredMaterials': rows})
        self.assertEqual(edited.status_code, 200, edited.data)
        approved = self.action(edited.data, 'approve', 'production_manager')
        self.assertEqual(approved.status_code, 200, approved.data)
        before = deepcopy(WorkflowJob.objects.get().data)
        version = WorkflowJob.objects.get().version
        result = self.action(approved.data, 'issue', 'store_keeper', itemId=child['id'],
                             quantities={row['id']: 1 for row in rows})
        self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(list(RawMovement.objects.values()), self.initial_movements)
        saved = WorkflowJob.objects.get()
        self.assertEqual(saved.data, before)
        self.assertEqual(saved.version, version)

    def test_job_wrong_saved_unit_rejects_issue_without_stock_change(self):
        _, job = self.create_order()
        child = job['manufacturingItems'][0]
        approved = self.action(job, 'approve', 'production_manager')
        self.assertEqual(approved.status_code, 200)
        # Simulate a legacy master inconsistency entirely inside this test DB.
        self.raw.unit = 'g'
        self.raw.save(update_fields=['unit'])
        result = self.action(approved.data, 'issue', 'store_keeper', itemId=child['id'],
                             quantities={child['requiredMaterials'][0]['id']: 1})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(list(RawMovement.objects.values()), self.initial_movements)

    def test_legacy_json_orders_and_jobs_read_without_new_control_fields(self):
        order, job = self.create_order()
        fresh = APIClient()
        fresh.force_authenticate(self.users['store_keeper'])
        self.assertEqual(fresh.get('/api/production-workflow/orders/').data, [order])
        self.assertEqual(fresh.get('/api/production-workflow/jobs/').data, [job])
        self.assertNotIn('commercialStatus', WorkflowOrder.objects.get().data)

    def test_workflow_requires_real_jwt_and_permission_not_display_role(self):
        fresh = APIClient()
        self.assertEqual(fresh.get('/api/production-workflow/orders/').status_code, 401)
        user = get_user_model().objects.create_user('baseline-no-grants', password='Baseline-only-928!', role='admin')
        response = fresh.post('/api/auth/login/', {'username': user.username, 'password': 'Baseline-only-928!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        fresh.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(fresh.get('/api/production-workflow/orders/').status_code, 403)
        self.assertEqual(fresh.post('/api/production-workflow/orders/', self.body, format='json').status_code, 403)
        self.assertFalse(WorkflowOrder.objects.exists())

    @tag('unsafe_characterization')
    def test_unsafe_current_draft_allocation_and_same_corrector_approval(self):
        order, job = self.create_order()
        self.assertEqual(order['status'], 'Draft', 'G04 observation, not approved future behavior')
        child = job['manufacturingItems'][0]
        changed = self.action(job, 'edit', 'production_manager', itemId=child['id'], change={'specifications': 'Same PM correction'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.action(changed.data, 'approve', 'production_manager').status_code, 200,
                         'G05 observation: future independent-checker implementation must reject this')

    @tag('unsafe_characterization')
    def test_unsafe_quote_lineage_is_not_exposed_as_top_level_order_fields(self):
        self.body.update(quotationId='synthetic-quote-root', quotationRevisionId='synthetic-quote-revision',
                         quotationNumber='QT-BASELINE', quotationRevision=1)
        order, _ = self.create_order()
        self.assertEqual(order['entryRequest']['quotationId'], 'synthetic-quote-root')
        self.assertNotIn('quotationId', order,
                         'Current persistence mismatch: frontend conversion duplicate lookup expects this field')
        self.assertEqual(WorkflowOrder.objects.get().data, order)

    @tag('unsafe_characterization')
    def test_unsafe_direct_out_requires_no_persisted_quality_evidence(self):
        fresh = APIClient()
        fresh.force_authenticate(self.users['store_keeper'])
        body = {'id': str(uuid4()), 'materialId': self.raw.pk, 'unit': 'kg',
                'date': timezone.localdate().isoformat(), 'movementType': 'OUT', 'quantity': '1'}
        result = fresh.post('/api/raw-inventory/movements/', body, format='json')
        self.assertEqual(result.status_code, 201, 'G02 missing-evidence observation, not QC approval')
        self.assertEqual(RawMovement.objects.get(pk=body['id']).quantity, Decimal('1'))
        self.assertEqual(RawMovement.objects.count(), len(self.initial_movements) + 1)


class DocumentBaselineTests(TestCase):
    def setUp(self):
        self.maker = get_user_model().objects.create_superuser('baseline-maker', password='Baseline-only-928!', role='admin')
        self.checker = get_user_model().objects.create_superuser('baseline-checker', password='Baseline-only-929!', role='production_manager')
        self.client = APIClient()
        self.client.force_authenticate(self.maker)

    def test_po_partial_damage_missing_retry_conflict_and_historical_reads(self):
        from .test_grn import GRNTests
        fixture = GRNTests()
        fixture.setUp()
        self.client = fixture.client
        created = self.client.post('/api/goods-received-notes/', fixture.body, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(Decimal(created.data['lines'][0]['quantity_accepted']), Decimal('7'))
        self.assertEqual(self.client.post('/api/goods-received-notes/', fixture.body, format='json').data['id'], created.data['id'])
        changed = deepcopy(fixture.body)
        changed['lines'][0]['quantity_received'] = '7'
        self.assertEqual(self.client.post('/api/goods-received-notes/', changed, format='json').status_code, 409)
        self.assertEqual(self.client.get(f"/api/goods-received-notes/{created.data['id']}/").data, created.data)
        po = self.client.get(f"/api/purchase-orders/{fixture.po['id']}/").data
        self.assertEqual(Decimal(po['lines'][0]['quantity_remaining']), Decimal('2'))
        changed['request_id'] = str(uuid4())
        self.assertEqual(self.client.post('/api/goods-received-notes/', changed, format='json').status_code, 400)
        self.assertEqual(len(fixture.receipts()), 1)

    @patch('quotations.views.configuration_error', return_value='')
    @patch('quotations.views.send_email', return_value='baseline-mocked-provider-id')
    def test_independent_quote_review_pdf_and_delivery_replay(self, send, configuration):
        from quotations.tests import sample_document
        quote = self.client.post('/api/quotations/', sample_document(), format='json')
        self.assertEqual(quote.status_code, 201, quote.data)
        url = f"/api/quotations/{quote.data['id']}/"
        self.assertEqual(self.client.post(url + 'submit/').status_code, 200)
        self.client.force_authenticate(self.checker)
        approved = self.client.post(url + 'approve/')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertTrue(approved.data['approved'])
        self.client.force_authenticate(self.maker)
        pdf = self.client.get(url + 'pdf/')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        body = {'request_id': str(uuid4()), 'channels': ['email'], 'email': 'baseline@example.com'}
        first = self.client.post(url + 'send/', body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.client.post(url + 'send/', body, format='json').data['results'], first.data['results'])
        send.assert_called_once()
