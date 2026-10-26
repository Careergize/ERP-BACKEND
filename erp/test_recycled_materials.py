from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import EntryRevision
from .recycled_materials import KEY, CATALOG_KEY


class RecycledMaterialTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.operator = get_user_model().objects.create_user('bags-operator', role='recycling_operator', assigned_role=Role.objects.get(code='recycling_operator'))
        self.manager = get_user_model().objects.create_user('bags-manager', role='project_manager', assigned_role=Role.objects.get(code='project_manager'))
        self.client.force_authenticate(self.operator)
        self.row = dict(id='bag1', date='2026-07-01', material='HDPE BlacK_Inhouse', bags='14', pricePerKg='3200', currency='UGX', notes='', status='Active', correctionReason='')

    def save(self, rows, version=0, command=None, key=KEY):
        return self.client.put(f'/api/shared-entries/{key}/', {'content': rows, 'expectedVersion': version, 'commandId': command or str(uuid4())}, format='json')

    def report(self, **kwargs):
        return self.client.get('/api/recycling/materials/report/', {'month': '2026-07', **kwargs})

    def test_calculations_daily_rollup_rate_currency_and_month(self):
        rows = [self.row, {**self.row, 'id': 'b2', 'bags': '7'}, {**self.row, 'id': 'b3', 'pricePerKg': '4000', 'date': '2026-07-31'},
                {**self.row, 'id': 'b4', 'currency': 'USD', 'pricePerKg': '0.0001', 'bags': '2'}, {**self.row, 'id': 'b5', 'date': '2026-08-01'}]
        self.assertEqual(self.save(rows).status_code, 200)
        data = self.report().data
        self.assertEqual(data['bags'], '37')
        self.assertEqual(data['kg'], '1480')
        self.assertEqual(data['totals'], {'UGX': '4928000.00', 'USD': '0.01'})
        self.assertEqual(len(data['summary']), 3)
        self.assertEqual(data['summary'][0]['daily']['2026-07-01'], '21')
        self.assertEqual(len(data['days']), 31)
        self.assertEqual(len(self.report(month='2028-02').data['days']), 29)
        for month in ['2026-13', '', '2026-7', '0000-01']:
            self.assertEqual(self.report(month=month).status_code, 400)

    def test_retry_conflict_and_audit_identity(self):
        command = str(uuid4())
        self.assertEqual(self.save([self.row], command=command).status_code, 200)
        self.assertEqual(self.save([self.row], command=command).status_code, 200)
        self.assertEqual(EntryRevision.objects.filter(entry_id=KEY).count(), 1)
        self.assertEqual(self.save([self.row]).status_code, 409)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.report().data['kg'], '560')
        self.assertEqual(self.save([self.row], 1).status_code, 403)
        history = self.client.get('/api/management/activity/', {'key': KEY}).data
        self.assertEqual(history['results'][0]['actorId'], str(self.operator.pk))

    def test_correction_void_and_no_delete(self):
        self.assertEqual(self.save([self.row]).status_code, 200)
        self.assertEqual(self.save([], 1).status_code, 400)
        updated = {**self.row, 'bags': '15'}
        self.assertEqual(self.save([updated], 1).status_code, 400)
        updated['correctionReason'] = 'Bag count corrected'
        self.assertEqual(self.save([updated], 1).status_code, 200)
        void = {**updated, 'status': 'Voided', 'correctionReason': 'Duplicate entry'}
        self.assertEqual(self.save([void], 2).status_code, 200)
        data = self.report().data
        self.assertEqual(data['bags'], '0')
        self.assertEqual(data['totals'], {})
        self.assertEqual(len(data['entries']), 1)
        self.assertEqual(self.save([updated], 3).status_code, 400)
        self.assertEqual(EntryRevision.objects.filter(entry_id=KEY).order_by('id').first().content[0]['bags'], '14')

    def test_bad_inputs_rejected_without_writing(self):
        for field, value in [('bags','0'), ('bags','-1'), ('bags','1.5'), ('bags','NaN'), ('bags','1000000001'), ('pricePerKg','1.00001'), ('pricePerKg','Infinity'), ('date','2026-02-30'), ('currency',''), ('material','Invented'), ('notes','\x00')]:
            with self.subTest(field=field, value=value):
                self.assertEqual(self.save([{**self.row, field:value}]).status_code, 400)
        self.assertEqual(self.save([self.row, self.row]).status_code, 400)
        self.assertEqual(self.save([{**self.row, 'kg':'999'}]).status_code, 400)
        self.assertEqual(EntryRevision.objects.filter(entry_id=KEY).count(), 0)

    def test_catalog_and_export(self):
        catalog = [{'id':'m1', 'name':'LD Red Film'}]
        self.assertEqual(self.save(catalog, key=CATALOG_KEY).status_code, 200)
        self.assertEqual(self.save([], 1, key=CATALOG_KEY).status_code, 400)
        self.assertEqual(self.save(catalog + [{'id':'m2','name':'ld red film'}], 1, key=CATALOG_KEY).status_code, 400)
        self.assertEqual(self.save([{**self.row, 'material':'LD Red Film', 'notes':'=SUM(1,2)'}]).status_code, 200)
        export = self.report(export='xlsx')
        self.assertEqual(export.status_code, 200)
        with ZipFile(BytesIO(export.content)) as z:
            for name in z.namelist():
                ET.fromstring(z.read(name))
            self.assertIn('=SUM(1,2)', z.read('xl/worksheets/sheet2.xml').decode())
            self.assertNotIn('<f>', z.read('xl/worksheets/sheet2.xml').decode())
            self.assertIn('<v>1792000.00</v>', z.read('xl/worksheets/sheet3.xml').decode())
        self.assertEqual(self.report(export='unknown').status_code, 400)

    def test_access_controls(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.report().status_code, 401)
        qc = get_user_model().objects.create_user('bags-qc', role='qc_inspector', assigned_role=Role.objects.get(code='qc_inspector'))
        self.client.force_authenticate(qc)
        self.assertEqual(self.report().status_code, 403)
        self.assertEqual(self.save([self.row]).status_code, 403)
        self.operator.is_active = False
        self.operator.save()
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.report().status_code, 403)
