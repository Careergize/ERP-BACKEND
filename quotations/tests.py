from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from .models import Delivery
from .delivery import send_email, send_whatsapp, DeliveryUnknown


def sample_document():
    data = json.loads(Path(__file__).with_name('sample.json').read_text())
    data['date'] = date.today().isoformat()
    return data


class QuotationTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('quoteadmin', role='production_manager', password='TestPassword-918!')
        self.client.force_authenticate(self.user)
        self.payload = sample_document()

    def create_quote(self, approve=True):
        response = self.client.post('/api/quotations/', self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        if approve:
            self.assertEqual(self.client.post(f'/api/quotations/{pk}/submit/').status_code, 200)
            self.assertEqual(self.client.post(f'/api/quotations/{pk}/approve/').status_code, 200)
        return pk

    def test_reference_total_snapshot_redaction_and_idempotent_create(self):
        self.payload['approved'] = True
        self.payload['totals'] = {'total': '1.00'}
        self.payload['private_cost'] = 'CONFIDENTIAL'
        self.payload['lines'][0]['margin'] = 'CONFIDENTIAL'
        pk = self.create_quote(False)
        data = self.client.get(f'/api/quotations/{pk}/').data
        self.assertFalse(data['approved'])
        self.assertEqual(data['document']['totals']['total'], '1717000.00')
        self.assertNotIn('CONFIDENTIAL', json.dumps(data, default=str))
        self.assertEqual(self.client.post('/api/quotations/', self.payload, format='json').data['id'], pk)

    def test_pdf_and_no_unapproved_send(self):
        pk = self.create_quote(False)
        response = self.client.get(f'/api/quotations/{pk}/pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/send/', {'request_id': str(uuid4()), 'channels': ['email'], 'email': 'customer@example.com'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f'/api/quotations/{pk}/', {}, format='json').status_code, 405)

    def test_permission_denied(self):
        user = get_user_model().objects.create_user('staff')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post('/api/quotations/', self.payload, format='json').status_code, 403)

    @patch('quotations.views.send_email', return_value='')
    @patch('quotations.views.configuration_error', return_value='')
    def test_sales_cannot_bypass_manager_approval(self, config, sender):
        from django.contrib.auth.models import Permission
        creator = get_user_model().objects.create_superuser('sales-creator', role='admin', password='Test-918-password!')
        self.client.force_authenticate(creator)
        pk = self.create_quote(False)
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/submit/').status_code, 200)
        body = {'request_id': str(uuid4()), 'channels': ['email'], 'email': 'buyer@example.com'}
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/approve/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/send/', body, format='json').status_code, 400)
        sender.assert_not_called()
        manager = get_user_model().objects.create_user('actual-manager', role='production_manager')
        manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='quotations', codename__in=['view_quotation', 'approve_quotation']))
        self.client.force_authenticate(manager)
        pending = self.client.get('/api/quotations/?pending=true')
        self.assertEqual(pending.data['results'][0]['id'], pk)
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/approve/').status_code, 200)
        self.client.force_authenticate(creator)
        self.assertTrue(self.client.get(f'/api/quotations/{pk}/').data['approved'])
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/send/', body, format='json').data['results'][0]['status'], 'accepted')
        sender.assert_called_once()
        # Editing any content creates a new, unapproved snapshot.
        self.payload['lines'][0]['unitPrice'] = '13000'
        changed = self.client.post('/api/quotations/', self.payload, format='json').data
        self.assertNotEqual(changed['id'], pk)
        self.assertFalse(changed['approved'])
        self.assertEqual(self.client.post(f"/api/quotations/{changed['id']}/send/", body, format='json').status_code, 400)

    def test_manager_must_have_permission_and_submission(self):
        pk = self.create_quote(False)
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/approve/').status_code, 400)
        self.client.post(f'/api/quotations/{pk}/submit/')
        manager = get_user_model().objects.create_user('unprivileged-manager', role='production_manager')
        self.client.force_authenticate(manager)
        self.assertEqual(self.client.post(f'/api/quotations/{pk}/approve/').status_code, 403)

    def test_validation(self):
        for change in [{'lines': []}, {'taxRate': '101'}, {'discount': '999999999'}, {'number': '../bad\\file'}]:
            data = deepcopy(self.payload); data.update(change)
            self.assertEqual(self.client.post('/api/quotations/', data, format='json').status_code, 400)

    def test_entered_price_precision_is_preserved_in_snapshot(self):
        self.payload['lines'][0]['unitPrice'] = '1234567890.123456'
        response = self.client.post('/api/quotations/', self.payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['document']['lines'][0]['unitPrice'], '1234567890.123456')

    def test_exclusive_tax(self):
        self.payload['lines'] = [{'description': 'Bag', 'group': '', 'specifications': '', 'quantity': '1', 'unit': 'pcs', 'unitPrice': '100'}]
        self.payload['taxMode'], self.payload['discount'] = 'exclusive', '10'
        response = self.client.post('/api/quotations/', self.payload, format='json')
        self.assertEqual(response.data['document']['totals']['total'], '106.20')

    def test_extracts_persisted_order_contact_and_line_details(self):
        from erp.models import Customer, Product, SalesOrder, SalesOrderLine
        customer = Customer.objects.create(code='C-Q', name='Backend buyer', email='buyer@example.com', phone='+916282744675', address='Buyer address')
        product = Product.objects.create(sku='FG-Q', name='Carrier bag', kind='finished_goods', unit='Kg')
        order = SalesOrder.objects.create(customer=customer, order_date=date.today())
        SalesOrderLine.objects.create(order=order, product=product, quantity='5', unit_price='100')
        data = {key: self.payload[key] for key in ['company', 'terms', 'currency', 'taxRate', 'taxMode']}
        data['sales_order_id'] = order.pk
        response = self.client.post('/api/quotations/extract/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['customer']['email'], 'buyer@example.com')
        self.assertEqual(response.data['lines'][0]['description'], 'Carrier bag')
        self.assertEqual(response.data['totals']['total'], '500.00')

    @patch('quotations.views.configuration_error', return_value='')
    @patch('quotations.views.send_whatsapp', return_value='wa-id')
    @patch('quotations.views.send_email', return_value='')
    def test_only_selected_channel_and_retries_do_not_duplicate(self, email, whatsapp, config):
        pk = self.create_quote()
        body = {'request_id': str(uuid4()), 'channels': ['email'], 'email': 'customer@example.com', 'phone': '+916282744675'}
        url = f'/api/quotations/{pk}/send/'
        response = self.client.post(url, body, format='json')
        self.assertEqual(response.data['results'][0]['status'], 'accepted')
        self.assertTrue(self.client.post(url, body, format='json').data['replayed'])
        email.assert_called_once(); whatsapp.assert_not_called()
        body['email'] = 'other@example.com'
        self.assertEqual(self.client.post(url, body, format='json').status_code, 409)

    @patch('quotations.views.configuration_error', return_value='')
    @patch('quotations.views.send_whatsapp', return_value='wa-id')
    @patch('quotations.views.send_email', side_effect=DeliveryUnknown('Check SMTP logs'))
    def test_partial_results_and_unknown_are_preserved(self, email, whatsapp, config):
        pk = self.create_quote()
        body = {'request_id': str(uuid4()), 'channels': ['email', 'whatsapp'], 'email': 'customer@example.com', 'phone': '+916282744675'}
        url = f'/api/quotations/{pk}/send/'
        response = self.client.post(url, body, format='json')
        self.assertEqual([d['status'] for d in response.data['results']], ['unknown', 'accepted'])
        self.client.post(url, body, format='json')
        self.assertEqual(email.call_count, 1)
        self.assertEqual(whatsapp.call_count, 1)

    def test_missing_configuration_and_invalid_recipient_send_nothing(self):
        pk = self.create_quote()
        url = f'/api/quotations/{pk}/send/'
        with patch('quotations.views.configuration_error', return_value='Configure SMTP'):
            response = self.client.post(url, {'request_id': str(uuid4()), 'channels': ['email'], 'email': 'customer@example.com'}, format='json')
            self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.post(url, {'request_id': str(uuid4()), 'channels': []}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'request_id': str(uuid4()), 'channels': ['whatsapp'], 'phone': '6282744675'}, format='json').status_code, 400)
        self.assertFalse(Delivery.objects.exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='sales@example.com')
    def test_email_has_real_pdf_attachment(self):
        from django.core import mail
        pk = self.create_quote()
        pdf = self.client.get(f'/api/quotations/{pk}/pdf/').content
        send_email('customer@example.com', self.payload, pdf, 'quote.pdf')
        self.assertEqual(mail.outbox[0].to, ['customer@example.com'])
        self.assertEqual(mail.outbox[0].attachments[0].mimetype, 'application/pdf')
        self.assertEqual(mail.outbox[0].attachments[0].content, pdf)

    @override_settings(WHATSAPP_GRAPH_VERSION='v-test', WHATSAPP_PHONE_NUMBER_ID='123', WHATSAPP_ACCESS_TOKEN='secret', WHATSAPP_TEMPLATE_NAME='quotation_pdf', WHATSAPP_TEMPLATE_LANGUAGE='en')
    @patch('quotations.delivery.requests.post')
    def test_whatsapp_uploads_document_and_submits_template(self, post):
        post.side_effect = [Mock(ok=True, json=lambda: {'id': 'media-123'}), Mock(ok=True, json=lambda: {'messages': [{'id': 'wa-123'}]})]
        result = send_whatsapp('+916282744675', self.payload, b'%PDF-example', 'quote.pdf')
        self.assertEqual(result, 'wa-123')
        self.assertEqual(post.call_args_list[0].kwargs['files']['file'][2], 'application/pdf')
        message = post.call_args_list[1].kwargs['json']
        self.assertEqual(message['to'], '916282744675')
        self.assertEqual(message['template']['components'][0]['parameters'][0]['document']['id'], 'media-123')
