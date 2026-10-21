from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APITestCase
from .models import Customer


class CustomerTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('customeradmin', password='Test-938-password!')
        self.client.force_authenticate(self.user)
        self.payload = {'name': 'Example Customer', 'contact_person': 'Customer Contact', 'phone': '+916282744675',
            'email': 'buyer@example.com', 'address': 'Billing address', 'delivery_address': 'Delivery address',
            'customer_type': 'B2B', 'tin': 'TIN-123', 'creation_key': str(uuid4())}

    def test_customer_contact_fields_saved_and_loaded_on_new_request(self):
        response = self.client.post('/api/customers/', self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        customer = Customer.objects.get(pk=response.data['id'])
        self.assertTrue(customer.code.startswith('CUS-'))
        self.assertEqual(customer.delivery_address, 'Delivery address')
        self.assertEqual(customer.contact_person, 'Customer Contact')
        self.assertNotIn('creation_key', response.data)
        from rest_framework.test import APIClient
        new_browser_request = APIClient()
        new_browser_request.force_authenticate(self.user)
        listing = new_browser_request.get('/api/customers/?active=true').data['results']
        self.assertEqual(listing[0]['id'], customer.pk)
        self.assertEqual(listing[0]['email'], 'buyer@example.com')

    def test_same_customer_save_request_does_not_duplicate(self):
        first = self.client.post('/api/customers/', self.payload, format='json')
        second = self.client.post('/api/customers/', self.payload, format='json')
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(Customer.objects.count(), 1)
        self.payload['name'] = 'Changed details'
        self.assertEqual(self.client.post('/api/customers/', self.payload, format='json').status_code, 409)
        self.assertEqual(Customer.objects.get().name, 'Example Customer')

    def test_edits_update_the_same_master(self):
        customer = self.client.post('/api/customers/', self.payload, format='json').data
        response = self.client.patch(f"/api/customers/{customer['id']}/", {'contact_person': 'New Contact', 'email': 'updated@example.com'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], customer['code'])
        self.assertEqual(Customer.objects.get().email, 'updated@example.com')

    def test_search_and_inactive_filter(self):
        self.client.post('/api/customers/', self.payload, format='json')
        Customer.objects.create(name='Inactive', is_active=False)
        result = self.client.get('/api/customers/?active=true&search=buyer@example.com')
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(self.client.get('/api/customers/?active=true').data['count'], 1)

    def test_customer_validation_and_permissions(self):
        self.payload['email'] = 'bad-email'
        self.assertEqual(self.client.post('/api/customers/', self.payload, format='json').status_code, 400)
        user = get_user_model().objects.create_user('customer-reader')
        user.user_permissions.add(Permission.objects.get(codename='view_customer'))
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/customers/').status_code, 200)
        self.payload['email'] = 'buyer@example.com'
        self.assertEqual(self.client.post('/api/customers/', self.payload, format='json').status_code, 403)

    def test_quotation_keeps_contact_snapshot_after_master_edit(self):
        import json
        from pathlib import Path
        from datetime import date
        from quotations.models import Quotation
        customer = self.client.post('/api/customers/', self.payload, format='json').data
        payload = json.loads((Path(__file__).resolve().parent.parent / 'quotations/sample.json').read_text())
        payload['date'] = date.today().isoformat()
        payload['customer_id'] = customer['id']
        payload['customer']['name'] = customer['name']
        response = self.client.post('/api/quotations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        quote = Quotation.objects.get(pk=response.data['id'])
        self.assertEqual(quote.customer_master_id, customer['id'])
        self.client.patch(f"/api/customers/{customer['id']}/", {'name': 'Renamed master'}, format='json')
        quote.refresh_from_db()
        self.assertEqual(quote.document['customer']['name'], 'Example Customer')
