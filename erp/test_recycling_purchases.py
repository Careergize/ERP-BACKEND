from copy import deepcopy
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import EntryRevision


class RecyclingPurchaseTests(APITestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.operator = User.objects.create_user('recycle-purchases', role='recycling_operator', assigned_role=Role.objects.get(code='recycling_operator'))
        self.manager = User.objects.create_user('purchase-manager', role='project_manager', assigned_role=Role.objects.get(code='project_manager'))
        self.client.force_authenticate(self.operator)
        self.row = dict(id='purchase1', date='2026-08-04', material='LD waste', supplier='Jingo', quantity='7', unit='kg', unitPrice='2500', currency='UGX', notes='', status='Active', correctionReason='')

    def save(self, rows, version=0, command=None):
        return self.client.put('/api/shared-entries/recycling-purchases/', {'content': rows, 'expectedVersion': version, 'commandId': command or str(uuid4())}, format='json')

    def report(self, **kwargs):
        return self.client.get('/api/recycling/purchases/report/', {'month': '2026-08', **kwargs})

    def test_operator_save_retry_manager_reload_and_actor_history(self):
        command = str(uuid4())
        self.assertEqual(self.save([self.row], command=command).status_code, 200)
        self.assertEqual(self.save([self.row], command=command).status_code, 200)
        self.assertEqual(EntryRevision.objects.count(), 1)
        self.client.force_authenticate(self.manager)
        data = self.report().data
        self.assertEqual(data['totals'], {'UGX': '17500.00'})
        self.assertEqual(data['entries'][0]['supplier'], 'Jingo')
        history = self.client.get('/api/management/activity/', {'key': 'recycling-purchases'}).data
        self.assertEqual(history['results'][0]['actorId'], str(self.operator.pk))
        self.assertEqual(self.save([self.row], version=1).status_code, 403)

    def test_month_boundaries_units_currencies_and_per_entry_rounding(self):
        rows = [self.row, {**self.row, 'id': 'p2', 'date': '2026-08-31', 'material': 'PEPSI', 'unit': 'pcs', 'quantity': '2000', 'unitPrice': '2700'},
                {**self.row, 'id': 'p3', 'currency': 'USD', 'quantity': '1.005', 'unitPrice': '1'},
                {**self.row, 'id': 'p4', 'date': '2026-09-01'}]
        self.assertEqual(self.save(rows).status_code, 200)
        data = self.report().data
        self.assertEqual(len(data['entries']), 3)
        self.assertEqual(data['totals'], {'UGX': '5417500.00', 'USD': '1.01'})
        self.assertEqual({r['unit'] for r in data['summary']}, {'kg', 'pcs'})
        self.assertEqual(self.report(month='2026-13').status_code, 400)

    def test_corrections_voids_and_deletion_retains_original(self):
        self.assertEqual(self.save([self.row]).status_code, 200)
        self.assertEqual(self.save([], 1).status_code, 400)
        edited = {**self.row, 'quantity': '8'}
        self.assertEqual(self.save([edited], 1).status_code, 400)
        edited['correctionReason'] = 'Corrected weighing entry'
        self.assertEqual(self.save([edited], 1).status_code, 200)
        voided = {**edited, 'status': 'Voided', 'correctionReason': 'Duplicate supplier receipt'}
        self.assertEqual(self.save([voided], 2).status_code, 200)
        self.assertEqual(self.report().data['totals'], {})
        self.assertEqual(len(self.report().data['entries']), 1)
        self.assertEqual(self.save([edited], 3).status_code, 400)
        self.assertEqual(EntryRevision.objects.order_by('id').first().content[0]['quantity'], '7')

    def test_invalid_values_and_stale_update(self):
        for field, value in [('quantity', '-1'), ('quantity', '0'), ('quantity', 'NaN'), ('quantity', '1.0001'), ('unitPrice', 'Infinity'), ('supplier', ' '), ('date', '2026-02-30'), ('unit', 'litres'), ('currency', 'ABC'), ('material', 'Invented'), ('notes', '\x00')]:
            with self.subTest(field=field, value=value):
                self.assertEqual(self.save([{**self.row, field: value}]).status_code, 400)
        self.assertEqual(self.save([self.row, deepcopy(self.row)]).status_code, 400)
        self.assertEqual(self.save([self.row]).status_code, 200)
        self.assertEqual(self.save([self.row]).status_code, 409)

    def test_native_excel_pdf_and_literal_formula_text(self):
        row = {**self.row, 'supplier': '=SUM(1,2)', 'notes': 'Supplier <reference> & details'}
        self.assertEqual(self.save([row]).status_code, 200)
        excel = self.report(export='xlsx')
        self.assertEqual(excel.status_code, 200)
        with ZipFile(BytesIO(excel.content)) as z:
            for name in z.namelist():
                ET.fromstring(z.read(name))
            details = z.read('xl/worksheets/sheet2.xml').decode()
            self.assertIn('=SUM(1,2)', details)
            self.assertNotIn('<f>', details)
            self.assertIn('<v>17500.00</v>', details)
        pdf = self.report(export='pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertIn('2026-08.pdf', pdf['Content-Disposition'])

    def test_anonymous_and_wrong_role_denied(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.report().status_code, 401)
        User = get_user_model()
        qc = User.objects.create_user('purchase-qc', role='qc_inspector', assigned_role=Role.objects.get(code='qc_inspector'))
        self.client.force_authenticate(qc)
        self.assertEqual(self.report().status_code, 403)
        self.assertEqual(self.save([self.row]).status_code, 403)

    def test_custom_material_persists_and_can_be_purchased(self):
        catalog = [{'id': 'material1', 'name': 'LD Red Film'}]
        def save_catalog(rows, version=0):
            return self.client.put('/api/shared-entries/recycling-materials/', {'content': rows, 'expectedVersion': version, 'commandId': str(uuid4())}, format='json')
        self.assertEqual(self.save([{**self.row, 'material': 'LD Red Film'}]).status_code, 400)
        self.assertEqual(save_catalog(catalog).status_code, 200)
        self.assertEqual(self.save([{**self.row, 'material': 'LD Red Film'}]).status_code, 200)
        self.assertEqual(save_catalog(catalog + [{'id': 'material2', 'name': 'ld red film'}], 1).status_code, 400)
        self.assertEqual(save_catalog([{'id': 'material2', 'name': 'LD waste'}], 1).status_code, 400)
        self.assertEqual(save_catalog([], 1).status_code, 400)
        self.client.force_authenticate(self.manager)
        saved = self.client.get('/api/shared-entries/').data['entries']
        self.assertEqual(next(r for r in saved if r['key'] == 'recycling-materials')['content'], catalog)
        self.assertEqual(self.report().data['summary'][0]['material'], 'LD Red Film')
