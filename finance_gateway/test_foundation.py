from datetime import timedelta
from unittest.mock import Mock, patch
import socket
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken
from accounts.models import FinanceEntitlement
from .authorization import require_company_capability, GatewayDenied
from .credentials import NativeCredentials, resolve_credentials, CredentialsUnavailable
from .destinations import validate_destination
from .models import (FinanceSite, FinanceCompanyReference, FinanceIdentityBinding,
                     FinanceCompanyGrant, FinanceCapabilityGrant, FinanceAuditEvent)

URL = 'https://finance.example.test'
POLICY = {URL: {'site_name': 'finance.example.test', 'addresses': ['8.8.8.8']}}
DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


@override_settings(FINANCE_GATEWAY_ACCESS_ENABLED=True, FINANCE_GATEWAY_READS_ENABLED=True,
                   FINANCE_GATEWAY_DESTINATIONS=POLICY)
class FoundationTests(APITestCase):
    def setUp(self):
        self.dns = patch('finance_gateway.destinations.socket.getaddrinfo', return_value=DNS).start()
        self.addCleanup(patch.stopall)
        self.user = get_user_model().objects.create_user('reader')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.user)))
        self.entitlement = FinanceEntitlement.objects.create(user=self.user, enabled=True,
                                                            frappe_user='reader@example.test')
        self.site = FinanceSite.objects.create(reference='test', base_url=URL,
                    native_site_name='finance.example.test', enabled=True)
        self.company = FinanceCompanyReference.objects.create(site=self.site,
                    native_company_name='Native confidential company', display_name='Authorized Company', enabled=True)
        self.binding = FinanceIdentityBinding.objects.create(user=self.user, site=self.site,
                    native_username=self.entitlement.frappe_user, credential_reference='secret/never-expose', enabled=True)
        self.grant = FinanceCompanyGrant.objects.create(user=self.user, company=self.company, enabled=True)
        for capability in ('finance.access.read', 'finance.accounts.read'):
            FinanceCapabilityGrant.objects.create(company_grant=self.grant, capability=capability, enabled=True)
        self.credentials = NativeCredentials(self.binding.native_username, str(self.site.pk),
                    'key-never-expose', 'secret-never-expose', timezone.now() + timedelta(minutes=5))
        self.resolver = Mock()
        self.resolver.resolve.return_value = self.credentials
        patch('finance_gateway.credentials.get_resolver', return_value=self.resolver).start()

    def get(self):
        return self.client.get('/api/finance/v1/access/')

    def test_success_is_scoped_sanitized_and_audited(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['companies'], [{'reference': str(self.company.reference),
            'display_name': self.company.display_name,
            'capabilities': ['finance.access.read', 'finance.accounts.read']}])
        for value in ('Native confidential', URL, 'reader@example', 'secret/', 'never-expose'):
            self.assertNotIn(value, response.content.decode())
        self.assertFalse(response.data['available'])
        self.assertFalse(response.data['posting_enabled'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(FinanceAuditEvent.objects.get().outcome, 'allowed')

    def test_anonymous_and_bad_jwt(self):
        for header in ('', 'Bearer invalid'):
            self.client.credentials(HTTP_AUTHORIZATION=header)
            self.assertEqual(self.get().status_code, 401)
        self.assertEqual(FinanceAuditEvent.objects.count(), 2)
        self.resolver.resolve.assert_not_called()

    def test_entitlement_revocation_expiry_and_privileged_identity(self):
        for changes in ({'enabled': False}, {'expires_at': timezone.now()-timedelta(seconds=1)},
                        {'frappe_user': 'Administrator'}, {'frappe_user': 'Guest'}):
            FinanceEntitlement.objects.filter(pk=self.entitlement.pk).update(**changes)
            self.assertEqual(self.get().status_code, 403)
            FinanceEntitlement.objects.filter(pk=self.entitlement.pk).update(enabled=True,
                expires_at=None, frappe_user='reader@example.test')
        self.resolver.resolve.assert_not_called()

    def test_disabled_user(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.get().status_code, 401)

    def test_expired_jwt(self):
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(token))
        self.assertEqual(self.get().status_code, 401)
        self.resolver.resolve.assert_not_called()

    def test_no_grant_and_no_superuser_bypass(self):
        self.grant.enabled = False
        self.grant.save()
        for superuser, staff in ((False, False), (True, True)):
            self.user.is_superuser, self.user.is_staff = superuser, staff
            self.user.save()
            self.assertEqual(self.get().data['companies'], [])
            with self.assertRaises(GatewayDenied):
                require_company_capability(self.user, self.company.reference, 'finance.accounts.read')
        self.resolver.resolve.assert_not_called()

    def test_company_isolation_and_query_injection(self):
        other = FinanceCompanyReference.objects.create(site=self.site,
                    native_company_name='Hidden native', display_name='Hidden', enabled=True)
        response = self.client.get('/api/finance/v1/access/', {'company': str(other.reference), 'user': self.user.pk})
        self.assertNotIn('Hidden', response.content.decode())
        with self.assertRaises(GatewayDenied):
            require_company_capability(self.user, other.reference, 'finance.accounts.read')

    def test_capability_rechecked_after_discovery(self):
        self.assertEqual(self.get().status_code, 200)
        FinanceCapabilityGrant.objects.filter(capability='finance.accounts.read').update(enabled=False)
        with self.assertRaises(GatewayDenied):
            require_company_capability(self.user, self.company.reference, 'finance.accounts.read')
        with self.assertRaises(GatewayDenied):
            require_company_capability(self.user, self.company.reference, 'finance.post')

    def test_company_and_entitlement_rechecked_after_discovery(self):
        self.assertEqual(self.get().status_code, 200)
        self.grant.enabled = False
        self.grant.save()
        with self.assertRaises(GatewayDenied):
            require_company_capability(self.user, self.company.reference, 'finance.accounts.read')
        self.grant.enabled = True
        self.grant.save()
        self.entitlement.enabled = False
        self.entitlement.save()
        with self.assertRaises(GatewayDenied):
            require_company_capability(self.user, self.company.reference, 'finance.accounts.read')

    def test_all_grant_and_binding_expiries(self):
        for model, pk in ((FinanceCompanyGrant, self.grant.pk),
                          (FinanceIdentityBinding, self.binding.pk)):
            model.objects.filter(pk=pk).update(expires_at=timezone.now()-timedelta(seconds=1))
            self.assertEqual(self.get().data['companies'], [])
            model.objects.filter(pk=pk).update(expires_at=None)
        FinanceCapabilityGrant.objects.filter(capability='finance.access.read').update(
            expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.get().data['companies'], [])

    def test_missing_disabled_and_mismatched_binding(self):
        for changes in ({'enabled': False}, {'native_username': 'different@example.test'}):
            FinanceIdentityBinding.objects.filter(pk=self.binding.pk).update(**changes)
            self.assertEqual(self.get().data['companies'], [])
            FinanceIdentityBinding.objects.filter(pk=self.binding.pk).update(enabled=True,
                native_username=self.entitlement.frappe_user)
        self.binding.delete()
        self.assertEqual(self.get().data['companies'], [])

    def test_company_site_disabled(self):
        for obj in (self.company, self.site):
            obj.enabled = False
            obj.save()
            self.assertEqual(self.get().data['companies'], [])
            obj.enabled = True
            obj.save()

    def test_missing_revoked_expired_and_mismatched_credentials(self):
        for changes in ({'revoked': True}, {'native_username': 'wrong'}, {'site_id': 'wrong'},
                        {'expires_at': timezone.now()-timedelta(seconds=1)}, {'api_secret': ''}):
            values = dict(self.credentials.__dict__, **changes)
            self.resolver.resolve.return_value = NativeCredentials(**values)
            response = self.get()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data, {'code': 'credentials_unavailable'})
        self.resolver.resolve.side_effect = RuntimeError('secret-never-expose')
        self.assertNotIn('secret-never-expose', self.get().content.decode())
        self.binding.credential_reference = ''
        self.binding.save()
        self.assertEqual(self.get().status_code, 503)

    def test_default_resolver_is_unavailable(self):
        from .credentials import UnavailableResolver
        with patch('finance_gateway.credentials.get_resolver', return_value=UnavailableResolver()):
            with self.assertRaises(CredentialsUnavailable):
                resolve_credentials(self.binding)

    def test_disabled_gates(self):
        with override_settings(FINANCE_GATEWAY_ACCESS_ENABLED=False):
            response = self.get()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data, {'code': 'integration_disabled'})
            self.resolver.resolve.assert_not_called()
        with override_settings(FINANCE_GATEWAY_READS_ENABLED=False):
            with self.assertRaises(GatewayDenied):
                require_company_capability(self.user, self.company.reference, 'finance.accounts.read')

    def test_dns_change_and_allowlist_revocation(self):
        self.dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        self.assertEqual(self.get().data, {'code': 'destination_denied'})
        self.dns.return_value = DNS
        with override_settings(FINANCE_GATEWAY_DESTINATIONS={}):
            self.assertEqual(self.get().data, {'code': 'destination_denied'})
        self.resolver.resolve.assert_not_called()

    def test_audit_failure_denies_success_and_denial(self):
        with patch('finance_gateway.access.FinanceAuditEvent.objects.create', side_effect=RuntimeError('secret-never-expose')):
            for gate in (True, False):
                with override_settings(FINANCE_GATEWAY_ACCESS_ENABLED=gate):
                    response = self.get()
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn('secret-never-expose', response.content.decode())
                    self.assertNotIn('companies', response.data)
            self.client.credentials()
            self.assertEqual(self.get().status_code, 503)

    def test_database_error_sanitized(self):
        with patch('finance_gateway.access.effective_companies', side_effect=RuntimeError('database-password')):
            response = self.get()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data, {'code': 'integration_unavailable'})
            self.assertEqual(FinanceAuditEvent.objects.get().reason_code, 'integration_unavailable')

    def test_audit_append_only(self):
        self.get()
        event = FinanceAuditEvent.objects.get()
        for operation in (lambda: event.save(), lambda: event.delete(),
                          lambda: FinanceAuditEvent.objects.all().update(reason_code='ok'),
                          lambda: FinanceAuditEvent.objects.all().delete(),
                          lambda: FinanceAuditEvent.objects.bulk_update([event], ['reason_code']),
                          lambda: FinanceAuditEvent.objects.bulk_create([event])):
            with self.assertRaises(ValidationError):
                operation()

    def test_invalid_capability_at_application_and_database_layers(self):
        with self.assertRaises(ValidationError):
            FinanceCapabilityGrant.objects.create(company_grant=self.grant, capability='finance.post')
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinanceCapabilityGrant.objects.filter(capability='finance.accounts.read').update(capability='finance.post')

    def test_unique_identity_company_and_capability(self):
        with self.assertRaises(ValidationError):
            FinanceCapabilityGrant.objects.create(company_grant=self.grant, capability='finance.access.read')
        other = get_user_model().objects.create_user('other')
        with self.assertRaises(ValidationError):
            FinanceIdentityBinding.objects.create(user=other, site=self.site,
                native_username=self.binding.native_username.upper())

    def test_no_financial_writes_or_side_effects(self):
        from accounting.models import Document, JournalLine, PostingEvent
        before = [m.objects.count() for m in (Document, JournalLine, PostingEvent)]
        self.get()
        self.assertEqual(before, [m.objects.count() for m in (Document, JournalLine, PostingEvent)])
        self.assertEqual(self.client.post('/api/finance/v1/access/', {}).status_code, 405)
        for route in ('invoices', 'payments', 'journals', 'activate', 'sync'):
            self.assertEqual(self.client.post('/api/finance/v1/'+route+'/', {}).status_code, 404)


