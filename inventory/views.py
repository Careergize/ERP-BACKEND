from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import RawMaterial, RawMovement, ProcurementPlan, ProcurementDelivery, ProcurementPlanRevision


def require(user, permission):
    if not user.is_active or not user.has_perm('inventory.' + permission):
        raise PermissionDenied('Your account does not have permission for this inventory action.')


def material_data(m):
    return {'id': m.pk, 'name': m.name, 'family': m.family, 'category': m.category, 'unit': m.unit,
            'openingStock': None if m.opening_stock is None else float(m.opening_stock), 'openingDate': m.opening_date.isoformat(),
            **({'minLevel': float(m.min_level)} if m.min_level is not None else {})}


def movement_data(m):
    return {'id': m.pk, 'materialId': m.material_id, 'materialName': m.material.name, 'category': m.material.category,
            'unit': m.material.unit, 'date': m.date.isoformat(), 'movementType': m.movement_type, 'quantity': float(m.quantity),
            'reference': m.reference, 'remarks': m.remarks}


def plan_data(p):
    return {'id': 'planner-' + p.material_id, 'materialId': p.material_id, 'name': p.material.name, 'unit': p.material.unit,
            'stock': None if p.stock_in_store is None else float(p.stock_in_store), 'asOfDate': p.as_of_date.isoformat(),
            'currentStock': None if p.stock_in_store is None else float(p.remaining_stock()),
            'monthly': float(p.monthly), 'days': p.days, 'lead': p.lead, 'comment': p.comment,
            'deliveries': [{'id': str(d.pk), 'supplier': d.supplier, 'date': d.expected_date.isoformat(), 'quantity': float(d.quantity)}
                           for d in p.expected_deliveries.all()], 'version': p.version}


