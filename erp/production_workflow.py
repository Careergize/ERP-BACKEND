from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers, permissions
from rest_framework.exceptions import PermissionDenied, APIException
from rest_framework.response import Response
from rest_framework.views import APIView
from inventory.models import RawMaterial, RawMovement
from .models import ProductionProcess, ProductionModelRevision, WorkflowOrder, WorkflowJob, FinishedStockItem


def fail(message):
    raise serializers.ValidationError(message)


def allowed(user, action):
    if not user.is_active or not user.has_perm('erp.' + action):
        raise PermissionDenied('Your account does not have permission for this workflow action.')


def number(value, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal('99999999999'):
            fail('Enter valid nonnegative quantities within the supported range.')
        return result
    except (InvalidOperation, TypeError, ValueError):
        fail('Enter a valid number.')


def dated(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        fail('Enter a valid date.')


def event(user, action, department, description):
    return {'id': str(uuid4()), 'timestamp': timezone.now().isoformat(), 'action': action,
            'user': user.get_full_name() or user.username, 'department': department, 'description': description}


def route(stages):
    if not isinstance(stages, list) or not stages or len(stages) > 50:
        fail('Configure between one and 50 model process steps.')
    result = []
    for index, original in enumerate(stages):
        if not isinstance(original, dict):
            fail('Each process step must contain its configured fields.')
        if not all(str(original.get(key, '')).strip() for key in ('name', 'department', 'inputUnit', 'outputUnit', 'wasteUnit')):
            fail('Complete process names, departments and units.')
        if len(str(original['name'])) > 150:
            fail('Process names cannot exceed 150 characters.')
        stage = {key: deepcopy(original[key]) for key in ('name', 'department', 'inputUnit', 'outputUnit', 'wasteUnit', 'machine', 'machineId', 'instructions', 'qcTemplateIds') if key in original}
        stage.update(id=str(original.get('id') or uuid4()), sequence=index + 1, required=bool(original.get('required', True)), kind='production',
                     status='Pending', inputQuantity=None, outputQuantity=None, wasteQuantity=None, operator='', remarks='')
        for key in ('expectedInput', 'expectedOutput', 'plannedWastePercent'):
            if original.get(key) is not None:
                stage[key] = float(number(original[key]))
        if stage.get('plannedWastePercent', 0) > 100:
            fail('Planned waste cannot exceed 100%.')
        result.append(stage)
    if not any(stage['required'] for stage in result):
        fail('Select at least one required process.')
    if len({stage['id'] for stage in result}) != len(result):
        fail('Each process occurrence needs its own step ID.')
    return result


def requirements(model, item):
    output = number(model.get('productionBatchQuantity', model.get('batchQuantity')), True)
    output_unit = model.get('productionBatchUnit', model.get('batchUnit'))
    if item['unit'] != output_unit:
        fail('Order unit must match the model production output unit. Configure an explicit production batch/yield; no unit conversion is assumed.')
    scale = number(item['quantity'], True) / output
    recipe = model.get('recipe', [])
    if not recipe or len(recipe) > 100 or len({row.get('materialId') for row in recipe}) != len(recipe):
        fail('Configure distinct raw-material quantities in the selected model recipe.')
    percentages = model.get('recipeMode') == 'percentages'
    if percentages and sum(number(row.get('percentage', 0)) for row in recipe) != 100:
        fail('Recipe percentages must total 100%.')
    mass = {'kg': Decimal(1), 'g': Decimal('0.001'), 'tonne': Decimal(1000), 'tonnes': Decimal(1000)}
    result = []
    for row in recipe:
        raw = RawMaterial.objects.filter(pk=row.get('materialId')).first()
        if not raw or raw.unit != row.get('unit'):
            fail('Model recipes require exact saved raw-material IDs and units.')
        quantity = number(row.get('quantity'), True) * scale if not percentages else number(model.get('batchQuantity'), True) * number(row.get('percentage')) / 100 * scale
        if percentages:
            if model.get('batchUnit') not in mass or raw.unit not in mass:
                fail('Percentage recipes require explicit mass units.')
            quantity = quantity * mass[model['batchUnit']] / mass[raw.unit]
        quantity = quantity.quantize(Decimal('0.000001'))
        number(quantity, True)
        result.append({'id': str(uuid4()), 'materialId': raw.pk, 'name': raw.name, 'unit': raw.unit, 'requiredQuantity': float(quantity), 'issuedQuantity': 0})
    return result


def allocate(order, user):
    existing = WorkflowJob.objects.filter(order=order).first()
    if existing:
        return existing
    source = order.data
    manufacturing = [line for line in source['items'] if line['fulfillmentSource'] == 'manufacturing']
    if not manufacturing:
        fail('Stock-only Sales Orders do not need a Job Card.')
    parent_id = 'wf-job-' + str(uuid4())
    children = []
    for item in manufacturing:
        revision = ProductionModelRevision.objects.filter(model_id=item.get('modelId'), revision=item.get('modelRevision')).first()
        if not revision:
            fail('Save a configured production model revision before allocating the Job Card.')
        model = revision.data
        stages = route(model.get('routing', []))
        materials = requirements(model, item)
        scale = number(item['quantity'], True) / number(model.get('productionBatchQuantity', model.get('batchQuantity')), True)
        for stage in stages:
            for key in ('expectedInput', 'expectedOutput'):
                if key in stage:
                    stage[key] = float(number(stage[key]) * scale)
        due = item.get('requiredDate')
        if dated(due) < dated(source['date']):
            fail('Manufacturing items need delivery dates on or after the Sales Order date.')
        children.append({'id': 'wf-item-' + str(uuid4()), 'parentJobCardId': parent_id, 'jobCardNumber': '',
                         'salesOrderId': source['id'], 'salesOrderItemId': item['id'], 'salesOrderNumber': source['orderNumber'],
                         'customerName': source['customerName'], 'product': item['itemName'], 'modelId': revision.model_id,
                         'modelRevision': revision.revision, 'modelName': model['name'], 'qty': item['quantity'], 'unit': item['unit'],
                         'requiredDate': due, 'createdDate': timezone.localdate().isoformat(), 'specifications': item.get('specifications', ''),
                         'productionRequirements': 'Calculated from the saved model recipe and production batch output. PM review required.',
                         'status': 'PendingApproval', 'department': 'Production Manager', 'stages': [], 'productionStages': stages,
                         'requiredMaterials': materials, 'routingRevision': 1, 'persistedWorkflow': True,
                         'activityHistory': [event(user, 'Job Card allocated', 'Production Manager', 'Model process steps and scaled material requirements submitted for review.')]})
    job = WorkflowJob.objects.create(order=order, data={})
    job_number = f'JC-W{job.pk:05d}'
    for child in children:
        child['jobCardNumber'] = job_number
        child['workflowKey'] = job.pk
    job.data = {'id': parent_id, 'jobCardNumber': job_number, 'origin': 'sales_order', 'salesOrderId': source['id'],
                'salesOrderNumber': source['orderNumber'], 'customerName': source['customerName'], 'modelName': 'Manufacturing items',
                'qty': 0, 'status': 'PendingApproval', 'department': 'Production Manager', 'stages': [], 'manufacturingItems': children,
                'persistedWorkflow': True, 'workflowKey': job.pk, 'workflowVersion': 1, 'createdDate': timezone.localdate().isoformat(),
                'requiredDate': min(child['requiredDate'] for child in children), 'activityHistory': []}
    job.save(update_fields=['data'])
    order.data = {**source, 'jobCardIds': [parent_id]}; order.save(update_fields=['data', 'updated_at'])
    return job


class Processes(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        allowed(request.user, 'view_productionprocess')
        return Response([{'id': str(row.pk), **row.data} for row in ProductionProcess.objects.all()])

    def post(self, request):
        allowed(request.user, 'add_productionprocess')
        stage = route([request.data])[0]
        if ProductionProcess.objects.filter(name__iexact=stage['name']).exists():
            fail('This process already exists. Select it from the process list.')
        row = ProductionProcess.objects.create(name=stage['name'], data=stage)
        return Response({**stage, 'id': str(row.pk)}, status=201)


class Models(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        allowed(request.user, 'view_productionmodelrevision')
        return Response(list(ProductionModelRevision.objects.values_list('data', flat=True)))

    @transaction.atomic
    def post(self, request):
        allowed(request.user, 'add_productionmodelrevision')
        data = deepcopy(request.data)
        if not all(str(data.get(key, '')).strip() for key in ('id', 'name', 'defaultUnit')):
            fail('Model ID, name and output unit are required.')
        # Serialize revisions for the same model while preserving all historical snapshots.
        previous = ProductionModelRevision.objects.select_for_update().filter(model_id=data['id']).order_by('-revision').first()
        serializers.CharField(max_length=100).run_validation(data['id'])
        revision = serializers.IntegerField(min_value=0, max_value=1000000).run_validation(data.get('revision', 0))
        if previous and revision != previous.revision:
            fail('A newer model revision exists. Reload models before saving.')
        if data.get('routing'):
            data['routing'] = route(data['routing'])
        data['revision'] = (previous.revision if previous else revision) + 1
        data['savedBy'] = request.user.pk
        data['savedAt'] = timezone.now().isoformat()
        data['stages'] = [row['name'] for row in data.get('routing', [])]
        row = ProductionModelRevision.objects.create(model_id=data['id'], revision=data['revision'], data=data)
        return Response(row.data, status=201)


def validated_order_total(data):
    if not all(str(data.get(key, '')).strip() for key in ('customerName', 'marketingPersonName', 'currency')) or data.get('customerType') not in ('B2B', 'B2C'):
        fail('Complete customer, marketing person, customer type and currency.')
    dated(data.get('date'))
    tax = number(data.get('taxRate'))
    if tax > 100:
        fail('Tax cannot exceed 100%.')
    items = data.get('items', [])
    if not isinstance(items, list) or not items or len(items) > 100 or len({item.get('id') for item in items}) != len(items):
        fail('Add distinct order items.')
    total = Decimal(0)
    money = lambda value: Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    for item in items:
        if not item.get('id') or not item.get('itemName') or not item.get('unit') or item.get('fulfillmentSource') not in ('stock', 'manufacturing'):
            fail('Select each item and its stock or manufacturing fulfillment source.')
        total += money(number(item.get('quantity'), True) * number(item.get('unitPrice')))
        if item['fulfillmentSource'] == 'stock' and not FinishedStockItem.objects.filter(pk=item.get('itemId'), unit=item['unit']).exists():
            fail('Stock items must reference the exact saved finished-goods variant and unit.')
        if item['fulfillmentSource'] == 'manufacturing':
            model = ProductionModelRevision.objects.filter(model_id=item.get('modelId'), revision=item.get('modelRevision')).first()
            if not model:
                fail('Select a saved, configured production model revision.')
            route(model.data.get('routing', [])); requirements(model.data, item)
            if dated(item.get('requiredDate')) < dated(data['date']):
                fail('Enter a manufacturing delivery date on or after the order date.')
    pricing = data.get('commercialPricing') or {}
    discount = money(number(pricing.get('discount', 0)))
    if discount > total or pricing.get('taxMode', 'exclusive') not in ('exclusive', 'inclusive'):
        fail('Check discount and tax treatment.')
    basis = money(total - discount)
    net = basis if pricing.get('taxMode') == 'inclusive' else money(basis * (1 + tax / 100))
    led = sum(number((item.get('ledSnapshot') or {}).get('selectedAmount', 0)) for item in items)
    led_tax = sum(number(item['ledSnapshot'].get('selectedAmount', 0)) * tax / 100 for item in items if (item.get('ledSnapshot') or {}).get('vatOnLED'))
    return float(money(net + money(led) + money(led_tax)))


class Orders(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        allowed(request.user, 'view_workfloworder')
        return Response(list(WorkflowOrder.objects.values_list('data', flat=True)))

    @transaction.atomic
    def post(self, request):
        allowed(request.user, 'add_workfloworder')
        data = deepcopy(request.data)
        key = serializers.UUIDField().run_validation(data.pop('creationKey', None))
        existing = WorkflowOrder.objects.filter(request_id=key).first()
        if existing:
            # Compare original immutable entry rather than server-assigned state.
            if existing.data.get('entryRequest') != data:
                error = APIException('This Sales Order request was already saved with different details.'); error.status_code = 409; raise error
            return Response(existing.data)
        order_total = validated_order_total(data)
        original = deepcopy(data)
        order = WorkflowOrder.objects.create(request_id=key, data={}, created_by=request.user)
        stored = {key: deepcopy(data[key]) for key in ('customerName', 'customerId', 'date', 'marketingPersonName', 'customerType', 'requisitionOrder', 'items', 'currency', 'taxRate', 'commercialPricing') if key in data}
        stored.update(id='wf-order-' + str(order.pk), orderNumber=f'SO-W{order.pk:05d}', status='Draft', jobCardIds=[],
                      orderTotal=order_total, entryRequest=original,
                      createdBy=request.user.pk, persistedWorkflow=True)
        order.data = stored; order.save(update_fields=['data'])
        if data.get('allocateJobCard') and any(line['fulfillmentSource'] == 'manufacturing' for line in data['items']):
            allocate(order, request.user)
        return Response(order.data, status=201)


class Allocate(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        allowed(request.user, 'add_workfloworder')
        order = serializers.PrimaryKeyRelatedField(queryset=WorkflowOrder.objects.select_for_update()).run_validation(pk)
        return Response(allocate(order, request.user).data)


class Jobs(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        allowed(request.user, 'view_workflowjob')
        return Response(list(WorkflowJob.objects.values_list('data', flat=True)))


class JobAction(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, action):
        permission = {'approve': 'approve_workflowjob', 'edit': 'change_workflowjob', 'issue': 'issue_workflowjob'}.get(action)
        if not permission:
            fail('Unknown workflow action.')
        allowed(request.user, permission)
        job = serializers.PrimaryKeyRelatedField(queryset=WorkflowJob.objects.select_for_update()).run_validation(pk)
        if job.source_confirmed_revision_id is not None or job.control_revisions.exists():
            error = APIException('Controlled Job Cards require controlled commands; approval and issue policy are not configured.')
            error.status_code = 409
            raise error
        if request.data.get('expectedVersion') != job.version:
            error = APIException('This Job Card changed in another session. Reload before continuing.'); error.status_code = 409; raise error
        card = deepcopy(job.data)
        item_id = request.data.get('itemId')
        if action == 'approve':
            if any(child.get('approval') or child['status'] != 'PendingApproval' for child in card['manufacturingItems']):
                fail('Only unapproved pending Job Cards can be approved.')
            for child in card['manufacturingItems']:
                route(child['productionStages'])
                child.update(status='StoreIssuePending', department='Store', approval={'approvedBy': request.user.get_full_name() or request.user.username, 'approvedAt': timezone.now().isoformat()})
                child['routingApproved'] = {'revision': child['routingRevision'], 'stages': deepcopy(child['productionStages']), 'materials': deepcopy(child['requiredMaterials']), **child['approval']}
                child['activityHistory'].append(event(request.user, 'Approved', 'Store', 'Approved by Production Manager and forwarded for Store Issue.'))
        else:
            child = next((row for row in card['manufacturingItems'] if row['id'] == item_id), None)
            if not child:
                fail('Select the manufacturing item within this Job Card.')
            if action == 'edit':
                if child.get('approval') or child['status'] != 'PendingApproval':
                    fail('Approved or issued requirements are locked.')
                change = request.data.get('change', {})
                if ('qty' in change and number(change['qty'], True) != number(child['qty'])) or ('unit' in change and change['unit'] != child['unit']):
                    fail('Job quantity and unit must match the Sales Order. Order amendments require separate review.')
                for key in ('specifications', 'productionRequirements', 'requiredDate'):
                    if key in change:
                        child[key] = change[key]
                if dated(child['requiredDate']) < dated(job.order.data['date']):
                    fail('Required date cannot precede the Sales Order.')
                if 'productionStages' in change:
                    child['productionStages'] = route(change['productionStages'])
                if 'requiredMaterials' in change:
                    materials = change['requiredMaterials']
                    if not materials or len({row.get('materialId') for row in materials}) != len(materials) or len({row.get('id') for row in materials}) != len(materials):
                        fail('Use distinct, positive material requirements.')
                    for row in materials:
                        if not row.get('id') or not RawMaterial.objects.filter(pk=row.get('materialId'), unit=row.get('unit')).exists() or number(row.get('issuedQuantity', 0)) != 0:
                            fail('Select exact unissued raw materials and units.')
                        number(row.get('requiredQuantity'), True)
                    child['requiredMaterials'] = deepcopy(materials)
                child['activityHistory'].append(event(request.user, 'PM correction', 'Production Manager', 'Required materials/process steps reviewed and corrected before approval.'))
            else:
                if not child.get('approval') or child['status'] not in ('StoreIssuePending', 'MaterialIssued'):
                    fail('Only approved items awaiting Store Issue can issue materials.')
                quantities = request.data.get('quantities', {})
                if not isinstance(quantities, dict) or set(quantities) - {row['id'] for row in child['requiredMaterials']}:
                    fail('Issue quantities must reference the approved material requirements.')
                did_issue = False
                for row in sorted(child['requiredMaterials'], key=lambda row: row['materialId']):
                    quantity = number(quantities.get(row['id'], 0))
                    if quantity != quantity.quantize(Decimal('0.000001')):
                        fail('Issue quantities support up to six decimal places.')
                    if quantity == 0:
                        continue
                    if quantity > number(row['requiredQuantity']) - number(row['issuedQuantity']):
                        fail('Issue exceeds the remaining approved requirement.')
                    raw = RawMaterial.objects.select_for_update().get(pk=row['materialId'])
                    if raw.unit != row['unit'] or raw.opening_stock is None or timezone.localdate() < raw.opening_date:
                        fail('Confirm the material opening balance, unit and date before issue.')
                    totals = {r['movement_type']: r['total'] for r in raw.movements.values('movement_type').annotate(total=Sum('quantity'))}
                    balance = raw.opening_stock + totals.get('IN', 0) - totals.get('OUT', 0)
                    if quantity > balance:
                        fail('Required issue exceeds current Raw Material stock. Reload and issue only available quantities.')
                    RawMovement.objects.create(id='wf-issue-' + str(uuid4()), material=raw, date=timezone.localdate(), movement_type='OUT', quantity=quantity,
                                               reference=card['jobCardNumber'], remarks=f'Store issue for {child["product"]}', created_by=request.user)
                    row['issuedQuantity'] = float(number(row['issuedQuantity']) + quantity); did_issue = True
                if not did_issue:
                    fail('Enter at least one positive material issue quantity.')
                child.update(status='MaterialIssued', department='Production')
                child['activityHistory'].append(event(request.user, 'Materials issued', 'Store', 'Approved quantities issued and Raw Material OUT recorded atomically.'))
        children = card['manufacturingItems']
        card['status'] = 'PendingApproval' if any(row['status'] == 'PendingApproval' for row in children) else 'MaterialIssued' if any(row['status'] == 'MaterialIssued' for row in children) else 'StoreIssuePending'
        card['department'] = 'Production Manager' if card['status'] == 'PendingApproval' else 'Store' if card['status'] == 'StoreIssuePending' else 'Production'
        card['storeIssueComplete'] = all(all(number(row['issuedQuantity']) >= number(row['requiredQuantity']) for row in child['requiredMaterials']) for child in children)
        job.version += 1; card['workflowVersion'] = job.version; job.data = card
        job.save(update_fields=['data', 'version', 'updated_at'])
        return Response(card)
