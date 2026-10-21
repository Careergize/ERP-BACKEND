from datetime import date
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from .models import Role


class ProjectManagerTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.creator = User.objects.create_superuser('salesadmin', role='admin', password='Test-1938-password!')
        self.manager = User.objects.create_user('projectmanager', password='Test-1938-password!', assigned_role=Role.objects.get(code='project_manager'))
        self.client.force_authenticate(self.creator)
        payload = json.loads((Path(__file__).resolve().parent.parent / 'quotations/sample.json').read_text())
        payload['date'] = date.today().isoformat()
        response = self.client.post('/api/quotations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.pk = response.data['id']
        self.client.post(f'/api/quotations/{self.pk}/submit/')

    def test_role_assignment_controls_login_profile_and_manager_permissions(self):
        self.client.force_authenticate(None)
        response = self.client.post('/api/auth/login/', {'username': 'projectmanager', 'password': 'Test-1938-password!'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['user']['role'], 'project_manager')
        self.assertTrue(self.manager.has_perm('quotations.view_quotation'))
        self.assertTrue(self.manager.has_perm('quotations.approve_quotation'))
        self.assertFalse(self.manager.has_perm('accounts.change_user'))
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        response = self.client.get('/api/quotations/?pending=true')
        self.assertEqual(response.data['results'][0]['id'], self.pk)
        self.assertEqual(self.client.get(f'/api/quotations/{self.pk}/').status_code, 200)
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/approve/').status_code, 200)

    @patch('quotations.views.send_email')
    @patch('quotations.views.send_whatsapp')
    def test_rejection_requires_reason_preserves_decision_and_blocks_delivery(self, whatsapp, email):
        self.client.force_authenticate(self.manager)
        url = f'/api/quotations/{self.pk}/reject/'
        self.assertEqual(self.client.post(url, {'reason': '   '}).status_code, 400)
        response = self.client.post(url, {'reason': 'Revise the price and delivery date.'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['reviewed_by'], 'projectmanager')
        self.assertFalse(response.data['approved'])
        self.assertEqual(self.client.get('/api/quotations/?pending=true').data['count'], 0)
        self.assertEqual(self.client.get('/api/quotations/?submitted=true').data['count'], 1)
        self.assertEqual(self.client.post(url, {'reason': 'Revise the price and delivery date.'}).status_code, 200)
        self.assertEqual(self.client.post(url, {'reason': 'Overwrite'}).status_code, 409)
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/approve/').status_code, 409)
        self.client.force_authenticate(self.creator)
        response = self.client.get(f'/api/quotations/{self.pk}/')
        self.assertEqual(response.data['rejection_reason'], 'Revise the price and delivery date.')
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/submit/').status_code, 409)
        body = {'request_id': str(uuid4()), 'channels': ['email', 'whatsapp'], 'email': 'buyer@example.com', 'phone': '+916282744675'}
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/send/', body, format='json').status_code, 400)
        email.assert_not_called(); whatsapp.assert_not_called()

    def test_other_roles_and_disabled_manager_cannot_review(self):
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/reject/', {'reason': 'Not authorized'}).status_code, 403)
        role = self.manager.assigned_role
        role.is_active = False; role.save()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.manager.pk))
        self.assertEqual(self.client.get('/api/quotations/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/approve/').status_code, 403)

    def test_approved_decision_cannot_be_rejected_later(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/approve/').status_code, 200)
        self.assertEqual(self.client.post(f'/api/quotations/{self.pk}/reject/', {'reason': 'Change decision'}).status_code, 409)

    def test_superuser_creates_role_assigned_user_without_elevating_staff(self):
        role = Role.objects.get(code='project_manager')
        self.assertEqual(self.client.get('/api/roles/').status_code, 200)
        response = self.client.post('/api/users/', {
            'username': 'new-manager', 'password': 'New-test-password-938!', 'assigned_role': role.pk,
            'is_superuser': True, 'is_staff': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('password', response.data)
        account = get_user_model().objects.get(username='new-manager')
        self.assertTrue(account.check_password('New-test-password-938!'))
        self.assertEqual(account.effective_role, 'project_manager')
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.client.force_authenticate(account)
        self.assertEqual(self.client.get('/api/quotations/?pending=true').status_code, 200)
        self.assertEqual(self.client.post('/api/users/', {'username': 'unauthorized'}).status_code, 403)
        self.assertEqual(self.client.get('/api/roles/').status_code, 403)

    def test_user_creation_rejects_weak_password_and_inactive_role(self):
        role = Role.objects.get(code='project_manager')
        response = self.client.post('/api/users/', {'username': 'weak', 'password': '123', 'assigned_role': role.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        role.is_active = False; role.save()
        response = self.client.post('/api/users/', {'username': 'disabled-role', 'password': 'Good-test-password-938!', 'assigned_role': role.pk}, format='json')
        self.assertEqual(response.status_code, 400)
