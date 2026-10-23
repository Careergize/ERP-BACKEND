from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from rest_framework import serializers


def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class BankSerializer(serializers.Serializer):
    bank = serializers.CharField(max_length=150)
    branch = serializers.CharField(max_length=150, allow_blank=True)
    accountName = serializers.CharField(max_length=200)
    accountNumber = serializers.CharField(max_length=80)
    currency = serializers.CharField(max_length=3)


class CompanySerializer(serializers.Serializer):
    tradingName = serializers.CharField(max_length=200)
    legalName = serializers.CharField(max_length=200)
    address = serializers.CharField(max_length=1000, allow_blank=True)
    telephone = serializers.CharField(max_length=60, allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    tin = serializers.CharField(max_length=80, allow_blank=True)
    signatory = serializers.CharField(max_length=150, allow_blank=True)
    banks = BankSerializer(many=True, max_length=5)


class CustomerSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    billingAddress = serializers.CharField(max_length=1000, allow_blank=True)
    deliveryAddress = serializers.CharField(max_length=1000, allow_blank=True)
    contactPerson = serializers.CharField(max_length=150, allow_blank=True)
    telephone = serializers.CharField(max_length=60, allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    tin = serializers.CharField(max_length=80, allow_blank=True)


class TermsSerializer(serializers.Serializer):
    priceValidity = serializers.CharField(max_length=1500, allow_blank=True)
    deliveryPeriod = serializers.CharField(max_length=1500, allow_blank=True)
    paymentTerms = serializers.CharField(max_length=1500, allow_blank=True)
    revisionClause = serializers.CharField(max_length=1500, allow_blank=True)
    additionalTerms = serializers.CharField(max_length=1500, allow_blank=True)
    remarks = serializers.CharField(max_length=1500, allow_blank=True)
    advancePercent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False)
    creditDays = serializers.IntegerField(min_value=0, max_value=3650, required=False)
    quantityTolerance = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False)


class LevySerializer(serializers.Serializer):
    method = serializers.CharField(max_length=100, required=False)
    selectedAmount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=0)
    vatOnLED = serializers.BooleanField(default=False)


class LineSerializer(serializers.Serializer):
    led = LevySerializer(required=False, allow_null=True)
    unit_id = serializers.IntegerField(min_value=1, required=False)
    description = serializers.CharField(max_length=2000)
    group = serializers.CharField(max_length=200, allow_blank=True)
    specifications = serializers.CharField(max_length=2000, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=6, min_value=Decimal('0.000001'))
    unit = serializers.CharField(max_length=30)
    unitPrice = serializers.DecimalField(max_digits=16, decimal_places=6, min_value=0)

    def validate(self, data):
        from erp.models import Unit
        units = Unit.objects.filter(is_active=True)
        unit = units.filter(pk=data['unit_id']).first() if 'unit_id' in data else units.filter(code__iexact=data['unit']).first()
        if not unit:
            raise serializers.ValidationError({'unit': 'Select an active unit from the backend unit master.'})
        if unit.code.casefold() != data['unit'].casefold():
            raise serializers.ValidationError({'unit_id': 'Selected unit and unit code do not match.'})
        data['unit_id'], data['unit'] = unit.pk, unit.code
        return data


class DocumentSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(min_value=1, required=False)
    source_id = serializers.CharField(max_length=100)
    number = serializers.RegexField(r'^[A-Za-z0-9][A-Za-z0-9 /_.-]{0,59}$')
    revision = serializers.IntegerField(min_value=1, max_value=10000)
    documentType = serializers.ChoiceField(choices=['Sales Quotation', 'Proforma Invoice'])
    date = serializers.DateField()
    validityDays = serializers.IntegerField(min_value=0, max_value=3650)
    deliveryDays = serializers.IntegerField(min_value=0, max_value=3650, required=False)
    currency = serializers.RegexField(r'^[A-Z]{3}$')
    taxRate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
    taxMode = serializers.ChoiceField(choices=['inclusive', 'exclusive'])
    discount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=0)
    company = CompanySerializer()
    customer = CustomerSerializer()
    terms = TermsSerializer()
    lines = LineSerializer(many=True, allow_empty=False, max_length=100)

    def validate_customer_id(self, value):
        from erp.models import Customer
        if not Customer.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('Select an active saved customer.')
        return value

    def validate(self, data):
        subtotal = sum((money(l['quantity'] * l['unitPrice']) for l in data['lines']), Decimal('0'))
        if data['discount'] > subtotal or subtotal > Decimal('99999999999999.99'):
            raise serializers.ValidationError('Invalid discount or total exceeds supported range.')
        basis = money(subtotal - data['discount'])
        rate = data['taxRate'] / 100
        for line in data['lines']:
            line['lineTotal'] = str(money(line['quantity'] * line['unitPrice']))
            line['unitPriceInclVat'] = str(line['unitPrice'] if data['taxMode'] == 'inclusive' else (line['unitPrice'] * (1 + rate)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP))
        net = money(basis / (1 + rate)) if data['taxMode'] == 'inclusive' else basis
        tax = money(basis - net) if data['taxMode'] == 'inclusive' else money(net * rate)
        data['totals'] = {'subtotal': str(subtotal), 'discount': str(data['discount']), 'taxableAmount': str(net), 'tax': str(tax), 'total': str(basis if data['taxMode'] == 'inclusive' else money(net + tax))}
        levy = Decimal(0)
        levy_tax = Decimal(0)
        for line in data['lines']:
            snapshot = line.get('ledSnapshot') or line.get('led') or {}
            if not isinstance(snapshot, dict):
                raise serializers.ValidationError('LED must contain a valid snapshot.')
            if snapshot.get('applies') and snapshot.get('selectedAmount') is None:
                raise serializers.ValidationError('LED requires an applied snapshot.')
            if snapshot.get('selectedAmount') is not None:
                amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=0).run_validation(snapshot['selectedAmount'])
                levy += amount
                if snapshot.get('vatOnLED'):
                    levy_tax += amount * rate
        if levy:
            taxable_levy = sum((money((line.get('ledSnapshot') or line.get('led') or {}).get('selectedAmount', 0)) for line in data['lines'] if (line.get('ledSnapshot') or line.get('led') or {}).get('vatOnLED')), Decimal(0))
            data['totals'].update(led=str(money(levy)), ledTax=str(money(levy_tax)), taxableAmount=str(money(net + taxable_levy)), tax=str(money(tax + levy_tax)), total=str(money(Decimal(data['totals']['total']) + levy + money(levy_tax))))
        data['validUntil'] = data['date'] + timedelta(days=data['validityDays'])
        return data


class SendSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    channels = serializers.ListField(child=serializers.ChoiceField(choices=['email', 'whatsapp']), allow_empty=False, max_length=2)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)

    def validate(self, data):
        if len(set(data['channels'])) != len(data['channels']):
            raise serializers.ValidationError('Choose each channel only once.')
        if 'email' in data['channels'] and not data.get('email'):
            raise serializers.ValidationError({'email': 'Email is required when Email is selected.'})
        if 'whatsapp' in data['channels']:
            phone = re.sub(r'[\s()-]', '', data.get('phone', ''))
            if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
                raise serializers.ValidationError({'phone': 'Include the country code, for example +916282744675.'})
            data['phone'] = phone
        return data
