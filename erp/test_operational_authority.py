from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from .models import WorkflowJob

class OperationalAuthorityTests(TestCase):
    def test_unconfigured_operational_commands_are_closed_even_to_admin(self):
        actor = get_user_model().objects.create_superuser(username='synthetic-admin', password='synthetic')
        job = WorkflowJob.objects.create(data={})
        client = APIClient(); client.force_authenticate(actor)
        for path in ['orders/1/approve/', 'orders/1/allocate/', 'jobs/', 'jobs/1/approve/', 'jobs/1/issue/', 'jobs/1/approve-costing/']:
            with self.subTest(path=path):
                result = client.post('/api/production-workflow/' + path, {}, format='json')
                self.assertEqual(result.status_code, 403, result.data)
        self.assertEqual(WorkflowJob.objects.count(), 1)
        job.refresh_from_db(); self.assertEqual(job.data, {})
