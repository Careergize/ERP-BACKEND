from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from .catalog_models import SpareItem, Consumable, SpareStockMovement
from .spare_service import post_movement, balance

class CatalogTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='catalog-test', password='synthetic-password')
        self.actor.user_permissions.set(Permission.objects.filter(content_type__app_label='inventory'))
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.item = SpareItem.objects.create(code='S-1', name='Synthetic', category='IMPORTED', unit='Each', created_by=self.actor)

    def command(self, **changes):
        return {'request_id': str(uuid4()), 'movement_type': 'IN', 'quantity': '10.000001', 'unit': 'Each', 'reference': 'Synthetic evidence', **changes}

    def opening(self):
        return post_movement(self.item.pk, self.command(is_opening=True), self.actor)

    def test_both_spare_categories_and_consumable_persist(self):
        for category in ['IMPORTED', 'LOCAL']:
            r = self.client.post('/api/spares/', {'code': category, 'name': category, 'category': category, 'unit': 'Both'})
            self.assertEqual(r.status_code, 201, r.data)
        r = self.client.post('/api/consumables/', {'code': 'C-1', 'name': 'Register only', 'unit': 'Litres'})
        self.assertEqual(r.status_code, 201, r.data)
        pk = r.data['id']
        self.assertEqual(self.client.patch(f'/api/consumables/{pk}/', {'description': 'Saved', 'is_active': False}).status_code, 200)
        self.assertEqual(Consumable.objects.get(pk=pk).description, 'Saved')
        self.assertEqual(self.client.get(f'/api/consumables/{pk}/').data['description'], 'Saved')
        self.assertNotIn('stock', r.data)
        self.assertEqual(self.client.post(f'/api/consumables/{pk}/movements/', {}).status_code, 404)
        self.assertEqual(self.client.get('/api/consumables/?is_active=false').data['count'], 1)

    def test_uniqueness_validation_filters_and_updates(self):
        self.assertEqual(self.client.post('/api/spares/', {'code': 'S-1', 'name': 'duplicate', 'category': 'LOCAL', 'unit': 'Each'}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/', {'minimum_stock': '-1'}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/', {'unit': 'kg'}).status_code, 400)
        self.assertEqual(self.client.get('/api/spares/?category=LOCAL').data['count'], 0)
        self.assertEqual(self.client.get('/api/spares/?search=S-1&unit=Each').data['count'], 1)
        self.opening()
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/', {'unit': 'Kilos'}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/', {'name': 'Edited'}).status_code, 200)
        self.assertEqual(SpareItem.objects.get(pk=self.item.pk).name, 'Edited')

    def test_authentication_and_permission(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/spares/').status_code, 401)
        other = get_user_model().objects.create_user(username='no-grants')
        self.client.force_authenticate(other)
        for url in ['/api/spares/', '/api/consumables/']:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/', {'name': 'No'}).status_code, 403)

    def test_posting_blocked_even_for_superuser(self):
        self.actor.is_superuser = True; self.actor.save()
        self.assertFalse(self.client.get('/api/spares/capabilities/').data['can_post'])
        self.assertEqual(self.client.post(f'/api/spares/{self.item.pk}/movements/', self.command()).status_code, 403)
        self.assertFalse(SpareStockMovement.objects.exists())

    def test_decimal_balances_idempotency_and_history(self):
        opening, created = self.opening()
        self.assertTrue(created)
        command = self.command(quantity='2.123456')
        row, _ = post_movement(self.item.pk, command, self.actor)
        replay, created = post_movement(self.item.pk, command, self.actor)
        self.assertFalse(created); self.assertEqual(row.pk, replay.pk)
        post_movement(self.item.pk, self.command(movement_type='OUT', quantity='1.000001'), self.actor)
        self.assertEqual(balance(self.item)['current_stock'], Decimal('11.123456'))
        self.assertEqual(self.client.get(f'/api/spares/{self.item.pk}/movements/').data['count'], 3)
        self.assertEqual(self.client.delete(f'/api/spares/{self.item.pk}/movements/').status_code, 405)
        self.assertEqual(self.client.patch(f'/api/spares/{self.item.pk}/movements/', {'quantity': '999'}).status_code, 405)
        with self.assertRaises(ValidationError): post_movement(self.item.pk, {**command, 'quantity': '3'}, self.actor)
        with self.assertRaises(ValidationError): self.opening()
        self.assertEqual(SpareStockMovement.objects.get(pk=opening.pk).quantity, Decimal('10.000001'))

    def test_rejections_do_not_write(self):
        with self.assertRaises(ValidationError): post_movement(self.item.pk, self.command(), self.actor)
        self.opening()
        for change in [{'quantity': '0'}, {'quantity': '-1'}, {'quantity': 'NaN'}, {'quantity': '0.0000001'}, {'unit': 'Kilos'}, {'movement_type': 'OUT', 'quantity': '11'}]:
            with self.subTest(change=change), self.assertRaises(ValidationError): post_movement(self.item.pk, self.command(**change), self.actor)
        self.assertEqual(self.item.movements.count(), 1)
        self.item.is_active = False; self.item.save()
        with self.assertRaises(ValidationError): post_movement(self.item.pk, self.command(), self.actor)

    def test_rollback_and_separate_items(self):
        self.opening()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                post_movement(self.item.pk, self.command(), self.actor)
                raise RuntimeError('Synthetic failure')
        self.assertEqual(self.item.movements.count(), 1)
        second = SpareItem.objects.create(code='S-2', name='Local', category='LOCAL', unit='Each', created_by=self.actor)
        self.assertIsNone(balance(second)['current_stock'])
        post_movement(second.pk, self.command(is_opening=True, quantity='0'), self.actor)
        self.assertEqual(balance(second)['current_stock'], 0)
        self.assertEqual(balance(self.item)['current_stock'], Decimal('10.000001'))
        with patch.object(SpareStockMovement.objects, 'create', side_effect=RuntimeError('Write failed')):
            with self.assertRaises(RuntimeError): post_movement(self.item.pk, self.command(), self.actor)
        self.assertEqual(self.item.movements.count(), 1)
