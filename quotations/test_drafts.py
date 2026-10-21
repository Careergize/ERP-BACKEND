from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from uuid import uuid4
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIClient
from .models import QuotationDraft


class DraftPersistenceTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('draft-owner', password='Draft-password-928!')
        self.client.force_authenticate(self.user)
        self.payload = json.loads(Path(__file__).with_name('sample.json').read_text())
        self.payload.update(id=str(uuid4()), rootId=str(uuid4()), date=date.today().isoformat(), salesRepresentative='Sales rep')
        self.payload['customer'].update(customerId='db:1', customerType='B2B')
        for line in self.payload['lines']:
            line.update(id=str(uuid4()), type='stock', productId='frontend-product-1')

    def save(self, payload=None, version=0):
        return self.client.post('/api/quotation-drafts/', {'editor_data': payload or self.payload, 'expected_version': version}, format='json')

    def test_saved_draft_reloads_in_new_session_with_source_details(self):
        response = self.save()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(QuotationDraft.objects.count(), 1)
        fresh = APIClient(); fresh.force_authenticate(self.user)
        record = fresh.get('/api/quotation-drafts/').data['results'][0]
        self.assertEqual(record['editor_data']['id'], self.payload['id'])
        self.assertEqual(record['editor_data']['salesRepresentative'], 'Sales rep')
        self.assertEqual(record['editor_data']['lines'][0]['productId'], 'frontend-product-1')
        self.assertEqual(record['editor_data']['lines'][0]['lineTotal'], '1200000.00')

    def test_save_edits_same_row_with_version_and_rejects_stale_edit(self):
        self.assertEqual(self.save().status_code, 201)
        self.payload['salesRepresentative'] = 'Updated rep'
        response = self.save(version=1)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['version'], 2)
        self.assertEqual(self.save(version=1).data['version'], 2)  # lost-response replay
        self.payload['salesRepresentative'] = 'Stale rep'
        self.assertEqual(self.save(version=1).status_code, 409)
        self.assertEqual(QuotationDraft.objects.count(), 1)
        self.assertEqual(QuotationDraft.objects.get().editor_data['salesRepresentative'], 'Updated rep')

    def test_incomplete_draft_can_be_saved_and_forged_approval_is_discarded(self):
        self.payload['lines'] = []
        self.payload['customer']['name'] = ''
        self.payload.update(status='Approved', approval={'user': 'Forged manager'})
        response = self.save()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('status', response.data['editor_data'])
        self.assertNotIn('approval', response.data['editor_data'])
        self.assertIsNone(response.data['review'])

    def test_drafts_are_owner_scoped(self):
        self.save()
        other = get_user_model().objects.create_superuser('other-draft-owner', password='Draft-password-928!')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get('/api/quotation-drafts/').data['count'], 0)
        self.assertEqual(self.client.get(f"/api/quotation-drafts/{self.payload['id']}/").status_code, 404)
        self.assertEqual(self.save().status_code, 403)

    def test_submitted_draft_is_frozen_and_new_revision_is_saved_separately(self):
        self.save()
        public = deepcopy(self.payload)
        public['source_id'] = self.payload['id']
        quote = self.client.post('/api/quotations/', public, format='json')
        self.assertEqual(quote.status_code, 201, quote.data)
        self.client.post(f"/api/quotations/{quote.data['id']}/submit/")
        record = self.client.get(f"/api/quotation-drafts/{self.payload['id']}/").data
        self.assertIsNotNone(record['review']['submitted_at'])
        self.payload['salesRepresentative'] = 'Changed after submit'
        self.assertEqual(self.save(version=1).status_code, 409)
        self.payload.update(id=str(uuid4()), revision=2)
        self.assertEqual(self.save().status_code, 201)
        self.assertEqual(QuotationDraft.objects.count(), 2)
