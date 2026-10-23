"""Controlled Draft commands only. No approval, allocation or stock effects."""
from copy import deepcopy
import json
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import APIException
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (CommandReceipt, ContributorEvidence, WorkflowJob,
                     WorkflowOrder, WorkflowOrderRevision)
from .order_control_foundation import (OrderControlError, capture_snapshot,
                                      require_order_revision, require_version,
                                      verify_snapshot)
from .production_workflow import validated_order_total


class DraftError(APIException):
    def __init__(self, code, status=409):
        self.status_code = status
        self.detail = {'code': code, 'detail': {
            'VALIDATION_ERROR': 'Check the supplied Draft fields.',
            'VERSION_CONFLICT': 'Reload this Draft before saving again.',
        }.get(code, 'This Draft request cannot be completed.'), 'fields': {}}


def strict_keys(value, allowed):
    if type(value) is not dict or set(value) - set(allowed):
        raise DraftError('VALIDATION_ERROR', 400)


def shape(value, schema):
    """Reject unknown nested fields and coercion; retain exact supplied values."""
    strict_keys(value, schema)
    for key, item in value.items():
        rule = schema[key]
        if type(rule) is dict:
            shape(item, rule)
        elif rule == 'number':
            if type(item) not in (int, float, str):
                raise DraftError('VALIDATION_ERROR', 400)
            # Same magnitude/finite rules as current capture, without boolean coercion.
            from .production_workflow import number
            number(item)
        elif rule == 'integer':
            if type(item) is not int or not 0 <= item <= 2**63 - 1:
                raise DraftError('VALIDATION_ERROR', 400)
        elif type(item) is not rule or (rule is str and len(item) > 2000):
            raise DraftError('VALIDATION_ERROR', 400)


CONVERSION = dict.fromkeys(('productId', 'modelId', 'originalUnit', 'conversionId'), str)
CONVERSION.update(originalQuantity='number', convertedKG='number', conversionVersion='integer')
LED = dict(applies=bool, percentageBase=str, eligibleWeightKG='number', usdToUGX='number',
           fxDate=str, vatOnLED=bool, conversion=CONVERSION)
LED_SNAPSHOT = dict.fromkeys(('baseAmount', 'eligibleWeightKG', 'percentageRate', 'usdPerTonne',
                             'usdToUGX', 'percentageAmount', 'weightAmount', 'selectedUnrounded',
                             'selectedAmount'), 'number')
LED_SNAPSHOT.update(method=str, percentageBase=str, fxDate=str, selected=str,
                    vatOnLED=bool, conversion=CONVERSION)
ITEM = dict.fromkeys(('id', 'modelId', 'specifications', 'requiredDate', 'estimateId',
                      'fulfillmentSource', 'itemId', 'itemName', 'unit', 'quotationLineId',
                      'quotationCostEstimateId'), str)
ITEM.update(modelRevision='integer', quotationCostEstimateRevision='integer',
            quantity='number', unitPrice='number', totalPrice='number', led=LED, ledSnapshot=LED_SNAPSHOT)
CONTENT = dict.fromkeys(('customerName', 'customerId', 'date', 'marketingPersonName', 'customerType',
                         'currency', 'requisitionOrder', 'quotationId', 'quotationRevisionId',
                         'quotationNumber', 'prospectId'), str)
CONTENT.update(taxRate='number', quotationRevision='integer',
               commercialPricing={'taxMode': str, 'discount': 'number', 'roundingPolicy': str},
               customerContact=dict.fromkeys(('customerId', 'prospectId', 'name', 'billingAddress',
                                              'deliveryAddress', 'contactPerson', 'telephone',
                                              'email', 'customerType', 'tin'), str),
               commercialTerms={**dict.fromkeys(('name', 'priceValidity', 'deliveryPeriod',
                                                 'paymentTerms', 'revisionClause', 'additionalTerms',
                                                 'remarks'), str), 'advancePercent': 'number',
                                'creditDays': 'number', 'quantityTolerance': 'number'})


