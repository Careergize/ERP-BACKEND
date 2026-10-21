from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APITestCase
from .models import Unit


class UnitTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('unitadmin', password='Unit-test-password-109!')
        self.client.force_authenticate(self.user)
        self.document = json.loads((Path(__file__).resolve().parent.parent / 'quotations/sample.json').read_text())
        self.document['date'] = date.today().isoformat()

    def test_seed_and_create_unit_then_load_for_quotation(self):
        codes = set(Unit.objects.values_list('code', flat=True))
        self.assertTrue({'kg', 'ctn', 'pcs'}.issubset(codes))
        response = self.client.post('/api/units/', {'code': 'bundle', 'name': 'Bundle'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        unit = response.data
        self.assertTrue(any(row['id'] == unit['id'] for row in self.client.get('/api/units/?active=true').data['results']))
        self.document['lines'][0].update(unit='bundle', unit_id=unit['id'], quantity='3', unitPrice='125.50')
        response = self.client.post('/api/quotations/', self.document, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        line = response.data['document']['lines'][0]
        self.assertEqual(line['unit_id'], unit['id'])
        self.assertEqual(line['lineTotal'], '376.50')

    def test_unknown_inactive_and_mismatched_units_rejected(self):
        inactive = Unit.objects.create(code='inactive', name='Inactive unit', is_active=False)
        for code, unit_id in [('unknown', None), ('inactive', inactive.pk), ('kg', Unit.objects.get(code='ctn').pk)]:
            data = deepcopy(self.document)
            data['lines'][0]['unit'] = code
            if unit_id:
                data['lines'][0]['unit_id'] = unit_id
            self.assertEqual(self.client.post('/api/quotations/', data, format='json').status_code, 400)
        self.assertNotIn('inactive', [u['code'] for u in self.client.get('/api/units/?active=true').data['results']])

    def test_vat_inclusive_and_exclusive_line_totals_are_authoritative(self):
        for mode, expected_total in [('inclusive', '300.00'), ('exclusive', '354.00')]:
            data = deepcopy(self.document)
            data['taxMode'] = mode
            data['lines'] = [{'group': '', 'description': 'Test bags', 'specifications': '', 'quantity': '3', 'unit': 'ctn', 'unitPrice': '100', 'lineTotal': '1.00', 'unitPriceInclVat': '0'}]
            response = self.client.post('/api/quotations/', data, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            document = response.data['document']
            self.assertEqual(document['lines'][0]['lineTotal'], '300.00')
            self.assertEqual(Decimal(document['lines'][0]['unitPriceInclVat']), Decimal('100' if mode == 'inclusive' else '118'))
            self.assertEqual(document['totals']['total'], expected_total)

    def test_unit_uniqueness_code_immutability_and_permissions(self):
        self.assertEqual(self.client.post('/api/units/', {'code': 'KG', 'name': 'Duplicate'}, format='json').status_code, 400)
        kg = Unit.objects.get(code='kg')
        self.assertEqual(self.client.patch(f'/api/units/{kg.pk}/', {'code': 'other'}, format='json').status_code, 400)
        reader = get_user_model().objects.create_user('unit-reader')
        reader.user_permissions.add(Permission.objects.get(codename='view_unit'))
        self.client.force_authenticate(reader)
        self.assertEqual(self.client.get('/api/units/').status_code, 200)
        self.assertEqual(self.client.post('/api/units/', {'code': 'new', 'name': 'New'}, format='json').status_code, 403)
