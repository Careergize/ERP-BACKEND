import json
from datetime import timedelta
from unittest.mock import patch
import requests
from pathlib import Path
import shutil
import subprocess
import base64
import re
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken
from accounts.models import FinanceEntitlement, Role
from accounting.models import Document, JournalLine, PostingEvent
from .adapter import connection_status


class FinanceGatewayTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='finance-reader', role='accounts')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.user)))
        self.permission = Permission.objects.get(content_type__app_label='accounts', codename='view_financeentitlement')
        self.transport = patch('finance_gateway.views.connection_status', return_value='authenticated')
        self.upstream = self.transport.start()
        self.addCleanup(self.transport.stop)

    def grant(self):
        self.user.user_permissions.add(self.permission)
        return FinanceEntitlement.objects.create(user=self.user, enabled=True, frappe_user='finance-user@example.invalid')

    def test_anonymous_and_invalid_jwt_make_no_upstream_request(self):
        for token in ('', 'Bearer invalid'):
            self.client.credentials(HTTP_AUTHORIZATION=token)
            response = self.client.get('/api/finance/status/')
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['Cache-Control'], 'no-store')
        self.upstream.assert_not_called()

    def test_role_superuser_or_permission_alone_never_grants_entitlement(self):
        self.user.user_permissions.add(self.permission)
        for role in ('admin', 'accounts', 'receptionist'):
            self.user.role = role
            self.user.is_superuser = True
            self.user.save()
            self.assertEqual(self.client.get('/api/finance/status/').status_code, 403)
        self.upstream.assert_not_called()

    def test_entitlement_without_action_permission_denied(self):
        FinanceEntitlement.objects.create(user=self.user, enabled=True, frappe_user='user@example.invalid')
        self.assertEqual(self.client.get('/api/finance/status/').status_code, 403)
        self.upstream.assert_not_called()

    def test_revoked_expired_privileged_and_disabled_users_make_no_calls(self):
        grant = self.grant()
        for identity, enabled, expires in [('Guest', True, None), ('Administrator', True, None), (' ', True, None),
                                          ('user@example.invalid', False, None),
                                          ('user@example.invalid', True, timezone.now() - timedelta(seconds=1))]:
            grant.frappe_user, grant.enabled, grant.expires_at = identity, enabled, expires
            grant.save()
            self.assertEqual(self.client.get('/api/finance/status/').status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get('/api/finance/status/').status_code, 401)
        self.upstream.assert_not_called()

    def test_authenticated_diagnostic_never_enables_app_or_launches_or_writes(self):
        self.grant()
        before = [model.objects.count() for model in (Document, JournalLine, PostingEvent, FinanceEntitlement)]
        response = self.client.get('/api/finance/status/?url=https://ignored.invalid&user=Administrator')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'available': False, 'reason': 'frappe_sso_not_configured',
                                        'connection': 'authenticated', 'read_only': True,
                                        'accounting_setup': 'not_assessed', 'posting_enabled': False})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(response.cookies)
        self.assertFalse(self.client.get('/api/auth/applications/').data['finance']['available'])
        self.assertEqual(self.client.post('/api/auth/finance/launch/').status_code, 503)
        self.assertEqual(before, [model.objects.count() for model in (Document, JournalLine, PostingEvent, FinanceEntitlement)])
        self.upstream.assert_called_once_with()

    def test_status_errors_are_sanitized_and_unavailable(self):
        self.grant()
        for state in ('not_configured', 'timeout', 'permission_denied', 'invalid_response', 'unavailable'):
            self.upstream.return_value = state
            response = self.client.get('/api/finance/status/')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.data['available'])
            self.assertEqual(set(response.data), {'available', 'reason', 'connection', 'read_only',
                                                 'accounting_setup', 'posting_enabled'})
            self.assertEqual(response.data['accounting_setup'], 'not_assessed')
            self.assertIs(response.data['posting_enabled'], False)

    def test_readiness_cannot_be_enabled_or_assessed_by_client_inputs(self):
        self.grant()
        for state in ('authenticated', 'not_configured', 'permission_denied', 'timeout', 'invalid_response', 'unavailable'):
            self.upstream.return_value = state
            response = self.client.get('/api/finance/status/?available=true&posting_enabled=true&accounting_setup=approved')
            self.assertIs(response.data['available'], False)
            self.assertIs(response.data['posting_enabled'], False)
            self.assertEqual(response.data['accounting_setup'], 'not_assessed')
            self.assertIs(response.data['read_only'], True)

    def test_mutating_methods_rejected_without_upstream_calls(self):
        self.grant()
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)('/api/finance/status/', {}).status_code, 405)
        self.upstream.assert_not_called()

    def test_existing_assigned_role_permissions_can_authorize_diagnostics(self):
        grant = self.grant()
        self.user.user_permissions.clear()
        role = Role.objects.get(code='accounts')
        role.permissions.add(self.permission)
        self.user.assigned_role = role
        self.user.save()
        self.assertEqual(self.client.get('/api/finance/status/').status_code, 200)
        grant.enabled = False
        grant.save()
        self.upstream.reset_mock()
        self.assertEqual(self.client.get('/api/finance/status/').status_code, 403)
        self.upstream.assert_not_called()