def content_total(content):
    strict_keys(content, {*CONTENT, 'items'})
    shape({key: value for key, value in content.items() if key != 'items'}, CONTENT)
    items = content.get('items')
    if type(items) is not list or not 1 <= len(items) <= 100:
        raise DraftError('VALIDATION_ERROR', 400)
    for item in items:
        shape(item, ITEM)
        for key in ('id', 'itemName', 'unit', 'fulfillmentSource'):
            if not item.get(key, '').strip():
                raise DraftError('VALIDATION_ERROR', 400)
        if item.get('modelRevision') == 0:
            raise DraftError('VALIDATION_ERROR', 400)
    # All existing reference, unit, quantity, pricing, routing and recipe checks.
    return validated_order_total(content)


def access(actor, action):
    if (not actor.is_authenticated or not actor.is_active or
            not actor.has_perm('erp.view_workfloworder') or
            (action and not actor.has_perm('erp.' + action + '_workfloworder'))):
        raise DraftError('PERMISSION_DENIED', 403)


def visible_order(pk, *, lock=False):
    # Existing workflow visibility is global under view_workfloworder; no new role/scope grants.
    rows = WorkflowOrder.objects.select_for_update() if lock else WorkflowOrder.objects
    order = rows.filter(pk=pk).first()
    if order is None:
        raise DraftError('NOT_FOUND', 404)
    return order


def checked_revision(order):
    revision = order.current_revision
    if revision is None:
        raise DraftError('LEGACY_REVIEW_REQUIRED')
    require_order_revision(order.pk, revision.order_id)
    verify_snapshot(revision.content, revision.content_hash)
    # A valid hash is insufficient when persisted root content differs from the revision.
    saved = {key: value for key, value in order.data.items() if key in {*CONTENT, 'items', 'orderTotal'}}
    if capture_snapshot(saved).digest != revision.content_hash:
        raise DraftError('REVISION_CONFLICT')
    return revision


def projection(order):
    return {**deepcopy(order.data), 'controlVersion': order.control_version,
            'commercialStatus': order.commercial_status,
            'currentRevision': order.current_revision_id,
            'confirmedRevision': order.confirmed_revision_id}


