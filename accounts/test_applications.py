from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from .models import FinanceEntitlement, Role


class ApplicationEntitlementTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='identity-test', password='test-only-unusable-outside-disposable-db', role='accounts')
        self.client.force_authenticate(self.user)

    def grant(self, **kwargs):
        return FinanceEntitlement.objects.create(user=self.user, frappe_user='fictional-test@example.invalid', **kwargs)

    def test_anonymous_direct_access_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/auth/applications/').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 401)

    def test_accounts_role_does_not_implicitly_grant_finance(self):
        data = self.client.get('/api/auth/applications/').data
        self.assertTrue(data['operations']['available'])
        self.assertFalse(data['finance']['entitled'])
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)

    def test_superuser_is_not_implicitly_entitled(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.assertFalse(self.client.get('/api/auth/applications/').data['finance']['entitled'])
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)

    def test_grants_default_disabled(self):
        self.grant()
        self.assertFalse(self.client.get('/api/auth/applications/').data['finance']['entitled'])

    def test_entitled_user_cannot_launch_without_real_sso(self):
        self.grant(enabled=True)
        response = self.client.get('/api/auth/applications/')
        self.assertTrue(response.data['finance']['entitled'])
        self.assertFalse(response.data['finance']['available'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        launch = self.client.post('/api/auth/finance/launch/', {'redirect_uri': 'https://evil.invalid', 'role':'admin'})
        self.assertEqual(launch.status_code, 503)
        self.assertFalse(launch.cookies)
        self.assertNotIn('Location', launch)
        for key in ('access', 'refresh', 'code_verifier', 'authorization_code', 'frappe_user'):
            self.assertNotIn(key, launch.data)

    def test_revocation_is_rechecked_without_relogin(self):
        grant = self.grant(enabled=True)
        self.assertTrue(self.client.get('/api/auth/applications/').data['finance']['entitled'])
        grant.enabled = False
        grant.save()
        self.assertFalse(self.client.get('/api/auth/applications/').data['finance']['entitled'])
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)

    def test_expired_and_disabled_users_denied(self):
        grant = self.grant(enabled=True, expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)
        grant.expires_at = timezone.now()+timedelta(days=1)
        grant.save()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)

    def test_blank_and_privileged_mappings_denied(self):
        grant = self.grant(enabled=True)
        for identity in ('', ' ', 'Administrator', ' administrator ', 'Guest'):
            grant.frappe_user = identity
            grant.save()
            self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 403)

    def test_clients_cannot_mutate_or_self_grant(self):
        self.assertEqual(self.client.post('/api/auth/applications/', {'enabled': True}).status_code, 405)
        self.assertEqual(FinanceEntitlement.objects.count(), 0)

    def test_existing_role_logins_and_profiles_unchanged(self):
        self.client.force_authenticate(None)
        for role in ('admin', 'receptionist', 'production_manager', 'qc_inspector', 'accounts'):
            self.user.assigned_role = Role.objects.get(code=role)
            self.user.save()
            response = self.client.post('/api/auth/login/', {'username':self.user.username, 'password':'test-only-unusable-outside-disposable-db'})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['user']['role'], role)
            self.client.credentials(HTTP_AUTHORIZATION='Bearer '+response.data['access'])
            self.assertEqual(self.client.get('/api/auth/me/').data['role'], role)
            self.assertFalse(self.client.get('/api/auth/applications/').data['finance']['entitled'])
            self.client.credentials()
