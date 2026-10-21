from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APITestCase
from .models import Customer, Product, SalesOrder, SalesOrderLine, JobCard


class BackendTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('admin', password='A-test-password-592!')
        self.customer = Customer.objects.create(name='Example customer', code='C001')
        self.product = Product.objects.create(sku='FG001', name='Bag', unit='pcs', kind='finished_goods')
        self.payload = {
            'customer': self.customer.pk, 'order_date': '2026-10-15',
            'lines': [{'product': self.product.pk, 'quantity': '10.000', 'unit_price': '2.50'}],
        }

    def test_health_and_anonymous_access(self):
        self.assertEqual(self.client.get('/api/health/').status_code, 200)
        self.assertEqual(self.client.get('/api/customers/').status_code, 401)

    def test_login_refresh_and_logout(self):
        response = self.client.post('/api/auth/login/', {'username': 'admin', 'password': 'A-test-password-592!'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['username'], 'admin')
        self.assertEqual(response.data['user']['role'], 'admin')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
        refreshed = self.client.post('/api/auth/refresh/', {'refresh': response.data['refresh']})
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': response.data['refresh']}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/logout/', {'refresh': refreshed.data['refresh']}).status_code, 200)
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': refreshed.data['refresh']}).status_code, 401)

    def test_display_role_does_not_grant_permissions(self):
        user = get_user_model().objects.create_user('viewer', role='admin', password='test-password')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/customers/').status_code, 403)
        user.user_permissions.add(Permission.objects.get(codename='view_customer'))
        self.client.force_authenticate(get_user_model().objects.get(pk=user.pk))
        self.assertEqual(self.client.get('/api/customers/').status_code, 200)
        self.assertEqual(self.client.post('/api/customers/', {'name': 'Other', 'code': 'C002'}).status_code, 403)

    def test_order_creation_calculates_total_and_ignores_client_status(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/sales-orders/', {**self.payload, 'status': 'completed'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total'], '25.00')
        self.assertEqual(response.data['status'], 'draft')
        self.assertEqual(SalesOrderLine.objects.count(), 1)
        self.assertEqual(JobCard.objects.count(), 0)

    def test_invalid_line_does_not_create_partial_order(self):
        self.client.force_authenticate(self.admin)
        self.payload['lines'].append({'product': self.product.pk, 'quantity': '-1', 'unit_price': '2'})
        response = self.client.post('/api/sales-orders/', self.payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SalesOrder.objects.exists())
        self.assertFalse(SalesOrderLine.objects.exists())

    def test_delivery_date_validation(self):
        self.client.force_authenticate(self.admin)
        self.payload['delivery_date'] = '2026-10-14'
        self.assertEqual(self.client.post('/api/sales-orders/', self.payload, format='json').status_code, 400)

    def test_one_parent_job_per_order(self):
        self.client.force_authenticate(self.admin)
        order = self.client.post('/api/sales-orders/', self.payload, format='json').data
        response = self.client.post('/api/job-cards/', {'sales_order': order['id']})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.post('/api/job-cards/', {'sales_order': order['id']}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/sales-orders/{order['id']}/", {'notes': 'edit'}).status_code, 405)

    def test_stock_production_job_needs_no_sales_order(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/job-cards/', {'notes': 'Production for stock'})
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['sales_order'])