@override_settings(DEBUG=False, FINANCE_GATEWAY_DESTINATIONS=POLICY)
class DestinationTests(SimpleTestCase):
    def test_unapproved_urls_never_resolve(self):
        with patch('finance_gateway.destinations.socket.getaddrinfo') as dns:
            for url in ('http://finance.example.test', 'https://evil.test', URL+'/api',
                        URL+'?x=1', URL+'#fragment', 'https://user:pass@finance.example.test',
                        'https://127.0.0.1', 'https://169.254.169.254', URL+':99999'):
                with self.assertRaises(ValidationError):
                    validate_destination(url, 'finance.example.test')
            dns.assert_not_called()

    def test_dns_unexpected_private_mixed_and_empty(self):
        for addresses in (['127.0.0.1'], ['169.254.169.254'], ['8.8.8.8', '10.0.0.1'], [], ['8.8.4.4']):
            with patch('finance_gateway.destinations.socket.getaddrinfo', return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in addresses]):
                with self.assertRaises(ValidationError):
                    validate_destination(URL, 'finance.example.test')

    def test_explicit_local_development_only(self):
        url = 'http://finance.localhost:8082'
        policy = {url: {'site_name': 'finance.localhost', 'addresses': ['127.0.0.1'], 'local_development': True}}
        with override_settings(FINANCE_GATEWAY_DESTINATIONS=policy), patch(
                'finance_gateway.destinations.socket.getaddrinfo', return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 8082))]):
            with self.assertRaises(ValidationError):
                validate_destination(url, 'finance.localhost')
            with override_settings(DEBUG=True):
                self.assertEqual(validate_destination(url, 'finance.localhost'), ('127.0.0.1',))
