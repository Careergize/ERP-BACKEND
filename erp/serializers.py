from django.db import transaction
from rest_framework import serializers
from .models import Customer, Supplier, Product, SalesOrder, SalesOrderLine, JobCard, Unit


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ('id', 'code', 'name', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')

    def validate_code(self, value):
        import re
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,29}', value):
            raise serializers.ValidationError('Use a short unit code such as kg, ctn or pcs.')
        if self.instance and self.instance.code != value:
            raise serializers.ValidationError('Unit codes cannot be changed. Add a new unit instead.')
        existing = Unit.objects.filter(code__iexact=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError('This unit code already exists.')
        return value


class CustomerSerializer(serializers.ModelSerializer):
    creation_key = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = Customer
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

    def create(self, validated_data):
        key = validated_data.pop('creation_key', None)
        if key is None:
            return super().create(validated_data)
        customer, created = Customer.objects.get_or_create(creation_key=key, defaults=validated_data)
        if not created and any(getattr(customer, field) != value for field, value in validated_data.items()):
            from rest_framework.exceptions import APIException
            error = APIException('This customer save request was already used with different details.')
            error.status_code = 409
            raise error
        return customer

    def update(self, instance, validated_data):
        validated_data.pop('creation_key', None)
        return super().update(instance, validated_data)


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class OrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesOrderLine
        fields = ('id', 'product', 'quantity', 'unit_price')
        read_only_fields = ('id',)

    def validate_product(self, product):
        if not product.is_active or product.kind != 'finished_goods':
            raise serializers.ValidationError('Select an active finished-goods product.')
        return product


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, allow_empty=False)
    total = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = ('id', 'number', 'customer', 'order_date', 'delivery_date', 'notes', 'status', 'lines', 'total', 'created_at', 'updated_at')
        read_only_fields = ('number', 'status', 'created_at', 'updated_at')

    def get_total(self, order):
        from decimal import Decimal
        return str(sum((line.quantity * line.unit_price for line in order.lines.all()), Decimal('0')).quantize(Decimal('0.01')))

    def validate_customer(self, customer):
        if not customer.is_active:
            raise serializers.ValidationError('Select an active customer.')
        return customer

    def validate(self, attrs):
        start = attrs.get('order_date', getattr(self.instance, 'order_date', None))
        end = attrs.get('delivery_date', getattr(self.instance, 'delivery_date', None))
        if start and end and end < start:
            raise serializers.ValidationError({'delivery_date': 'Must be on or after order date.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop('lines')
        order = SalesOrder.objects.create(**validated_data)
        for line in lines:
            SalesOrderLine.objects.create(order=order, **line)
        return order


class JobCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobCard
        fields = '__all__'
        read_only_fields = ('number', 'status', 'created_at', 'updated_at')
