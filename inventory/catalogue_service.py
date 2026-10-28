"""Catalogue-only transactions. Never posts raw, finished-goods or accounting entries."""
import hashlib
import json
import time
from functools import wraps
from decimal import Decimal
from uuid import uuid4
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction, OperationalError
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .catalog_models import CatalogueSequence, ConsumableCodeReservation, Consumable, SpareItem, UNITS

CATEGORIES = {
    'Factory & Production Consumables': {'Lubricants and oils':'LUB', 'Machine cleaning materials':'MCL', 'Printing consumables':'PRT', 'Electrical consumables':'ELE', 'Packaging consumables':'PKG', 'Tools and workshop supplies':'TLS', 'Quality control supplies':'QCS', 'Factory safety supplies':'SAF'},
    'Pantry & Refreshments': {'Pantry & Refreshments':'PAN'},
    'Housekeeping & Cleaning': {'Housekeeping & Cleaning':'HSK'},
    'Office Stationery': {'Office Stationery':'STA'},
    'Office Maintenance Supplies': {'Office Maintenance Supplies':'OMT'},
    'Employee Welfare': {'Employee Welfare':'WEL'},
    'General Administration': {'General Administration':'ADM'},
}
LEGACY_OFFICE = {'Pantry & Refreshments':'PAN', 'Housekeeping & Cleaning':'HSK', 'Office Stationery':'STA', 'Office Maintenance':'OMT', 'Employee Welfare':'WEL', 'General Administration':'ADM'}
DEPARTMENTS = ['Office / Administration', 'Production', 'Maintenance', 'Quality Control', 'Stores', 'Other']
PURPOSES = ['Office Consumption', 'Pantry / Staff Refreshments', 'Housekeeping', 'Machine Maintenance', 'Production Support', 'Quality Control', 'Employee Welfare', 'Other']

class CatalogueBusy(APIException):
    status_code = 503
    default_detail = 'Catalogue is busy. Retry with the same request ID.'


def serialized(fn):
    @wraps(fn)
    def command(*args, **kwargs):
        for attempt in range(5):
            try:
                with transaction.atomic():
                    # First DB operation is a write. SQLite obtains its writer lock before
                    # reading balances/counters; PostgreSQL locks this one catalogue row.
                    if CatalogueSequence.objects.filter(pk='catalogue-lock').update(value=F('value')) != 1:
                        raise CatalogueBusy('Catalogue migration is not installed.')
                    return fn(*args, **kwargs)
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or not any(s in str(exc).lower() for s in ('locked', 'busy')):
                    raise
                if attempt == 4 or connection.in_atomic_block:
                    raise CatalogueBusy() from exc
                time.sleep(0.02 * (attempt + 1))
    return command


def canonical(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, allow_nan=False))


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def snapshot(item):
    return canonical({f.name: getattr(item, f.attname) for f in item._meta.concrete_fields if f.name not in {'creation_digest', 'creation_request_id'}})


def kind(item):
    return 'consumables' if isinstance(item, Consumable) else 'spares'


def audit(item, actor, action, previous, next_value, reference='', department='', remarks=''):
    # Reuse the existing immutable management revision store. These internal keys are
    # deliberately absent from the generic shared-entry write allowlist.
    from erp.models import SharedEntry, EntryRevision
    key = f'catalogue-{kind(item)}-{item.pk}'
    entry, _ = SharedEntry.objects.get_or_create(key=key, defaults={'content':{}, 'version':0, 'updated_by':actor})
    content = canonical({'item_id':str(item.pk), 'item_code':item.code, 'action':action, 'previous':previous, 'next':next_value,
                         'reference':reference, 'department':department or item.department, 'remarks':remarks})
    entry.version += 1
    entry.content = content
    entry.updated_by = actor
    entry.save()
    return EntryRevision.objects.create(entry=entry, version=entry.version, command_id=uuid4(), content=content, content_hash=digest(content), actor=actor)


def allocate(prefix, reserve_code=False):
    sequence, _ = CatalogueSequence.objects.get_or_create(scope=prefix)
    while True:
        sequence.value += 1
        sequence.save(update_fields=['value'])
        result = f'{prefix}{sequence.value:03d}' if reserve_code else f'{prefix}{sequence.value:05d}'
        if not reserve_code:
            return result
        if not ConsumableCodeReservation.objects.filter(pk=result.upper()).exists():
            ConsumableCodeReservation.objects.create(code=result.upper())
            return result


def balance(item):
    # Sum Python Decimals, avoiding SQLite floating-point SUM coercion.
    rows = list(item.movements.all())
    openings = [m.quantity for m in rows if m.is_opening]
    opening = sum(openings, Decimal('0')) if openings else None
    incoming = sum((m.quantity for m in rows if not m.is_opening and m.movement_type == 'IN'), Decimal('0'))
    outgoing = sum((m.quantity for m in rows if m.movement_type == 'OUT'), Decimal('0'))
    return {'opening_stock':opening, 'stock_in':incoming, 'stock_out':outgoing, 'current_stock':None if opening is None else opening + incoming - outgoing}


class MovementInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    movement_type = serializers.ChoiceField(choices=['IN','OUT'])
    is_opening = serializers.BooleanField(default=False)
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0'))
    unit = serializers.ChoiceField(choices=UNITS)
    reference = serializers.CharField(max_length=250, allow_blank=True, default='')
    date = serializers.DateField(required=False)
    unit_cost = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0'), allow_null=True, default=None)
    supplier = serializers.CharField(max_length=250, allow_blank=True, default='')
    department = serializers.ChoiceField(choices=['']+DEPARTMENTS, default='')
    storage_location = serializers.CharField(max_length=250, allow_blank=True, default='')
    received_issued_by = serializers.CharField(max_length=250, allow_blank=True, default='')
    issued_to = serializers.CharField(max_length=250, allow_blank=True, default='')
    purpose = serializers.ChoiceField(choices=['']+PURPOSES, default='')
    machine = serializers.CharField(max_length=250, allow_blank=True, default='')
    job_reference = serializers.CharField(max_length=250, allow_blank=True, default='')
    purchase_order_reference = serializers.CharField(max_length=250, allow_blank=True, default='')
    invoice_reference = serializers.CharField(max_length=250, allow_blank=True, default='')
    remarks = serializers.CharField(max_length=5000, allow_blank=True, default='')


@serialized
def post_movement(item_id, data, actor, item_model=SpareItem, *, _opening_authority=None):
    from .catalog_models import SpareStockMovement, ConsumableStockMovement
    movement_model = ConsumableStockMovement if item_model == Consumable else SpareStockMovement
    s = MovementInput(data=data); s.is_valid(raise_exception=True); v = s.validated_data
    item = item_model.objects.get(pk=item_id)
    if item_model == SpareItem and v['is_opening']:
        from .spare_openings import validate_opening_authority
        validate_opening_authority(item, v, actor, _opening_authority)
    previous = movement_model.objects.filter(request_id=v['request_id']).first()
    payload_hash = digest(v)
    if previous:
        if previous.recorded_by_id != actor.pk or previous.item_id != item.pk or (previous.payload_hash and previous.payload_hash != payload_hash):
            raise serializers.ValidationError('Request ID already used with different details.')
        if not previous.payload_hash and any(getattr(previous, key) != value for key,value in v.items() if key in {'movement_type','is_opening','quantity','unit','reference'}):
            raise serializers.ValidationError('Request ID already used with different details.')
        return previous, False
    if not item.is_active or item.unit != v['unit']:
        raise serializers.ValidationError('Use an active item and its exact unit.')
    if isinstance(item, Consumable) and ('spare' in item.category.lower() or 'spare' in item.subcategory.lower()):
        raise serializers.ValidationError('This legacy classification requires reviewed migration; stock was not changed.')
    totals = balance(item)
    date = v.get('date') or timezone.localdate()
    if date > timezone.localdate():
        raise serializers.ValidationError({'date':'Future movements are not allowed.'})
    if v['is_opening']:
        if v['movement_type'] != 'IN' or item.movements.exists():
            raise serializers.ValidationError('Opening must be the first and only opening entry.')
    elif v['quantity'] <= 0 or totals['opening_stock'] is None:
        raise serializers.ValidationError('A positive quantity and explicit opening balance are required.')
    if not v['is_opening'] and isinstance(item, Consumable):
        if not v['department']:
            raise serializers.ValidationError({'department':'Department is required.'})
        if v['movement_type'] == 'IN' and (not v['supplier'] or not v['storage_location']):
            raise serializers.ValidationError('Supplier and storage location are required for receipts.')
        if v['movement_type'] == 'OUT' and (not v['issued_to'] or not v['purpose']):
            raise serializers.ValidationError('Issued to and purpose are required for issues.')
    if v['movement_type'] == 'OUT' and v['quantity'] > totals['current_stock']:
        raise serializers.ValidationError('Insufficient stock.')
    rows = list(item.movements.all())
    opening = next((m for m in rows if m.is_opening), None)
    if opening and date < (opening.date or timezone.localtime(opening.created_at).date()):
        raise serializers.ValidationError({'date':'Movement cannot precede the recorded opening.'})
    running = Decimal('0')
    dated = [(m.date or timezone.localtime(m.created_at).date(), index, m.quantity if m.movement_type=='IN' else -m.quantity) for index,m in enumerate(sorted(rows,key=lambda m:m.created_at))]
    dated.append((date,len(rows),v['quantity'] if v['movement_type']=='IN' else -v['quantity']))
    for _,_,change in sorted(dated):
        running += change
        if running < 0:
            raise serializers.ValidationError('Insufficient stock on the selected date.')
    prefix = ('C' if item_model == Consumable else 'S') + ('IN' if v['movement_type']=='IN' else 'OUT') + f'-{date.year}-'
    reference = allocate(prefix)
    previous_cost = item.unit_cost
    movement = movement_model.objects.create(item=item, recorded_by=actor, **{**v,'date':date}, transaction_reference=reference, payload_hash=payload_hash)
    if v['movement_type']=='IN' and v['unit_cost'] is not None:
        newer = item.movements.filter(movement_type='IN', unit_cost__isnull=False, date__gt=date).exists()
        if not newer:
            item.unit_cost=v['unit_cost']; item.updated_by=actor; item.save()
    audit(item,actor,'Stock IN' if v['movement_type']=='IN' else 'Stock OUT', {**{k:None if value is None else str(value) for k,value in totals.items()}, 'unit_cost':None if previous_cost is None else str(previous_cost)}, {**{k:None if value is None else str(value) for k,value in balance(item).items()}, 'unit_cost':None if item.unit_cost is None else str(item.unit_cost)}, reference, v['department'], v['remarks'])
    return movement, True


