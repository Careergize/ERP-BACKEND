from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from .catalog_models import SpareItem, Consumable, SpareStockMovement
from .spare_service import balance

class SpareSerializer(serializers.ModelSerializer):
    stock = serializers.SerializerMethodField()
    class Meta:
        model = SpareItem
        fields = ['id', 'code', 'name', 'description', 'unit', 'is_active', 'category', 'minimum_stock', 'created_at', 'updated_at', 'stock']
        read_only_fields = ['id', 'created_at', 'updated_at', 'stock']
        extra_kwargs = {'minimum_stock': {'min_value': 0}}
    def get_stock(self, obj):
        totals = balance(obj)
        return {**{k: None if v is None else str(v) for k, v in totals.items()},
                'below_minimum': totals['current_stock'] is not None and totals['current_stock'] < obj.minimum_stock}

class ConsumableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Consumable
        fields = ['id', 'code', 'name', 'description', 'unit', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

class MovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpareStockMovement
        fields = ['id', 'item', 'request_id', 'movement_type', 'is_opening', 'quantity', 'unit', 'reference', 'recorded_by', 'created_at']
        read_only_fields = fields

class CatalogPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated or not request.user.is_active:
            return False
        model = view.queryset.model._meta.model_name
        verb = {'create': 'add', 'update': 'change', 'partial_update': 'change'}.get(view.action, 'view')
        return request.user.has_perm(f'inventory.{verb}_{model}')

class RegisterViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, CatalogPermission]
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('search'):
            qs = qs.filter(Q(name__icontains=p['search']) | Q(code__icontains=p['search']))
        for key in ('unit', 'is_active'):
            if p.get(key):
                value = p[key]
                if key == 'is_active':
                    if value not in ('true', 'false'):
                        raise serializers.ValidationError({'is_active': 'Use true or false.'})
                    value = value == 'true'
                qs = qs.filter(**{key: value})
        if qs.model == SpareItem and p.get('category'):
            qs = qs.filter(category=p['category'])
        return qs
    @action(detail=False, methods=['get'])
    def capabilities(self, request):
        model = self.queryset.model._meta.model_name
        return Response({'can_create': request.user.has_perm(f'inventory.add_{model}'),
                         'can_edit': request.user.has_perm(f'inventory.change_{model}'),
                         'can_post': False,
                         'posting_notice': 'Posting is unavailable until Procurement/Stores authority and approval mapping is confirmed.'})

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class ConsumableViewSet(RegisterViewSet):
    queryset = Consumable.objects.all()
    serializer_class = ConsumableSerializer

class SpareViewSet(RegisterViewSet):
    queryset = SpareItem.objects.all()
    serializer_class = SpareSerializer

    @transaction.atomic
    def perform_update(self, serializer):
        item = SpareItem.objects.select_for_update().get(pk=serializer.instance.pk)
        if item.movements.exists() and serializer.validated_data.get('unit', item.unit) != item.unit:
            raise serializers.ValidationError({'unit': 'Unit is locked after an opening or movement.'})
        serializer.instance = item
        serializer.save()

    @action(detail=True, methods=['get'])
    def stock(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data['stock'])

    @action(detail=True, methods=['get', 'post'])
    def movements(self, request, pk=None):
        item = self.get_object()
        if request.method == 'GET':
            if not request.user.has_perm('inventory.view_sparestockmovement'):
                raise PermissionDenied('Movement history permission required.')
            page = self.paginate_queryset(item.movements.all())
            return self.get_paginated_response(MovementSerializer(page, many=True).data)
        # No published authority mapping exists. Neither roles nor superuser status
        # can activate this operational command. The service is synthetic-tested only.
        raise PermissionDenied('Stock posting unavailable pending approved authority mapping.')
