"""Only access discovery is exposed. No financial read/write transport exists."""
import uuid
from time import monotonic
from django.db import transaction
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from .authorization import effective_companies, GatewayDenied
from .models import FinanceAuditEvent


class FinanceAccessView(APIView):
    # Authenticate using existing JWT settings; audit even permission failures.
    permission_classes = []
    http_method_names = ['get', 'head', 'options']

    def initial(self, request, *args, **kwargs):
        try:
            super().initial(request, *args, **kwargs)
        except APIException as error:
            status = error.status_code
            code = 'authentication_required' if status in (401, 403) else 'integration_unavailable'
            self.audit(request, code, status)
            raise

    def audit(self, request, code, status):
        try:
            FinanceAuditEvent.objects.create(
                correlation_id=self.correlation_id,
                actor=request.user if request.user.is_authenticated else None,
                action='finance.access.read', reason_code=code, http_status=status,
                duration_ms=max(0, int((monotonic() - self.started_at) * 1000)),
                outcome=('allowed' if status == 200 else 'disabled' if code == 'integration_disabled'
                         else 'denied' if status in (401, 403) else 'unavailable'))
        except Exception:
            error = APIException({'code': 'integration_unavailable'})
            error.status_code = 503
            error.default_code = 'integration_unavailable'
            raise error from None

    def dispatch(self, request, *args, **kwargs):
        self.correlation_id = uuid.uuid4()
        self.started_at = monotonic()
        return super().dispatch(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Vary'] = 'Authorization, Cookie, Accept'
        response['X-Correlation-ID'] = str(self.correlation_id)
        return response

    def get(self, request):
        try:
            # Roll back a failed DB read before attempting the mandatory audit.
            with transaction.atomic():
                companies = effective_companies(request.user)
            status, code = 200, 'ok'
            data = {'companies': companies, 'available': False, 'read_only': True,
                    'posting_enabled': False, 'activation_enabled': False,
                    'synchronization_enabled': False}
        except GatewayDenied as error:
            code, status = error.code, error.status
            data = {'code': code}
        except Exception:
            code, status = 'integration_unavailable', 503
            data = {'code': code}
        self.audit(request, code, status)
        return Response(data, status=status)