@serialized
def save_item(serializer, actor):
    model = serializer.Meta.model
    values = dict(serializer.validated_data)
    opening = values.pop('opening_stock', None)
    if model == SpareItem and opening is not None:
        raise serializers.ValidationError({'opening_stock':'Use a spare opening request with independent approval; master creation cannot post openings.'})
    request_id = values.get('creation_request_id')
    initial_digest = digest({**values, 'opening_stock':opening})
    if serializer.instance is None and request_id:
        previous = model.objects.filter(creation_request_id=request_id).first()
        if previous:
            if previous.created_by_id != actor.pk or previous.creation_digest != initial_digest:
                raise serializers.ValidationError('Creation request ID already used with different details.')
            return previous
    if serializer.instance:
        item = model.objects.get(pk=serializer.instance.pk)
        before = snapshot(item)
        if values.get('code',item.code) != item.code:
            raise serializers.ValidationError({'code':'Item codes are immutable.'})
        if model == Consumable and ('spare' in item.category.lower() or 'spare' in item.subcategory.lower()):
            raise serializers.ValidationError('Legacy spare classification requires reviewed migration.')
        if item.movements.exists() and values.get('unit',item.unit)!=item.unit:
            raise serializers.ValidationError({'unit':'Unit is locked after an opening or movement.'})
        if opening is not None:
            raise serializers.ValidationError({'opening_stock':'Use an explicit opening movement; existing balances cannot be overwritten.'})
        for key,value in values.items():
            if key != 'creation_request_id': setattr(item,key,value)
        item.updated_by=actor; item.save()
        if before['is_active'] != item.is_active:
            audit(item,actor,'Item Activated / Deactivated',before['is_active'],item.is_active,item.code)
        audit(item,actor,'Item Updated',before,snapshot(item),item.code)
        return item
    if model == Consumable:
        category, subcategory = values.get('category',''), values.get('subcategory','')
        if category:
            if values.get('code'):
                raise serializers.ValidationError({'code':'Omit code for category-based creation; the backend allocates it.'})
            values['code'] = allocate(f'CON-{CATEGORIES[category][subcategory]}-',True)
        elif values.get('code'):
            # Legacy manual-code clients remain compatible, but cannot allocate the new namespace.
            if values['code'].upper().startswith('CON-') or ConsumableCodeReservation.objects.filter(pk=values['code'].upper()).exists():
                raise serializers.ValidationError({'code':'This code is reserved. Use category-based creation.'})
            ConsumableCodeReservation.objects.create(code=values['code'].upper())
        else:
            raise serializers.ValidationError({'category':'Category and subcategory are required for generated codes.'})
    if model.objects.filter(code__iexact=values['code']).exists():
        raise serializers.ValidationError({'code':'An item with this code already exists.'})
    item = model.objects.create(**values, created_by=actor, updated_by=actor, creation_digest=initial_digest)
    audit(item,actor,'Item Created',None,snapshot(item),item.code)
    if opening is not None:
        movement_model_name = 'consumablestockmovement' if model == Consumable else 'sparestockmovement'
        if not actor.has_perm(f'inventory.add_{movement_model_name}') or not actor.has_perm(f'inventory.view_{model._meta.model_name}'):
            raise serializers.ValidationError({'opening_stock':'Stock-posting permission is required to record an opening.'})
        post_movement(item.pk,{'request_id':str(uuid4()),'movement_type':'IN','is_opening':True,'quantity':str(opening),'unit':item.unit},actor,model)
    return item