class InventorySnapshot(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        require(request.user, 'view_rawmaterial')
        require(request.user, 'view_rawmovement')
        return Response({'materials': [material_data(m) for m in RawMaterial.objects.all()],
                         'movements': [movement_data(m) for m in RawMovement.objects.select_related('material')],
                         'plans': [plan_data(p) for p in ProcurementPlan.objects.select_related('material').prefetch_related('expected_deliveries')] if request.user.has_perm('inventory.view_procurementplan') else [],
                         'canPost': request.user.has_perm('inventory.add_rawmovement'),
                         'canPlan': request.user.has_perm('inventory.change_procurementplan')})


class ProcurementSnapshot(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        require(request.user, 'view_procurementplan')
        materials = RawMaterial.objects.annotate(
            total_in=Sum('movements__quantity', filter=Q(movements__movement_type='IN')),
            total_out=Sum('movements__quantity', filter=Q(movements__movement_type='OUT')))
        choices = [{**material_data(m), 'currentStock': None if m.opening_stock is None else float(
            m.opening_stock + (m.total_in or 0) - (m.total_out or 0))} for m in materials]
        return Response({'materials': choices,
                         'plans': [plan_data(p) for p in ProcurementPlan.objects.select_related('material').prefetch_related('expected_deliveries')],
                         'canPlan': request.user.has_perm('inventory.change_procurementplan')})


class MovementInput(serializers.Serializer):
    id = serializers.UUIDField()
    materialId = serializers.CharField(max_length=100)
    unit = serializers.CharField(max_length=30)
    date = serializers.DateField()
    movementType = serializers.ChoiceField(choices=['IN', 'OUT'])
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0.000001'))
    reference = serializers.CharField(max_length=250, allow_blank=True, default='')
    remarks = serializers.CharField(max_length=5000, allow_blank=True, default='')


class RawMovementCreate(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        require(request.user, 'add_rawmovement')
        s = MovementInput(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        with transaction.atomic():
            # Serialize all issues/receipts for this exact grade, including first writes.
            material = get_object_or_404(RawMaterial.objects.select_for_update(), pk=v['materialId'])
            if v['unit'] != material.unit or v['date'] < material.opening_date or v['date'] > timezone.localdate():
                raise serializers.ValidationError('Use the exact material unit and a date between its opening date and today.')
            fields = {'material_id': material.pk, 'date': v['date'], 'movement_type': v['movementType'],
                      'quantity': v['quantity'], 'reference': v['reference'], 'remarks': v['remarks']}
            previous = RawMovement.objects.filter(pk=str(v['id'])).first()
            if previous:
                if any(getattr(previous, k) != value for k, value in fields.items()):
                    return Response({'detail': 'This request ID was already used for another entry.'}, status=409)
                return Response(movement_data(previous))
            if v['movementType'] == 'OUT':
                if material.opening_stock is None:
                    raise serializers.ValidationError('Opening stock is unknown; confirm the opening balance before issuing material.')
                totals = {row['movement_type']: row['total'] for row in material.movements.values('movement_type').annotate(total=Sum('quantity'))}
                available = material.opening_stock + totals.get('IN', 0) - totals.get('OUT', 0)
                if v['quantity'] > available:
                    raise serializers.ValidationError('Consumption exceeds the remaining stock. Reload the store before retrying.')
            movement = RawMovement.objects.create(id=str(v['id']), created_by=request.user, **fields)
            return Response(movement_data(movement), status=201)


class DeliveryInput(serializers.Serializer):
    id = serializers.UUIDField()
    supplier = serializers.CharField(max_length=250)
    date = serializers.DateField()
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0.000001'))


class PlanInput(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=0)
    stock = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=0, allow_null=True, required=False)
    asOfDate = serializers.DateField(required=False)
    monthly = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=0)
    days = serializers.IntegerField(min_value=1, max_value=31)
    lead = serializers.IntegerField(min_value=0, max_value=3650)
    comment = serializers.CharField(max_length=5000, allow_blank=True)
    deliveries = DeliveryInput(many=True, max_length=100)

    def validate_asOfDate(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('Stock date cannot be in the future.')
        return value

    def validate_deliveries(self, rows):
        if len({r['id'] for r in rows}) != len(rows):
            raise serializers.ValidationError('Delivery IDs must be unique.')
        return rows


class ProcurementPlanSave(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, material_id):
        require(request.user, 'change_procurementplan')
        s = PlanInput(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        deliveries = sorted([{'id': str(d['id']), 'supplier': d['supplier'], 'date': d['date'].isoformat(), 'quantity': float(d['quantity'])} for d in v['deliveries']], key=lambda d: (d['date'], d['id']))
        with transaction.atomic():
            material = get_object_or_404(RawMaterial.objects.select_for_update(), pk=material_id)
            plan = ProcurementPlan.objects.filter(material=material).first()
            fields = {k: v[k] for k in ['monthly', 'days', 'lead', 'comment']}
            fields['stock_in_store'] = v.get('stock', plan.stock_in_store if plan else None)
            fields['as_of_date'] = v.get('asOfDate', plan.as_of_date if plan else timezone.localdate())
            # Identical retry succeeds even if its expected version is now stale.
            if plan and all(getattr(plan, k) == value for k, value in fields.items()) and plan_data(plan)['deliveries'] == deliveries:
                return Response(plan_data(plan))
            if v['expected_version'] != (plan.version if plan else 0):
                return Response({'detail': 'Another session changed this plan. Reload before saving.'}, status=409)
            delivery_ids = [d['id'] for d in v['deliveries']]
            if ProcurementDelivery.objects.filter(pk__in=delivery_ids).exclude(plan_id=material.pk).exists():
                return Response({'detail': 'A delivery belongs to another material plan. Add a new delivery instead.'}, status=409)
            if plan:
                for k, value in fields.items():
                    setattr(plan, k, value)
                plan.version += 1
                plan.updated_by = request.user
                plan.save()
            else:
                plan = ProcurementPlan.objects.create(material=material, updated_by=request.user, **fields)
            plan.expected_deliveries.exclude(pk__in=delivery_ids).delete()
            for delivery in v['deliveries']:
                row, created = ProcurementDelivery.objects.get_or_create(pk=delivery['id'], defaults={
                    'plan': plan, 'supplier': delivery['supplier'], 'expected_date': delivery['date'], 'quantity': delivery['quantity'],
                    'created_by': request.user, 'updated_by': request.user})
                if not created:
                    row.supplier = delivery['supplier']
                    row.expected_date = delivery['date']
                    row.quantity = delivery['quantity']
                    row.updated_by = request.user
                    row.save(update_fields=['supplier', 'expected_date', 'quantity', 'updated_by', 'updated_at'])
            ProcurementPlanRevision.objects.create(plan=plan, version=plan.version, snapshot=plan_data(plan), changed_by=request.user)
            return Response(plan_data(plan))