@transaction.atomic
def save_draft(actor, payload, pk=None):
    """Receipt reservation, root CAS, revision and evidence share one transaction."""
    access(actor, 'add' if pk is None else 'change')
    strict_keys(payload, ('commandId', 'expectedVersion', 'reason', 'evidenceIds', 'content'))
    if set(payload) != {'commandId', 'expectedVersion', 'reason', 'evidenceIds', 'content'}:
        raise DraftError('VALIDATION_ERROR', 400)
    command_id = serializers.UUIDField().run_validation(payload['commandId'])
    if (type(payload['reason']) is not str or not payload['reason'].strip() or
            len(payload['reason']) > 2000 or payload['evidenceIds'] != []):
        # No external evidence repository is activated. Draft capture needs no external evidence.
        raise DraftError('VALIDATION_ERROR', 400)
    expected = payload['expectedVersion']
    require_version(expected, 0 if pk is None else expected, creating=pk is None)
    action = 'order.draft.create' if pk is None else 'order.draft.edit'
    digest = capture_snapshot({'schema': 'so-draft-command-v1', 'actor': actor.pk,
                               'action': action, 'workflowOrder': pk, 'payload': payload}).digest
    order = visible_order(pk, lock=True) if pk is not None else None
    receipt, created = CommandReceipt.objects.get_or_create(command_id=command_id, defaults={
        'actor': actor, 'action': action, 'schema_version': 1, 'payload_digest': digest})
    receipt = CommandReceipt.objects.select_for_update().get(pk=receipt.pk)
    if not created:
        if (receipt.actor_id != actor.pk or receipt.action != action or
                receipt.payload_digest != digest or receipt.completed_at is None or
                (pk is not None and receipt.workflow_order_id != pk)):
            raise DraftError('COMMAND_PAYLOAD_CONFLICT')
        return deepcopy(receipt.response_body), receipt.response_status, True
    total = content_total(payload['content'])
    content = deepcopy(payload['content'])
    previous = None
    if order is None:
        # Also protects collision with historical creationKey without touching that row.
        if WorkflowOrder.objects.filter(request_id=command_id).exists():
            raise DraftError('COMMAND_PAYLOAD_CONFLICT')
        order = WorkflowOrder.objects.create(request_id=command_id, data={}, created_by=actor,
                                             control_version=1, commercial_status='Draft')
        number = 1
        entry = deepcopy(content)
    else:
        require_version(expected, order.control_version)
        if (order.commercial_status != 'Draft' or order.confirmed_revision_id is not None or
                order.data.get('status') != 'Draft' or order.data.get('jobCardIds') or
                WorkflowJob.objects.filter(order=order).exists()):
            raise DraftError('ORDER_NOT_EDITABLE')
        previous = checked_revision(order)
        if expected == 2**63 - 1 or previous.revision == 2**63 - 1:
            raise DraftError('VERSION_CONFLICT')
        number = previous.revision + 1
        entry = deepcopy(order.data.get('entryRequest'))
    snapshot = capture_snapshot({**content, 'orderTotal': total})
    revision = WorkflowOrderRevision.objects.create(order=order, revision=number,
        content=snapshot.decode(), content_hash=snapshot.digest, captured_by=actor,
        reason=payload['reason'], provenance='Contemporaneous')
    contributors = {actor.pk, order.created_by_id}
    if previous is not None:
        inherited = set(previous.contributors.values_list('actor_id', flat=True))
        if not inherited or order.created_by_id not in inherited:
            raise DraftError('MAKER_EVIDENCE_REQUIRED')
        contributors |= inherited
    for actor_id in sorted(contributors):
        ContributorEvidence.objects.create(event_id=uuid4(), order_revision=revision,
            actor_id=actor_id, action='Draft saved' if actor_id == actor.pk else 'Inherited contributor')
    data = {**snapshot.decode(), 'id': 'wf-order-' + str(order.pk),
            'orderNumber': f'SO-W{order.pk:05d}', 'status': 'Draft', 'jobCardIds': [],
            'entryRequest': entry, 'createdBy': order.created_by_id, 'persistedWorkflow': True}
    updated = WorkflowOrder.objects.filter(pk=order.pk, control_version=expected if pk else 1).update(
        data=data, current_revision=revision, control_version=expected + 1, updated_at=timezone.now())
    if updated != 1:
        raise DraftError('VERSION_CONFLICT')
    order.refresh_from_db()
    result = projection(order)
    receipt.workflow_order = order
    receipt.response_status = 201 if pk is None else 200
    receipt.response_body = result
    receipt.completed_at = timezone.now()
    receipt.save()
    return result, receipt.response_status, False


class StrictJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        try:
            return json.load(stream, object_pairs_hook=unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise DraftError('VALIDATION_ERROR', 400)


class DraftAPI(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, OrderControlError):
            exc = DraftError(exc.code, 400 if exc.code == 'VALIDATION_ERROR' else 409)
        elif isinstance(exc, serializers.ValidationError):
            exc = DraftError('VALIDATION_ERROR', 400)
        return super().handle_exception(exc)

    def post(self, request, pk=None):
        try:
            body, status, replay = save_draft(request.user, request.data, pk)
        except IntegrityError:
            # Transaction already rolled back. No target/version/actor details disclosed.
            raise DraftError('COMMAND_PAYLOAD_CONFLICT')
        return Response(body, status=status, headers={'Idempotency-Replayed': str(replay).lower()})


class DraftDetail(DraftAPI):
    def get(self, request, pk):
        access(request.user, None)
        order = visible_order(pk)
        # Read a coherent root/revision pair without taking write locks.
        if order.control_version is not None:
            checked_revision(order)
        return Response(projection(order))


class DraftHistory(DraftAPI):
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        access(request.user, None)
        order = visible_order(pk)
        results = []
        for revision in order.control_revisions.order_by('revision'):
            require_order_revision(order.pk, revision.order_id)
            verify_snapshot(revision.content, revision.content_hash)
            results.append({'id': revision.pk, 'revision': revision.revision,
                'content': revision.content, 'contentHash': revision.content_hash,
                'capturedAt': revision.captured_at.isoformat(), 'reason': revision.reason})
        return Response({'orderId': order.pk, 'revisions': results})
