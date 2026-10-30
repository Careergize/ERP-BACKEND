"""Return an actionable service error while accounting schema is not deployed."""
from functools import lru_cache

from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from rest_framework.exceptions import APIException
from rest_framework.views import APIView


class AccountingSchemaRequired(APIException):
    status_code = 503
    default_code = 'accounting_migrations_required'


@lru_cache(maxsize=1)
def required_accounting_migrations():
    return frozenset(name for app, name in MigrationLoader(None).graph.leaf_nodes('accounting'))


class AccountingAPIView(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Check access before disclosing service setup status. Import here avoids
        # a views/base-class cycle and preserves the existing role policy.
        from .views import access
        access(request.user)
        applied = set(MigrationRecorder.Migration.objects.filter(app='accounting').values_list('name', flat=True))
        if not required_accounting_migrations() <= applied:
            raise AccountingSchemaRequired(
                'Accounting database update required. On the backend server, run '
                '`python manage.py migrate accounting`, then restart the backend service and retry. '
                'Use the same environment and database as the running backend.'
            )