@override_settings(DEBUG=True, FINANCE_FRAPPE_URL='http://finance.localhost:8082',
                   FINANCE_FRAPPE_IDENTITY='integration@example.invalid',
                   FINANCE_FRAPPE_API_KEY='test-key', FINANCE_FRAPPE_API_SECRET='test-secret')
class AdapterTests(SimpleTestCase):
    def setUp(self):
        self.transport = patch('finance_gateway.adapter.requests.Session')
        self.factory = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.response = self.session.get.return_value.__enter__.return_value
        self.response.status_code = 200
        self.response.iter_content.return_value = [json.dumps({'message': 'integration@example.invalid'}).encode()]

    def test_fixed_authenticated_get_and_bounded_timeout_no_redirects(self):
        self.assertEqual(connection_status(), 'authenticated')
        self.session.get.assert_called_once_with('http://finance.localhost:8082/api/method/frappe.auth.get_logged_user',
            headers={'Authorization': 'token test-key:test-secret', 'Accept': 'application/json'},
            timeout=(3, 5), allow_redirects=False, stream=True)
        self.assertFalse(self.session.trust_env)
        self.assertFalse(self.session.post.called)

    def test_bad_configuration_never_opens_a_session(self):
        for url in ('http://remote.invalid', 'https://user:secret@host.invalid', 'https://host.invalid/path',
                    'https://host.invalid/?secret=private', 'https://host.invalid/#private', 'http://[bad', 'https://host.invalid:99999'):
            with self.settings(FINANCE_FRAPPE_URL=url):
                self.assertEqual(connection_status(), 'not_configured')
        for identity in ('Guest', 'Administrator', ''):
            with self.settings(FINANCE_FRAPPE_IDENTITY=identity):
                self.assertEqual(connection_status(), 'not_configured')
        with self.settings(FINANCE_FRAPPE_API_SECRET=''):
            self.assertEqual(connection_status(), 'not_configured')
        with self.settings(FINANCE_FRAPPE_API_KEY='key\r\nInjected: value'):
            self.assertEqual(connection_status(), 'not_configured')
        with self.settings(DEBUG=False):
            self.assertEqual(connection_status(), 'not_configured')
        self.factory.assert_not_called()

    def test_remote_https_can_only_use_fixed_identity_endpoint(self):
        with self.settings(DEBUG=False, FINANCE_FRAPPE_URL='https://finance.example.invalid'):
            self.assertEqual(connection_status(), 'authenticated')
        self.assertTrue(self.session.get.call_args.args[0].startswith('https://finance.example.invalid/api/method/'))

    def test_timeout_and_transport_errors_do_not_leak_secrets_or_log(self):
        with patch('logging.Logger._log') as log:
            for error, expected in [(requests.Timeout('test-secret'), 'timeout'), (requests.ConnectionError('test-key'), 'unavailable')]:
                self.session.get.side_effect = error
                self.assertEqual(connection_status(), expected)
            log.assert_not_called()

    def test_denial_redirect_and_server_errors_are_not_forwarded(self):
        for status, expected in [(401, 'permission_denied'), (403, 'permission_denied'), (302, 'unavailable'), (500, 'unavailable')]:
            self.response.status_code = status
            self.assertEqual(connection_status(), expected)

    def test_malformed_wrong_identity_and_oversized_payloads_are_rejected(self):
        for content in (b'[]', b'{"message":"Administrator"}', b'{"message":"Guest"}', b'{}', b'not-json', b'x' * 16385):
            self.response.iter_content.return_value = [content]
            self.assertIn(connection_status(), {'invalid_response', 'unavailable'})


