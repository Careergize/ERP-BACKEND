from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase
from .models import SharedEntry, EntryRevision
from accounts.models import Role


class SharedEntryTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user('entry-admin', role='admin', assigned_role=Role.objects.get(code='admin'))
        self.accounts = User.objects.create_user('entry-accounts', role='accounts', assigned_role=Role.objects.get(code='accounts'))
        self.store = User.objects.create_user('entry-store', role='store_keeper', assigned_role=Role.objects.get(code='store_keeper'))
        self.manager = User.objects.create_user('entry-manager', role='project_manager', assigned_role=Role.objects.get(code='project_manager'))
        self.qc = User.objects.create_user('entry-qc', role='qc_inspector', assigned_role=Role.objects.get(code='qc_inspector'))
        self.client.force_authenticate(self.admin)

    def save(self, key='commercial-prospects', content=None, version=0, command=None):
        return self.client.put(f'/api/shared-entries/{key}/', {
            'commandId': command or str(uuid4()), 'expectedVersion': version,
            'content': content if content is not None else [{'id': 'p1', 'name': 'Customer prospect'}],
        }, format='json')

    def test_save_reload_other_account_and_management_history(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(self.manager)
        result = self.client.get('/api/shared-entries/').data
        self.assertEqual(result['entries'][0]['content'][0]['name'], 'Customer prospect')
        event = self.client.get('/api/management/activity/').data['results'][0]
        self.assertEqual(event['actorId'], str(self.admin.pk))
        self.assertEqual(event['version'], 1)
        self.assertEqual(event['content'][0]['id'], 'p1')

    def test_retry_is_idempotent_and_changed_retry_conflicts(self):
        command = str(uuid4())
        self.assertEqual(self.save(command=command).status_code, 200)
        self.assertEqual(self.save(command=command).status_code, 200)
        self.assertEqual(EntryRevision.objects.count(), 1)
        self.assertEqual(self.save(command=command, content=[]).status_code, 409)

    def test_stale_write_cannot_replace_another_users_change(self):
        self.save()
        response = self.save(content=[{'id': 'p2'}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SharedEntry.objects.get().content[0]['id'], 'p1')
        self.assertEqual(EntryRevision.objects.count(), 1)

    def test_revisions_are_retained_and_immutable(self):
        self.save()
        self.save(content=[{'id': 'p1', 'name': 'Revised'}], version=1)
        self.assertEqual(EntryRevision.objects.count(), 2)
        original = EntryRevision.objects.order_by('id').first()
        self.assertEqual(original.content[0]['name'], 'Customer prospect')
        with self.assertRaises(ValidationError):
            original.delete()
        with self.assertRaises(ValidationError):
            EntryRevision.objects.filter(pk=original.pk).update(content=[])

    def test_permissions_and_anonymous_access(self):
        self.client.force_authenticate(self.store)
        self.assertEqual(self.save().status_code, 403)
        self.assertEqual(self.client.get('/api/management/activity/').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/shared-entries/').status_code, 401)
        self.assertEqual(self.save().status_code, 401)

    def test_read_scope_hides_commercial_records_from_qc(self):
        self.save()
        self.client.force_authenticate(self.qc)
        response = self.client.get('/api/shared-entries/').data
        self.assertNotIn('commercial-prospects', response['readable'])
        self.assertEqual(response['entries'], [])

    def test_inactive_account_and_unknown_scope_fail_closed(self):
        self.assertEqual(self.save(key='unknown-ledger').status_code, 403)
        self.admin.is_active = False; self.admin.save()
        self.assertEqual(self.save().status_code, 403)

    def test_currency_rate_append_only_and_validation(self):
        self.client.force_authenticate(self.accounts)
        rate = {'id': 'fx1', 'date': '2026-10-25', 'currency': 'USD', 'rate': 3700}
        self.assertEqual(self.save(key='exchange-rates', content=[rate]).status_code, 200)
        self.assertEqual(self.save(key='exchange-rates', content=[], version=1).status_code, 400)
        self.assertEqual(self.save(key='exchange-rates', content=[{**rate, 'rate': -1}], version=1).status_code, 400)
        self.assertEqual(self.save(key='exchange-rates', content=[rate, {**rate, 'id': 'fx2', 'rate': 3710}], version=1).status_code, 200)

    def test_no_release_or_approval_bypass_through_supporting_storage(self):
        state = {'inspections': [], 'releases': [{'id': 'fake-release'}], 'nonConformances': []}
        self.assertNotEqual(self.save(key='quality-state', content=state).status_code, 200)
        self.assertNotEqual(self.save(key='costing-estimates', content=[{'id': 'fake', 'status': 'Approved'}]).status_code, 200)
        self.assertEqual(SharedEntry.objects.count(), 0)

    def test_management_history_filter_and_cursor(self):
        for version in range(3):
            self.save(content=[{'id': 'p1', 'name': str(version)}], version=version)
        result = self.client.get('/api/management/activity/', {'key': 'commercial-prospects', 'before': 3})
        self.assertEqual([row['version'] for row in result.data['results']], [2, 1])
        self.assertEqual(self.client.get('/api/management/activity/', {'before': 'bad'}).status_code, 400)

    def test_malformed_data_cannot_create_a_record(self):
        self.assertEqual(self.save(content={'bad': 'root'}).status_code, 400)
        response = self.client.put('/api/shared-entries/commercial-prospects/', {'expectedVersion': True, 'commandId': str(uuid4()), 'content': []}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SharedEntry.objects.exists())

    def test_quotation_workspaces_are_scoped_to_the_authenticated_author(self):
        draft = [{'id': 'q1', 'lines': [], 'events': [], 'responses': []}]
        self.assertEqual(self.save(key='quotation-workspace', content=draft).status_code, 200)
        self.client.force_authenticate(self.accounts)
        self.assertNotIn('quotation-workspace', [row['key'] for row in self.client.get('/api/shared-entries/').data['entries']])
        self.assertEqual(self.save(key='quotation-workspace', content=draft).status_code, 200)
        self.assertEqual(SharedEntry.objects.filter(key__startswith='quotation-workspace:').count(), 2)
        self.client.force_authenticate(self.manager)
        self.assertEqual(len(self.client.get('/api/management/activity/').data['results']), 2)

    def test_malformed_nested_quality_data_is_rejected(self):
        content = {'inspections': [None], 'releases': [], 'nonConformances': []}
        self.assertEqual(self.save(key='quality-state', content=content).status_code, 400)
        content['inspections'] = [{'id': 'i1', 'status': 'Draft'}, {'id': 'i1', 'status': 'Draft'}]
        self.assertEqual(self.save(key='quality-state', content=content).status_code, 400)

    def test_role_label_without_server_capability_cannot_write(self):
        unconfigured = get_user_model().objects.create_user('unconfigured-entry-user', role='admin')
        self.client.force_authenticate(unconfigured)
        self.assertEqual(self.save().status_code, 403)
