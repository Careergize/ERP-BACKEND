from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from accounts.applications import private_response
from .adapter import connection_status
from .permissions import CanInspectFinanceStatus


class FinanceStatusView(APIView):
    permission_classes = [IsAuthenticated, CanInspectFinanceStatus]
    http_method_names = ['get', 'head', 'options']

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Vary'] = 'Authorization, Cookie, Accept'
        return response

    def get(self, request):
        connection = connection_status()
        return private_response({
            'available': False,
            'reason': 'frappe_sso_not_configured',
            'connection': connection,
            'read_only': True,
            'accounting_setup': 'not_assessed',
            'posting_enabled': False,
        }, status=200 if connection == 'authenticated' else 503)