@skipUnless(shutil.which('powershell'), 'Windows PowerShell is required for private diagnostic script tests')
class PowerShellConnectivityTests(SimpleTestCase):
    def run_script(self, scenario='ok', url='http://127.0.0.1:8082', identity='reader@example.invalid'):
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'Test-FinanceConnectivity.ps1'
        # Execute the actual script with its transport replaced in memory after
        # AST validation. No file rewrite, real credentials or socket connection.
        harness = r"""
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath '__PATH__' -Raw
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Diagnostic script syntax failure'}
$fn=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-FinanceReadOnlyGet'},$true)
if(-not $fn){throw 'Missing transport function'}
$global:probeCalls=[Collections.Generic.List[string]]::new()
$global:promptCount=0
$global:probeMode='__MODE__'
function global:Read-Host {
 param([string]$Prompt,[switch]$AsSecureString)
 if(-not $AsSecureString){throw 'Unmasked credential input'}
 $global:promptCount++
 $value=if($global:promptCount -eq 1){'SYNTHETIC-KEY'}else{'SYNTHETIC-SECRET'}
 if($global:probeMode -eq 'empty_secret' -and $global:promptCount -eq 2){$value=''}
 $secure=[Security.SecureString]::new()
 foreach($character in $value.ToCharArray()){$secure.AppendChar($character)}
 return $secure
}
$mock=@'
function Invoke-FinanceReadOnlyGet {
 param($Client,[Uri]$Uri,[string]$Authorization='')
 [void]$global:probeCalls.Add($Uri.AbsolutePath)
 if($Uri.AbsolutePath -eq '/api/method/ping'){
   if($Authorization){throw 'Credentials sent to public ping'}
   if($global:probeMode -eq 'ping_failure'){return [pscustomobject]@{Status=503;Content='SYNTHETIC-SECRET'}}
   return [pscustomobject]@{Status=200;Content='{"message":"pong"}'}
 }
 if($Uri.AbsolutePath -ne '/api/method/frappe.auth.get_logged_user'){throw 'Unexpected endpoint'}
 if($Authorization -cne 'token SYNTHETIC-KEY:SYNTHETIC-SECRET'){throw 'Invalid token construction'}
 switch($global:probeMode){
  'timeout' {throw [OperationCanceledException]::new('SYNTHETIC-SECRET')}
  'transport' {throw [InvalidOperationException]::new('SYNTHETIC-SECRET')}
  'denied' {return [pscustomobject]@{Status=403;Content='SYNTHETIC-SECRET'}}
  'redirect' {return [pscustomobject]@{Status=302;Content='SYNTHETIC-SECRET'}}
  'invalid' {return [pscustomobject]@{Status=200;Content='SYNTHETIC-SECRET'}}
  'wrong_identity' {return [pscustomobject]@{Status=200;Content='{"message":"Administrator"}'}}
  'array' {return [pscustomobject]@{Status=200;Content='[{"message":"reader@example.invalid"}]'}}
 }
 return [pscustomobject]@{Status=200;Content='{"message":"reader@example.invalid"}'}
}
'@
$source=$source.Insert($fn.Extent.EndOffset,"`n"+$mock+"`n")
$outcome=& ([scriptblock]::Create($source)) -BaseUrl '__URL__' -ExpectedIdentity '__IDENTITY__'
[pscustomobject]@{result=$outcome;calls=$global:probeCalls.ToArray();prompts=$global:promptCount}|ConvertTo-Json -Depth 5 -Compress
"""
        harness = harness.replace('__PATH__', str(path)).replace('__MODE__', scenario).replace('__URL__', url).replace('__IDENTITY__', identity)
        encoded = base64.b64encode(harness.encode('utf-16-le')).decode('ascii')
        run = subprocess.run([shutil.which('powershell'), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                             capture_output=True, text=True, timeout=25)
        self.assertEqual(run.returncode, 0, 'PowerShell test harness failed')
        self.assertNotIn('SYNTHETIC-KEY', run.stdout + run.stderr)
        self.assertNotIn('SYNTHETIC-SECRET', run.stdout + run.stderr)
        clean = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', run.stdout).strip()
        result = json.loads(clean)
        self.assertIs(result['result']['available'], False)
        self.assertIs(result['result']['posting_enabled'], False)
        self.assertEqual(result['result']['accounting_setup'], 'not_assessed')
        return result

    def test_script_authentication_and_sanitized_failure_paths(self):
        for mode, expected in [('ok', 'authenticated'), ('ping_failure', 'unavailable'),
                               ('denied', 'permission_denied'), ('redirect', 'unavailable'),
                               ('invalid', 'invalid_response'), ('wrong_identity', 'invalid_response'),
                               ('array', 'invalid_response'), ('timeout', 'timeout'), ('transport', 'unavailable')]:
            with self.subTest(mode=mode):
                result = self.run_script(mode)
                self.assertEqual(result['result']['connection'], expected)
                self.assertEqual(result['result']['identity_verified'], mode == 'ok')
                self.assertEqual(result['prompts'], 0 if mode == 'ping_failure' else 2)
                self.assertEqual(result['calls'][0], '/api/method/ping')
                self.assertLessEqual(len(result['calls']), 2)

    def test_script_rejects_unsafe_origins_and_privileged_identities_without_network(self):
        for url, identity in [('http://remote.invalid', 'reader@example.invalid'),
                              ('https://user:SYNTHETIC-SECRET@host.invalid', 'reader@example.invalid'),
                              ('https://host.invalid/?private=SYNTHETIC-SECRET', 'reader@example.invalid'),
                              ('https://host.invalid/path', 'reader@example.invalid'),
                              ('http://127.0.0.1:8082', 'Administrator'),
                              ('http://127.0.0.1:8082', 'Guest')]:
            with self.subTest(url=url, identity=identity):
                result = self.run_script(url=url, identity=identity)
                self.assertEqual(result['result']['connection'], 'not_configured')
                self.assertEqual(result['calls'], [])
                self.assertEqual(result['prompts'], 0)
