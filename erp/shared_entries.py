"""Versioned storage for supporting records, with immutable management history.

Operational job, stock and approval commands must use their existing services.
No generic state key can bypass their authority gates.
"""
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied, APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SharedEntry, EntryRevision, WorkflowJob

MANAGEMENT = {'admin', 'project_manager', 'production_manager', 'accounts'}
QC = {'admin', 'qc_inspector', 'qc_supervisor'}
COMMERCIAL = {'admin', 'receptionist', 'accounts', 'production_manager', 'project_manager'}
ALL = MANAGEMENT | QC | {'receptionist', 'store_keeper', 'production_operator', 'recycling_operator'}
# (read roles, write roles, root content type)
SCOPES = {
    'labour-job-entries': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'inhouse-waste-entries': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'inhouse-waste-rates': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'labour-job-options': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'recycled-material-entries': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'recycled-material-catalog': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'recycling-materials': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'recycling-purchases': ({'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}, {'admin', 'accounts', 'store_keeper', 'recycling_operator'}, list),
    'quality-state': (ALL, QC | {'production_manager'}, dict),
    'quality-schedules': (ALL, {'admin', 'qc_supervisor'}, list),
    'quality-templates': (ALL, {'admin'}, list),
    'quality-areas': (ALL, {'admin'}, list),
    'costing-estimates': (COMMERCIAL, COMMERCIAL - {'project_manager'}, list),
    'costing-prices': (MANAGEMENT | {'receptionist', 'store_keeper'}, {'admin', 'accounts'}, list),
    'exchange-rates': (ALL, {'admin', 'accounts'}, list),
    'commercial-prospects': (COMMERCIAL, COMMERCIAL, list),
    'commercial-company': (ALL, {'admin'}, dict),
    'commercial-terms': (COMMERCIAL, {'admin'}, list),
    'inventory-conversions': (ALL, {'admin', 'production_manager'}, list),
    'inventory-thresholds': (ALL, {'admin', 'production_manager', 'store_keeper'}, list),
    'inventory-pieces': (ALL, {'admin', 'store_keeper'}, dict),
    'master-items': (ALL, {'admin'}, list),
    'master-suppliers': (ALL, {'admin'}, list),
    'master-departments': (ALL, {'admin'}, list),
    # Durable editor work, scoped to its author. Canonical quotation submission,
    # approval and delivery remain on the existing quotation services.
    'quotation-workspace': (COMMERCIAL, COMMERCIAL, list),
    'inventory-ledger': (ALL, {'admin', 'store_keeper', 'receptionist'}, dict),
    'invoices': (COMMERCIAL | {'store_keeper'}, {'admin', 'accounts', 'receptionist'}, list),
    'job-tracking': (ALL, QC | {'production_manager', 'production_operator', 'store_keeper', 'receptionist'}, dict),
    'order-links': (ALL, COMMERCIAL | {'store_keeper'}, list),
}


def role(user):
    return user.effective_role if user.is_active else ''


def permitted(user, roles, capability='view_sharedentry'):
    return user.is_active and (user.is_superuser or role(user) in roles) and user.has_perm('erp.' + capability)


def canonical(content):
    try:
        encoded = json.dumps(content, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise serializers.ValidationError('Enter valid finite JSON data.')
    if len(encoded.encode()) > 2_000_000:
        raise serializers.ValidationError('This record exceeds the 2 MB limit.')
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_content(key, content, previous, user):
    if not isinstance(content, SCOPES[key][2]):
        raise serializers.ValidationError('Invalid record structure.')
    if isinstance(content, list) and (len(content) > 10000 or any(not isinstance(row, dict) for row in content)):
        raise serializers.ValidationError('Records must be a list of objects (maximum 10,000).')
    if key == 'labour-job-options':
        from .labour_jobs import validate_options
        validate_options(content, previous)
    if key in {'inhouse-waste-entries', 'inhouse-waste-rates'}:
        from .inhouse_waste import validate_entries, validate_rates
        (validate_entries if key == 'inhouse-waste-entries' else validate_rates)(content, previous)
    if key == 'labour-job-entries':
        from .labour_jobs import validate_entries
        validate_entries(content, previous)
    if key == 'recycled-material-entries':
        from .recycled_materials import validate_entries
        validate_entries(content, previous)
    if key == 'recycled-material-catalog':
        from .recycled_materials import validate_catalog
        validate_catalog(content, previous)
    if key == 'recycling-purchases':
        from .recycling_purchases import validate_purchases
        validate_purchases(content, previous, user)
    if key == 'recycling-materials':
        from .recycling_purchases import validate_materials
        validate_materials(content, previous)
    if key == 'quality-state':
        if set(content) != {'inspections', 'releases', 'nonConformances'} or any(not isinstance(rows, list) for rows in content.values()):
            raise serializers.ValidationError('Invalid quality record structure.')
        for rows in content.values():
            if len(rows) > 10000 or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
                raise serializers.ValidationError('Quality lists require identified records (maximum 10,000).')
            if len({row['id'] for row in rows}) != len(rows):
                raise serializers.ValidationError('Quality record IDs must be unique.')
        from .operational_records import validate_quality
        validate_quality(content, previous, user)
    if key == 'costing-estimates':
        from .operational_records import validate_costing
        validate_costing(content, previous, user)
    if key == 'inventory-ledger':
        from .operational_records import validate_ledger
        validate_ledger(content, previous, user)
    if key == 'invoices':
        from .operational_records import validate_invoices
        validate_invoices(content, previous, user)
    if key == 'job-tracking':
        from .operational_records import validate_tracking
        validate_tracking(content, previous, user)
    if key == 'order-links':
        from .operational_records import validate_order_links
        validate_order_links(content, previous, user)
    if key == 'exchange-rates':
        from datetime import date
        for row in content:
            try:
                date.fromisoformat(row['date'])
                rate = row['rate']
                if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
                    raise ValueError()
                if row['currency'] not in {'USD', 'NGN', 'GHS', 'ZAR'} or not isinstance(row['id'], str) or not row['id']:
                    raise ValueError()
            except (KeyError, ValueError, TypeError, AssertionError):
                raise serializers.ValidationError('Provide an ID, supported currency, valid date and positive exchange rate.')
        ids = [row['id'] for row in content]
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError('Exchange-rate IDs must be unique.')
        current = {row['id']: row for row in content}
        if any(current.get(row['id']) != row for row in previous or []):
            raise serializers.ValidationError('Recorded exchange rates are immutable. Append a new rate.')
    if key == 'quotation-workspace':
        if any(not isinstance(row.get('id'), str) or not isinstance(row.get('lines'), list) or not isinstance(row.get('events'), list) or not isinstance(row.get('responses'), list) for row in content):
            raise serializers.ValidationError('Quotation editor records require IDs, lines, events and responses.')
        if len({row['id'] for row in content}) != len(content):
            raise serializers.ValidationError('Quotation editor IDs must be unique.')


def entry_data(entry):
    return {'key': entry.key.split(':')[0], 'content': entry.content, 'version': entry.version,
            'updatedAt': entry.updated_at.isoformat(), 'updatedBy': str(entry.updated_by_id)}


class SharedEntries(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        keys = [key for key, (read, _, _) in SCOPES.items() if permitted(request.user, read)]
        stored_keys = [f'{key}:{request.user.pk}' if key == 'quotation-workspace' else key for key in keys]
        return Response({'entries': [entry_data(row) for row in SharedEntry.objects.filter(key__in=stored_keys)],
                         'readable': keys, 'writable': [key for key in keys if permitted(request.user, SCOPES[key][1], 'change_sharedentry')]})


class SharedEntryDetail(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, key):
        if key not in SCOPES or not permitted(request.user, SCOPES[key][1], 'change_sharedentry'):
            raise PermissionDenied('Your account cannot write this shared record.')
        body = request.data
        try:
            command = UUID(str(body['commandId']))
            version = body['expectedVersion']
            if type(version) is not int or version < 0:
                raise ValueError()
            content = body['content']
        except (KeyError, ValueError, TypeError):
            raise serializers.ValidationError('Supply commandId, nonnegative expectedVersion and content.')
        digest = canonical(content)
        stored_key = f'{key}:{request.user.pk}' if key == 'quotation-workspace' else key
        try:
            with transaction.atomic():
                receipt = EntryRevision.objects.filter(command_id=command).first()
                if receipt:
                    if receipt.entry_id != stored_key or receipt.actor_id != request.user.pk or receipt.content_hash != digest:
                        return Response({'detail': 'This command ID was already used for different data.'}, status=409)
                    return Response({'key': key, 'version': receipt.version, 'commandId': str(command)})
                entry = SharedEntry.objects.select_for_update().filter(pk=stored_key).first()
                if version != (entry.version if entry else 0):
                    return Response({'detail': 'Another user changed this record. Export your pending entry and reload before reapplying it.'}, status=409)
                try:
                    validate_content(key, content, entry.content if entry else None, request.user)
                except (KeyError, TypeError, ValueError, AttributeError, StopIteration, OverflowError):
                    raise serializers.ValidationError('Complete the required record fields with valid types and references.')
                next_version = version + 1
                previous_content = entry.content if entry else None
                if entry:
                    # Compare-and-swap also protects engines without row locks.
                    from django.utils import timezone
                    if not SharedEntry.objects.filter(pk=stored_key, version=version).update(content=content, version=next_version, updated_by=request.user, updated_at=timezone.now()):
                        error = APIException('Concurrent update; reload before saving.'); error.status_code = 409; raise error
                else:
                    entry = SharedEntry.objects.create(key=stored_key, content=content, version=next_version, updated_by=request.user)
                EntryRevision.objects.create(entry=entry, version=next_version, command_id=command, content=content, content_hash=digest, actor=request.user)
                if key in {'invoices', 'inventory-ledger'}:
                    from accounting.integration import shared_saved
                    shared_saved(key, content, previous_content, request.user)
                return Response({'key': key, 'version': next_version, 'commandId': str(command)})
        except IntegrityError:
            return Response({'detail': 'Concurrent update; retry the same command or reload.'}, status=409)


class ManagementActivity(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not permitted(request.user, MANAGEMENT, 'view_entryrevision'):
            raise PermissionDenied('Management access is required to review shared activity.')
        try:
            before = int(request.query_params.get('before', '0'))
        except ValueError:
            raise serializers.ValidationError('Invalid history cursor.')
        query = EntryRevision.objects.select_related('actor').order_by('-id')
        if before:
            query = query.filter(id__lt=before)
        if request.query_params.get('key'):
            query = query.filter(entry_id=request.query_params['key'])
        rows = list(query[:51])
        return Response({'results': [{'id': row.pk, 'actor': row.actor.get_full_name() or row.actor.username,
            'actorId': str(row.actor_id), 'at': row.recorded_at.isoformat(), 'key': row.entry_id,
            'version': row.version, 'content': row.content, 'hash': row.content_hash} for row in rows[:50]],
            'nextBefore': rows[49].pk if len(rows) > 50 else None})


class SharedEntryCommands(APIView):
    """One UI operation may update stock, order links and a Job Card together."""
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        from types import SimpleNamespace
        if not isinstance(request.data, dict):
            raise serializers.ValidationError('Supply an object containing entry commands.')
        commands = request.data.get('commands')
        if not isinstance(commands, list) or not 1 <= len(commands) <= 50 or any(not isinstance(command, dict) or not isinstance(command.get('key'), str) for command in commands):
            raise serializers.ValidationError('Supply between one and 50 identified entry commands.')
        results = []
        for command in commands:
            result = SharedEntryDetail().put(SimpleNamespace(user=request.user, data=command), command['key'])
            if not 200 <= result.status_code < 300:
                transaction.set_rollback(True)
                return result
            results.append(result.data)
        return Response({'results': results})


class CostingSummary(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not permitted(request.user, SCOPES['costing-estimates'][0]):
            raise PermissionDenied('Costing view permission is required.')
        from .operational_records import shared
        entries = shared('costing-estimates', [])
        latest = {}
        for estimate in entries:
            if estimate.get('revision', 0) >= latest.get(estimate['number'], {}).get('revision', 0):
                latest[estimate['number']] = estimate
        return Response([{'id': row['id'], 'itemOrModel': row['input']['product'],
            'rawMaterialCost': row['totals']['materialCost'] + row['totals']['inkCost'], 'productionCost': row['totals']['processCost'],
            'otherCost': row['totals']['wasteCost'] + row['input']['overheads'] + row['input']['additionalCosts'],
            'currency': row['input']['currency'], 'exchangeRateUsed': 1, 'totalCostLocal': row['totals']['costOfProduction'],
            'lastUpdated': (row.get('approvedAt') or row['createdAt'])[:10]} for row in latest.values()])


class RecyclingRecords(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind):
        if not permitted(request.user, ALL):
            raise PermissionDenied('Inventory view permission is required.')
        if kind == 'granules':
            # No separate granules-production entry is offered by this frontend.
            # Raw-material receipts are not fabricated into production batches.
            return Response([])
        from .operational_records import quantity
        result = []
        for job in WorkflowJob.objects.all():
            for child in job.data.get('manufacturingItems', []):
                for stage in child.get('productionStages', []):
                    waste = quantity(stage.get('wasteQuantity') or 0); sent = quantity(stage.get('sentToRecycling') or 0)
                    for suffix, amount, forwarded in [('sent', sent, True), ('pending', waste - sent, False)]:
                        if amount > 0:
                            result.append({'id': f"{child['id']}-{stage['id']}-{suffix}", 'jobCardNumber': child['jobCardNumber'],
                                'stage': stage['name'], 'wasteQty': float(amount), 'unit': stage['wasteUnit'], 'wasteType': stage.get('wasteType') or 'Unspecified',
                                'sentToRecycling': forwarded, 'date': (stage.get('completedAt') or stage.get('startedAt') or job.data['createdDate'])[:10]})
        return Response(result)
