from django.db import connection
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.filters import SearchFilter
from .models import Customer, Supplier, Product, SalesOrder, JobCard, Unit
from .serializers import CustomerSerializer, SupplierSerializer, ProductSerializer, SalesOrderSerializer, JobCardSerializer, UnitSerializer


class ERPModelPermissions(permissions.DjangoModelPermissions):
    # A display role never grants access: administrators assign Django permissions.
    perms_map = {**permissions.DjangoModelPermissions.perms_map, 'GET': ['%(app_label)s.view_%(model_name)s']}


class MasterViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ERPModelPermissions]
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    filter_backends = []


class CustomerViewSet(MasterViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [SearchFilter]
    search_fields = ['name', 'code', 'email', 'phone', 'contact_person']

    @action(detail=False, methods=['get'], url_path='ledger-options')
    def ledger_options(self, request):
        # Customer view permission allows only ledger identity here, never balances.
        from accounting.models import Party
        from accounting.api import required_accounting_migrations, AccountingSchemaRequired
        from django.db.migrations.recorder import MigrationRecorder
        applied = set(MigrationRecorder.Migration.objects.filter(app='accounting').values_list('name', flat=True))
        if not required_accounting_migrations() <= applied:
            raise AccountingSchemaRequired('Apply accounting migrations on the backend server to load Finance customer ledgers.')
        can_link = request.user.has_perm('erp.add_customer') and (request.user.is_superuser or request.user.effective_role in {'admin','accounts'})
        return Response({'ledgers':list(Party.objects.filter(kind='customer',source__isnull=True).order_by('name','pk').values('id','name')),
                         'canLink':bool(can_link)})

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('active') == 'true':
            queryset = queryset.filter(is_active=True)
        return queryset


class UnitViewSet(MasterViewSet):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('active') == 'true':
            queryset = queryset.filter(is_active=True)
        return queryset


class SupplierViewSet(MasterViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class ProductViewSet(MasterViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


class DraftRecordViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    # Edits and transitions require future revision, audit and approval services.
    permission_classes = [permissions.IsAuthenticated, ERPModelPermissions]


class SalesOrderViewSet(DraftRecordViewSet):
    queryset = SalesOrder.objects.select_related('customer').prefetch_related('lines').all()
    serializer_class = SalesOrderSerializer


class JobCardViewSet(DraftRecordViewSet):
    queryset = JobCard.objects.select_related('sales_order').all()
    serializer_class = JobCardSerializer


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return Response({'status': 'unavailable'}, status=503)
    return Response({'status': 'ok', 'service': 'navapack-erp'})
