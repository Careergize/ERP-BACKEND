"""Server validation for persisted QC, dispatch and invoice records.

All callers run inside the shared-entry version/evidence transaction. Existing
records are immutable except explicitly allowed state transitions.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from .models import SharedEntry, WorkflowOrder, WorkflowJob, FinishedStockItem, PurchaseOrderLine, GoodsReceivedLine
from .operational_policy import authorize
from functools import lru_cache
from pathlib import Path
import json


@lru_cache(maxsize=1)
def quality_reference():
    return json.loads((Path(__file__).parent / 'data' / 'quality_reference.json').read_text())


def require(condition, message):
    if not condition:
        raise serializers.ValidationError(message)


def quantity(value, positive=False):
    try:
        result = Decimal(str(value))
        require(not isinstance(value, bool) and result.is_finite() and result >= 0 and result < Decimal('1e14') and (not positive or result > 0), 'Enter a valid quantity or amount.')
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise serializers.ValidationError('Enter a finite quantity or amount.')


def money(value):
    return Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def dated(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise serializers.ValidationError('Enter a valid ISO date.')


def rows(value):
    require(isinstance(value, list) and all(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'] for row in value), 'Records require stable IDs.')
    result = {row['id']: row for row in value}
    require(len(result) == len(value), 'Record IDs must be unique.')
    return result


def retained(old, new, mutable=()):
    require(set(old) <= set(new), 'Recorded history cannot be removed.')
    for key, previous in old.items():
        require({k: v for k, v in previous.items() if k not in mutable} == {k: v for k, v in new[key].items() if k not in mutable}, 'Recorded values are immutable. Append a new record or use its explicit reversal.')


def shared(key, default):
    entry = SharedEntry.objects.filter(pk=key).first()
    return entry.content if entry else deepcopy(default)


def qc_result(inspection):
    template = inspection['template']; count = inspection['sampleCount']
    require(type(count) is int and 1 <= count <= (8 if template.get('category') == 'production' else 1), 'Invalid QC sample count.')
    answers = inspection.get('responses', [])
    lookup = {(row['itemId'], row['sample']): row for row in answers}
    require(len(lookup) == len(answers), 'Duplicate QC responses.')
    applicable = 0; failed = False
    item_ids = {item['id'] for item in template['items']}
    require(all(row['itemId'] in item_ids and type(row['sample']) is int and 1 <= row['sample'] <= count for row in answers), 'Invalid checklist/sample identity.')
    options = {'yes_no': {'Yes', 'No'}, 'ok_not_ok': {'OK', 'Not OK'}, 'tick_cross': {'Tick', 'Cross'}, 'pass_fail': {'Pass', 'Fail'}}
    for item in template['items']:
        for sample in range(1, count + 1):
            answer = lookup.get((item['id'], sample))
            if not answer or not str(answer.get('value', '')).strip():
                require(not item.get('required'), 'Complete all required QC answers.')
                continue
            value = str(answer['value']); remark = str(answer.get('remarks', '')).strip()
            if value == 'NA':
                require(item.get('allowNA') and remark, 'Not-applicable answers require permission and remarks.')
                continue
            applicable += 1
            if item['type'] == 'numeric':
                actual = quantity(value); target = quantity(answer.get('target'))
                lower = quantity(answer.get('lowerTolerance')); upper = quantity(answer.get('upperTolerance'))
                require(bool(answer.get('unit')), 'Numeric QC requires an explicit unit.')
                passed = target - lower <= actual <= target + upper
            elif item['type'] == 'text':
                passed = bool(value.strip())
            else:
                require(value in options.get(item['type'], set()), 'Invalid QC response option.')
                passed = value == item.get('acceptable')
            require(not (item.get('remarksRequired') or not passed) or bool(remark), 'Record required/non-compliance remarks.')
            failed |= not passed
    require(applicable > 0, 'An all-not-applicable inspection cannot be completed.')
    return 'Fail' if failed else 'Pass'


def validate_quality(content, previous, user):
    previous = previous or {'inspections': [], 'releases': [], 'nonConformances': []}
    old = rows(previous['inspections']); new = rows(content['inspections']); require(set(old) <= set(new), 'Inspections cannot be removed.')
    actor = str(user.pk)
    for key, item in new.items():
        prior = old.get(key)
        if item == prior:
            continue
        require(isinstance(item.get('template'), dict) and isinstance(item.get('subject'), dict), 'An inspection requires its exact template and subject.')
        if not prior or prior['status'] == 'Draft':
            authorize(user, 'qc-record')
            require(user.effective_role == 'admin' or item.get('assignedTo') == actor, 'Only assigned QC staff may change the inspection.')
            require(item['status'] in ('Draft', 'Submitted'), 'New inspections must begin as drafts.')
            if prior:
                require(all(item.get(field) == prior.get(field) for field in ('id', 'template', 'subject', 'date', 'sampleCount')), 'Inspection source and template are immutable.')
                mutable = {'time', 'responses', 'metadata', 'comments', 'events', 'status', 'result', 'assignedTo', 'inspectorId', 'inspector'}
                require(all(item.get(field) == prior.get(field) for field in (set(item) | set(prior)) - mutable), 'Only draft measurements, comments and authorized assignments may change.')
                require(item.get('assignedTo') == prior.get('assignedTo') or user.effective_role == 'admin', 'Only Admin can change inspection assignment.')
            else:
                templates = shared('quality-templates', quality_reference()['templates'])
                template = next((t for t in templates if t['id'] == item['template'].get('id') and t['revision'] == item['template'].get('revision')), None)
                require(template and template == item['template'] and template.get('active') and item['subject'].get('kind') == template['category'], 'Use the exact saved active QC template revision.')
                require(not any(t['id'] == template['id'] and t['revision'] > template['revision'] for t in templates), 'Use the latest QC template revision.')
                require(dated(item['date']) >= dated(template['effectiveDate']), 'Inspection cannot predate the template.')
                subject = item['subject']
                if subject['kind'] == 'raw':
                    from inventory.models import RawMovement
                    receipt = RawMovement.objects.select_related('material').filter(pk=subject.get('receiptId'), movement_type='IN').first()
                    require(receipt and str(receipt.material_id) == subject.get('materialId') and receipt.material.unit == subject.get('unit') and receipt.quantity == quantity(subject.get('quantity'), True), 'Raw QC requires an exact saved inward receipt, quantity and unit.')
                elif subject['kind'] == 'production':
                    parent = WorkflowJob.objects.filter(data__id=subject.get('jobCardId')).first()
                    child = next((c for c in parent.data['manufacturingItems'] if c['id'] == subject.get('manufacturingItemId')), None) if parent else None
                    batch = next((b for b in child.get('productionBatches', []) if b['id'] == subject.get('batchId')), None) if child else None
                    require(batch and batch['stageId'] == subject.get('stageId') and batch['number'] == subject.get('batchNumber') and batch['unit'] == subject.get('unit') and quantity(batch['quantity']) == quantity(subject.get('quantity')), 'Production QC requires the exact saved Job Card item, stage and batch.')
                    final = sorted([s for s in child['productionStages'] if s.get('required') and s.get('kind') == 'production'], key=lambda s: s['sequence'])[-1]
                    require(bool(subject.get('isFinishedOutput')) == (final['id'] == batch['stageId']), 'Finished-output QC must identify the final required production stage.')
                elif subject['kind'] == 'machine':
                    from .models import ProductionMachine
                    machine = ProductionMachine.objects.filter(pk=subject.get('machineId')).first()
                    require(machine and machine.data.get('active', True) and machine.data.get('type') == subject.get('machineType') == template.get('applicable'), 'Machine QC requires an active machine of the exact template type.')
                elif subject['kind'] == 'cleaning':
                    require(any(area['id'] == subject.get('areaId') for area in shared('quality-areas', quality_reference()['areas'])), 'Select a saved cleaning area.')
            require(not item.get('approvals'), 'A draft cannot contain approval decisions.')
            if item['status'] == 'Submitted':
                require(dated(item['date']) <= timezone.localdate() and bool(item.get('time')), 'Complete inspection date and time.')
                datetime.strptime(item['time'], '%H:%M')
                if item['subject']['kind'] == 'cleaning':
                    require(all(item.get('metadata', {}).get(field) for field in ('cleaningMethod', 'chemicals', 'cleanedBy')), 'Complete cleaning method, chemicals and responsible staff.')
                if item['subject']['kind'] == 'raw':
                    metadata = item.get('metadata', {})
                    require(all(metadata.get(field) for field in ('sampledQuantity', 'coa', 'tds', 'msds')) and item['subject'].get('batchNumber') and quantity(metadata.get('sampledQuantity'), True) <= quantity(item['subject']['quantity']), 'Complete raw-receipt sampling and document availability.')
                require(item.get('result') == qc_result(item), 'QC result does not match the saved measurements.')
                require(str(item.get('inspectorId')) == actor, 'Submission must identify the authenticated inspector.')
        elif prior['status'] == 'Submitted':
            authorize(user, 'qc-review')
            require(str(prior['inspectorId']) != actor, 'A different authorized reviewer must review QC.')
            require(all(item.get(field) == prior.get(field) for field in ('template', 'subject', 'responses', 'metadata', 'comments', 'date', 'time', 'sampleCount', 'inspectorId')), 'Submitted measurements are locked.')
            require(item['status'] in ('Passed', 'Failed', 'Reinspection Required', 'Conditionally Accepted'), 'Invalid QC review decision.')
            require(item['status'] != 'Passed' or qc_result(prior) == 'Pass', 'Failing measurements cannot pass review.')
            require(item.get('result') == qc_result(prior), 'Reviewed QC result must match the submitted measurements.')
            require(item['status'] != 'Conditionally Accepted' or item['subject']['kind'] == 'raw', 'Conditional acceptance applies only to raw receipts.')
            decisions = item.get('approvals', [])
            require(decisions[:-1] == prior.get('approvals', []) and len(decisions) == len(prior.get('approvals', [])) + 1, 'Append exactly one review decision.')
            require(str(decisions[-1].get('userId')) == actor and decisions[-1].get('remarks') and decisions[-1].get('decision') == item['status'], 'Review must identify the authenticated reviewer and remarks.')
        else:
            require(item['status'] == 'Approved/Released', 'Reviewed inspections are immutable apart from explicit release.')
            require(all(item.get(field) == prior.get(field) for field in ('template', 'subject', 'responses', 'metadata', 'comments', 'approvals', 'inspectorId')), 'Reviewed measurements cannot be edited.')
    old_releases = rows(previous['releases']); releases = rows(content['releases']); retained(old_releases, releases)
    for key in releases.keys() - old_releases.keys():
        release = releases[key]; inspection = new.get(release.get('inspectionId'))
        require(inspection and inspection['status'] == 'Approved/Released', 'Release requires an exact reviewed inspection.')
        prior = old.get(inspection['id']); require(prior and prior['status'] in ('Passed', 'Approved/Released'), 'Independently passing QC must precede release.')
        require(release.get('subject') == inspection['subject'] and str(release.get('userId')) == actor, 'Release subject/actor must match the saved inspection and authenticated user.')
        purpose = release.get('purpose'); authorize(user, 'qc-review' if purpose == 'quality' else 'stock-authorize')
        require(purpose in ('quality', 'finished_stock'), 'Invalid release purpose.')
        require(not any(row['inspectionId'] == inspection['id'] and row['purpose'] == purpose for row in old_releases.values()), 'Duplicate QC release.')
        require(not any(f.get('status') != 'Closed' and f.get('inspectionId') == inspection['id'] for f in content['nonConformances']), 'Close findings before release.')
        if purpose == 'finished_stock':
            require(inspection['subject'].get('isFinishedOutput') is True and any(row['inspectionId'] == inspection['id'] and row['purpose'] == 'quality' for row in old_releases.values()), 'Quality release must precede finished-stock authorization.')
            accepted = quantity(release.get('acceptedQuantity')); rejected = quantity(release.get('rejectedQuantity'))
            require(0 < accepted + rejected <= quantity(inspection['subject']['quantity']), 'Authorized quantities exceed inspected output.')
    old_findings = rows(previous['nonConformances']); findings = rows(content['nonConformances']); require(set(old_findings) <= set(findings), 'Findings cannot be removed.')
    for key, finding in findings.items():
        prior = old_findings.get(key)
        if prior == finding:
            continue
        authorize(user, 'qc-record' if prior is None else 'qc-review')
        require(finding.get('inspectionId') in new and finding.get('description'), 'Findings require an exact inspection and description.')
        require(not prior or prior.get('status') != 'Closed', 'Closed findings are immutable.')
        if finding.get('status') == 'Closed':
            require(finding.get('verification') and finding.get('correctiveAction'), 'Closing a finding requires corrective action and verification.')


def external_movements():
    result = []
    for line in PurchaseOrderLine.objects.filter(stock_posted=True).select_related('order'):
        result.append({'id': f'purchase-line-{line.pk}', 'productId': line.stock_item_id, 'date': line.order.order_date.isoformat(), 'type': 'IN', 'quantity': float(line.quantity), 'unit': line.unit, 'batch': 'reference-stock'})
    for line in GoodsReceivedLine.objects.filter(quantity_received__gt=0).select_related('note', 'purchase_line'):
        amount = line.quantity_received - line.damaged_quantity
        if amount > 0:
            result.append({'id': f'grn-line-{line.pk}', 'productId': line.purchase_line.stock_item_id, 'date': line.note.receipt_date.isoformat(), 'type': 'IN', 'quantity': float(amount), 'unit': line.purchase_line.unit, 'batch': 'reference-stock'})
    return result


def order_line(order_id, item_id):
    try:
        order = WorkflowOrder.objects.get(pk=int(str(order_id).removeprefix('wf-order-'))).data
        line = next(row for row in order['items'] if row['id'] == item_id)
    except (ValueError, WorkflowOrder.DoesNotExist, StopIteration):
        raise serializers.ValidationError('Select an exact saved Sales Order item.')
    require(order.get('salesOrderApproval') and order.get('status') not in ('Cancelled', 'Completed'), 'Dispatch requires an approved open Sales Order.')
    return order, line


def validate_ledger(content, previous, user):
    authorize(user, 'dispatch' if content.get('dispatches') != (previous or {}).get('dispatches', []) or content.get('allocations') != (previous or {}).get('allocations', []) else 'stock-receipt')
    require(set(content) == {'movements', 'allocations', 'dispatches'}, 'Invalid finished-goods ledger.')
    previous = previous or {'movements': [], 'allocations': [], 'dispatches': []}
    old_m = rows(previous['movements']); movements = rows(content['movements']); retained(old_m, movements)
    old_a = rows(previous['allocations']); allocations = rows(content['allocations']); retained(old_a, allocations, ('dispatched', 'released'))
    old_d = rows(previous['dispatches']); dispatches = rows(content['dispatches']); retained(old_d, dispatches, ('status',))
    qc = shared('quality-state', {'inspections': [], 'releases': [], 'nonConformances': []})
    invoices = shared('invoices', [])
    products = {str(row.pk): row for row in FinishedStockItem.objects.select_for_update().order_by('pk')}
    additions = movements.keys() - old_m.keys()
    for key in additions:
        row = movements[key]; product = products.get(str(row.get('productId')))
        require(product and row.get('unit') == product.unit and row.get('type') in ('IN', 'OUT'), 'Use an exact finished product, unit and direction.')
        quantity(row.get('quantity'), True); require(product.opening_date <= dated(row.get('date')) <= timezone.localdate(), 'Movement date must follow opening and cannot be future.')
        require(not row.get('purchaseOrderId') and not row.get('goodsReceivedNoteId') and not key.startswith(('purchase-line-', 'grn-line-')), 'Purchase/GRN receipts are written only by their source services.')
        if row.get('dispatchId'):
            dispatch = dispatches.get(row['dispatchId']); require(dispatch is not None, 'A sales movement requires its matching dispatch.')
            require(all(row.get(field) == dispatch.get(field) for field in ('productId', 'quantity', 'unit', 'batch')), 'Sales movement must match exact dispatch quantities and units.')
            require(row.get('salesOrderId') == dispatch['orderId'] and row.get('salesOrderItemId') == dispatch['itemId'], 'Sales movement must match its exact order line.')
            if row['type'] == 'IN':
                require(row.get('reversalOf') == f"out-{dispatch['id']}" and dispatch['status'] == 'Cancelled' and row.get('remarks'), 'Use an explicit reasoned dispatch reversal.')
        elif row.get('jobCardId') or row.get('manufacturingItemId'):
            authorize(user, 'stock-receipt')
            release = next((r for r in qc['releases'] if r['id'] == row.get('stockApprovalId') and r['purpose'] == 'finished_stock'), None)
            require(release and release['subject'].get('manufacturingItemId') == row.get('manufacturingItemId') and release['subject'].get('batchNumber') == row.get('batch'), 'Manufactured Stock IN requires its exact final QC authorization.')
            require(row['type'] == 'IN' and quantity(row['quantity']) <= quantity(release['acceptedQuantity']), 'Receipt exceeds the authorized accepted quantity.')
            require(sum((quantity(m['quantity']) for m in movements.values() if m.get('stockApprovalId') == row['stockApprovalId']), Decimal(0)) <= quantity(release['acceptedQuantity']), 'Receipts exceed the total accepted quantity of this authorized lot.')
            require(row['unit'] == release.get('unit', release['subject'].get('unit')) and any(r['id'] == row.get('qualityReleaseId') and r['inspectionId'] == release['inspectionId'] and r['purpose'] == 'quality' for r in qc['releases']), 'Receipt requires the exact quality-release reference and output unit.')
        else:
            require(user.effective_role in ('admin', 'store_keeper') and row.get('reference'), 'Store/Admin and an explicit reference are required for manual stock movements.')
            if row['type'] == 'IN':
                require(user.effective_role == 'admin', 'Only Admin records unbatched opening/reference receipts.')
                require(not any(i['subject'].get('productId') == str(product.pk) and i['status'] != 'Approved/Released' for i in qc['inspections']), 'A QC hold prevents manual receipt for this product.')
            else:
                require(row.get('movementPurpose') in ('transfer', 'waste', 'adjustment') and row.get('remarks'), 'Non-sales OUT requires purpose and reason.')
                require(row['movementPurpose'] != 'adjustment' or user.effective_role == 'admin', 'Admin must approve adjustments.')
    for key, dispatch in dispatches.items():
        prior = old_d.get(key)
        if dispatch == prior:
            continue
        authorize(user, 'dispatch')
        if prior:
            require(prior['status'] == 'Confirmed' and dispatch['status'] == 'Cancelled', 'Only confirmed dispatches may be reversed.')
            require(not any(key in invoice.get('dispatchIds', []) for invoice in invoices), 'An invoiced dispatch requires a credit-note workflow before reversal.')
            require(any(m.get('dispatchId') == key and m.get('reversalOf') == f'out-{key}' for m in movements.values()), 'A reversal must restore stock atomically.')
            continue
        order, line = order_line(dispatch.get('orderId'), dispatch.get('itemId'))
        require(dispatch.get('status') == 'Confirmed' and dispatch.get('productId') == line.get('itemId') and dispatch.get('unit') == line['unit'], 'Dispatch product/unit must match the saved Sales Order.')
        require(dated(dispatch['date']) >= dated(order['date']) and dispatch.get('destination') and dispatch.get('challan'), 'Dispatch requires a valid date, destination and delivery note.')
        require(f'out-{key}' in movements and movements[f'out-{key}'].get('date') == dispatch['date'], 'Dispatch and stock OUT must be saved atomically.')
        total = sum((quantity(d['quantity']) for d in dispatches.values() if d['orderId'] == order['id'] and d['itemId'] == line['id'] and d['status'] == 'Confirmed'), Decimal(0))
        require(total <= quantity(line['quantity']), 'Dispatch exceeds the Sales Order item quantity.')
        held = [i for i in qc['inspections'] if i['subject'].get('productId') == dispatch['productId'] and i['subject'].get('batchNumber') == dispatch['batch'] and i['status'] not in ('Approved/Released',)]
        require(not held, 'An unresolved QC inspection holds this product/batch.')
    for allocation in allocations.values():
        _, line = order_line(allocation['orderId'], allocation['itemId'])
        require(allocation['productId'] == line.get('itemId'), 'Allocation must reference the saved product variant.')
        amount = quantity(allocation['quantity'], True); dispatched = sum((quantity(d['quantity']) for d in dispatches.values() if d.get('allocationId') == allocation['id'] and d['status'] == 'Confirmed'), Decimal(0))
        require(quantity(allocation['dispatched']) == dispatched and dispatched <= amount, 'Allocation totals must reconcile to confirmed dispatches.')
        committed = sum((quantity(a['dispatched'] if a.get('released') else a['quantity']) for a in allocations.values() if a['orderId'] == allocation['orderId'] and a['itemId'] == line['id']), Decimal(0))
        require(committed <= quantity(line['quantity']), 'Allocations exceed the order quantity.')
    combined = list(movements.values()) + external_movements()
    for product_id in {str(m['productId']) for m in movements.values()} | {str(a['productId']) for a in allocations.values()}:
        product = products.get(product_id); require(product is not None, 'Unknown finished product.')
        selected = [m for m in combined if str(m['productId']) == product_id]
        if product.opening_stock is None:
            require(not any(m['type'] == 'OUT' for m in selected), 'Confirm the unknown opening balance before Stock OUT.')
            continue
        dates = sorted({dated(m['date']) for m in selected})
        balance = product.opening_stock
        for day in dates:
            balance = product.opening_stock + sum((quantity(m['quantity']) * (1 if m['type'] == 'IN' else -1) for m in selected if dated(m['date']) <= day), Decimal(0))
            require(balance >= 0, 'Movement would make current or historical stock negative.')
            for batch in {m.get('batch', 'reference-stock') for m in selected}:
                batch_balance = (product.opening_stock if batch == 'reference-stock' else Decimal(0)) + sum((quantity(m['quantity']) * (1 if m['type'] == 'IN' else -1) for m in selected if m.get('batch', 'reference-stock') == batch and dated(m['date']) <= day), Decimal(0))
                require(batch_balance >= 0, 'Movement exceeds physical stock in this exact batch.')
        held = sum((quantity(a['quantity']) - quantity(a['dispatched']) for a in allocations.values() if str(a['productId']) == product_id and not a.get('released')), Decimal(0))
        require(held <= balance, 'Reservations exceed known physical stock.')
        for batch in {a['batch'] for a in allocations.values() if str(a['productId']) == product_id}:
            available = (product.opening_stock if batch == 'reference-stock' else Decimal(0)) + sum((quantity(m['quantity']) * (1 if m['type'] == 'IN' else -1) for m in selected if m.get('batch', 'reference-stock') == batch), Decimal(0))
            reserved = sum((quantity(a['quantity']) - quantity(a['dispatched']) for a in allocations.values() if str(a['productId']) == product_id and a['batch'] == batch and not a.get('released')), Decimal(0))
            require(reserved <= available, 'Reservations exceed physical stock in the selected batch.')


def commercial_totals(lines, discount, rate, mode):
    subtotal = money(sum((money(quantity(row['quantity']) * quantity(row['unitPrice'])) for row in lines), Decimal(0)))
    discount = money(discount); require(discount <= subtotal and 0 <= rate <= 100, 'Invalid discount/tax.')
    basis = subtotal - discount; tax = money(basis - money(basis / (1 + rate / 100))) if mode == 'inclusive' else money(basis * rate / 100)
    led = money(sum((quantity(row.get('ledSnapshot', {}).get('selectedAmount', 0)) for row in lines), Decimal(0)))
    led_tax = money(sum((quantity(row.get('ledSnapshot', {}).get('selectedAmount', 0)) * rate / 100 for row in lines if row.get('ledSnapshot', {}).get('vatOnLED')), Decimal(0)))
    return subtotal, tax + led_tax, money(basis + (0 if mode == 'inclusive' else tax) + led + led_tax), led


def validate_invoices(content, previous, user):
    authorize(user, 'invoice')
    old = rows(previous or []); new = rows(content); retained(old, new)
    ledger = shared('inventory-ledger', {'dispatches': []}); dispatches = rows(ledger['dispatches'])
    numbers = [row.get('number') for row in content]; require(all(numbers) and len(set(numbers)) == len(numbers), 'Invoice numbers must be unique.')
    for key in new.keys() - old.keys():
        invoice = new[key]; order, _ = order_line(invoice['orderId'], invoice.get('items', [{}])[0].get('itemId'))
        ids = invoice.get('dispatchIds', []); require(ids and len(set(ids)) == len(ids), 'Select distinct confirmed dispatches.')
        require(not any(set(ids) & set(row.get('dispatchIds', [])) for row in old.values()), 'A dispatch cannot be invoiced twice.')
        require(all(identity in dispatches and dispatches[identity]['status'] == 'Confirmed' and dispatches[identity]['orderId'] == order['id'] and dated(dispatches[identity]['date']) <= dated(invoice['date']) for identity in ids), 'Invoice only confirmed, uninvoiced dispatches from this order and date.')
        require(invoice.get('currency') == order['currency'] and invoice.get('seller') and invoice.get('billingAddress'), 'Invoice requires the order currency, seller and billing address.')
        expected = []
        for line in order['items']:
            amount = sum((quantity(dispatches[d]['quantity']) for d in ids if dispatches[d]['itemId'] == line['id']), Decimal(0))
            if amount:
                expected.append({**line, 'quantity': float(amount)})
        require(len(invoice.get('items', [])) == len(expected), 'Invoice lines must match the exact selected dispatch quantities.')
        for item in invoice['items']:
            source = next((row for row in expected if row['id'] == item.get('itemId')), None)
            require(source and quantity(item['quantity']) == quantity(source['quantity']) and item['unit'] == source['unit'] and quantity(item['rate']) == quantity(source['unitPrice']), 'Invoice quantities, units and selling prices must match the saved order and dispatches.')
            require(item.get('description') == source['itemName'] and quantity(item.get('amount')) == money(quantity(source['quantity']) * quantity(source['unitPrice'])), 'Invoice descriptions and line amounts must match the order.')
        rate = quantity(order.get('taxRate', 0)); pricing = order.get('commercialPricing')
        require(quantity(invoice['taxRate']) == rate, 'Invoice tax rate must match the order.')
        discount = quantity(invoice.get('discount', 0)); mode = pricing['taxMode'] if pricing else 'exclusive'
        if pricing:
            full_subtotal, full_tax, full_total, _ = commercial_totals(order['items'], quantity(pricing['discount']), rate, mode)
            partial_subtotal = sum((money(quantity(row['quantity']) * quantity(row['unitPrice'])) for row in expected), Decimal(0))
            prior = [row for row in old.values() if row['orderId'] == order['id']]
            remaining_discount = quantity(pricing['discount']) - sum((quantity(row.get('quotationDiscount', 0)) for row in prior), Decimal(0))
            final = all(sum((quantity(i['quantity']) for row in prior for i in row['items'] if i['itemId'] == line['id']), Decimal(0)) + sum((quantity(i['quantity']) for i in expected if i['id'] == line['id']), Decimal(0)) == quantity(line['quantity']) for line in order['items'])
            required_discount = money(remaining_discount if final else min(money(quantity(pricing['discount']) * partial_subtotal / full_subtotal) if full_subtotal else Decimal(0), remaining_discount))
            require(discount == required_discount, 'Accepted quotation discount must be apportioned consistently.')
        # Use server-saved line snapshots, scaling LED by the dispatched fraction.
        for row in expected:
            source = next(line for line in order['items'] if line['id'] == row['id'])
            if source.get('ledSnapshot'):
                row['ledSnapshot'] = {**source['ledSnapshot'], 'selectedAmount': float(money(quantity(source['ledSnapshot']['selectedAmount']) * quantity(row['quantity']) / quantity(source['quantity'])))}
        subtotal, tax, total, _ = commercial_totals(expected, discount, rate, mode)
        if pricing and final:
            tax = money(full_tax - sum((quantity(row['tax']) for row in prior), Decimal(0)))
            total = money(full_total - sum((quantity(row['total']) for row in prior), Decimal(0)))
        require(quantity(invoice['subtotal']) == subtotal and quantity(invoice['tax']) == tax and quantity(invoice['total']) == total, 'Invoice amounts do not match server calculations.')


def validate_costing(content, previous, user):
    from .job_costing import convert
    old = rows(previous or []); new = rows(content); require(set(old) <= set(new), 'Costing revisions cannot be removed.')
    for key, estimate in new.items():
        prior = old.get(key)
        if prior == estimate:
            continue
        if prior and (prior.get('approvedAt') or prior.get('generatedAt')):
            require(estimate['input'] == prior['input'] and estimate['totals'] == prior['totals'], 'Approved/generated costing is immutable. Create a revision.')
        if estimate.get('status') == 'Approved' and (not prior or prior.get('status') != 'Approved'):
            authorize(user, 'costing-approve')
            require(user.effective_role in ('admin', 'accounts') and prior and prior['status'] == 'Pending Review', 'Accounts/Admin approves a saved review request.')
            require(estimate['input'] == prior['input'] and estimate['totals'] == prior['totals'], 'Approval must preserve the exact reviewed financial snapshot.')
            require(estimate.get('approvedBy') == (user.get_full_name() or user.username), 'Approval must identify the authenticated reviewer.')
        require(estimate.get('status') in ('Draft', 'Pending Review', 'Approved', 'Revision Required', 'Superseded'), 'Invalid costing status.')
        i = estimate['input']; q = quantity(i['quantity'], True)
        require(i.get('product') and i.get('unit') and estimate.get('number') and estimate.get('createdAt'), 'Costing requires product, unit, reference and creation timestamp.')
        require(i['currency'] in ('USD', 'UGX'), 'Unsupported costing currency.')
        materials = i['materials']; require(materials and len({m['materialId'] for m in materials}) == len(materials), 'Select distinct recipe materials.')
        if i['recipeMode'] == 'percentages':
            require(abs(sum((quantity(m['percentage'], True) for m in materials), Decimal(0)) - 100) < Decimal('.000001'), 'Mixing percentages must total 100.')
        totals = {'materialCost': Decimal(0), 'inkCost': Decimal(0)}
        for material in materials:
            amount = convert(quantity(i['batchQuantity'], True) * quantity(material['percentage'], True) / 100, i['batchUnit'], material['unit']) if i['recipeMode'] == 'percentages' else quantity(material['quantity'], True)
            fx = Decimal(1) if material['currency'] == i['currency'] else quantity(material.get('exchangeRate'), True)
            cost = money(convert(amount, material['unit'], material['priceUnit']) * quantity(material['price']) * fx)
            require(material.get('costGroup') in ('material', 'ink'), 'Invalid material cost group.')
            totals['inkCost' if material['costGroup'] == 'ink' else 'materialCost'] += cost
        process = Decimal(0)
        for row in i['processes']:
            require(row['basis'] in ('fixed', 'per kg', 'per piece', 'per batch'), 'Invalid process cost basis.')
            require(row['basis'] != 'per kg' or row['unit'] == 'kg', 'Per KG process units must be kg.')
            require(row['basis'] != 'per piece' or row['unit'] == 'pcs', 'Per-piece process units must be pcs.')
            process += money(quantity(row['rate']) * (1 if row['basis'] == 'fixed' else quantity(row['quantity'])))
        totals['processCost'] = money(process)
        waste = quantity(i['wastePercent']); markup = quantity(i['markupPercent']); tax_rate = quantity(i['taxPercent']); charge_rate = quantity(i['chargePercent'])
        require(waste <= 100 and tax_rate <= 100 and charge_rate <= 100 and i['pricingMethod'] in ('markup', 'gross-margin'), 'Invalid costing percentages/method.')
        base = totals['materialCost'] + totals['inkCost']
        totals['wasteCost'] = money((base + (process if i['wasteBasis'] == 'materials-and-processes' else 0)) * waste / 100)
        cop = money(base + process + totals['wasteCost'] + quantity(i['overheads']) + quantity(i['additionalCosts'])); totals['costOfProduction'] = cop
        require(i['pricingMethod'] != 'gross-margin' or markup < 100, 'Gross margin must be below 100%.')
        selling = money(cop / (1 - markup / 100) if i['pricingMethod'] == 'gross-margin' else cop * (1 + markup / 100))
        totals.update(sellingBeforeTax=selling, profit=money(selling - cop), charges=money(max(quantity(i['minimumCharge']), selling * charge_rate / 100)))
        led = Decimal(0); config = i.get('led') or {}
        if config.get('applies'):
            require(i['currency'] == 'UGX' and config.get('percentageBase') in ('net-selling', 'production-cost'), 'LED requires UGX and an explicit percentage base.')
            dated(config.get('fxDate')); led = money(max((cop if config['percentageBase'] == 'production-cost' else selling) * Decimal('.025'), quantity(config['eligibleWeightKG']) / 1000 * 70 * quantity(config['usdToUGX'], True)))
            require(quantity(estimate['totals'].get('led', {}).get('selectedAmount')) == led, 'LED comparison differs from the server calculation.')
        totals['tax'] = money((selling + (totals['charges'] if i.get('taxOnCharges') else 0) + (led if config.get('vatOnLED') else 0)) * tax_rate / 100)
        totals['grandTotal'] = money(selling + totals['charges'] + led + totals['tax']); totals['unitSellingPrice'] = money(totals['grandTotal'] / q)
        require(all(quantity(estimate['totals'].get(field)) == value for field, value in totals.items()), 'Costing totals differ from the server calculation.')


def validate_tracking(content, previous, user):
    previous = previous or {}
    require(set(previous) <= set(content), 'Job tracking history cannot be removed.')
    for parent_id, proposed in content.items():
        if proposed == previous.get(parent_id):
            continue
        try:
            job = WorkflowJob.objects.select_for_update().get(pk=proposed['card']['workflowKey'])
        except (ValueError, WorkflowJob.DoesNotExist):
            raise serializers.ValidationError('Select a persisted parent Job Card.')
        require(job.data['id'] == parent_id and proposed.get('expectedVersion') == job.version, 'This Job Card changed; reload before recording production.')
        require(job.source_confirmed_revision_id is None and not job.control_revisions.exists(), 'Controlled Job Cards require their controlled command service.')
        current = deepcopy(job.data); incoming = deepcopy(proposed['card']); children = rows(current['manufacturingItems']); changed = rows(incoming['manufacturingItems'])
        require(set(children) == set(changed), 'Tracking cannot add/remove manufacturing items.')
        qc = shared('quality-state', {'inspections': [], 'releases': [], 'nonConformances': []})
        for identity, child in changed.items():
            before = children[identity]
            if child == before:
                continue
            mutable = {'productionStages', 'productionRuns', 'productionBatches', 'status', 'department', 'activityHistory', 'stages', 'stockTransfers', 'stockTransfer', 'acceptedQuantity', 'rejectedQuantity', 'stockReceiptPending', 'finishedProductId'}
            require(all(child.get(field) == before.get(field) for field in (set(child) | set(before)) - mutable), 'Tracking cannot change approved job scope, costing or authorization.')
            stages = rows(before.get('productionStages', [])); after_stages = rows(child.get('productionStages', [])); require(set(stages) == set(after_stages), 'Tracking cannot replace routing steps.')
            if child.get('stages') != before.get('stages'):
                require(user.effective_role in ('admin', 'production_manager', 'receptionist') and not before.get('approval') and before['status'] in ('Draft', 'PendingApproval'), 'Only authorized draft editors can change stage requirements.')
            batches = rows(child.get('productionBatches', [])); old_batches = rows(before.get('productionBatches', [])); retained(old_batches, batches)
            for batch_id in batches.keys() - old_batches.keys():
                authorize(user, 'qc-record'); batch = batches[batch_id]; stage = stages.get(batch['stageId'])
                require(stage and stage['status'] == 'Completed' and batch['unit'] == stage['outputUnit'] and batch.get('number'), 'QC lots require a completed stage and matching output unit.')
                require(sum((quantity(b['quantity'], True) for b in batches.values() if b['stageId'] == stage['id']), Decimal(0)) <= quantity(stage['outputQuantity']), 'QC lot quantities exceed stage output.')
            runs = rows(child.get('productionRuns', [])); old_runs = rows(before.get('productionRuns', [])); retained(old_runs, runs)
            for run_id in runs.keys() - old_runs.keys():
                authorize(user, 'production'); run = runs[run_id]; stage = stages.get(run['stageId'])
                require(stage and all(run[field] == stage[field] for field in ('inputUnit', 'outputUnit', 'wasteUnit')), 'Run units must match the approved stage.')
                require(run.get('number') and not any(r['number'] == run['number'] for r in old_runs.values()), 'Run references must be distinct.')
                started = datetime.fromisoformat(run['startedAt'].replace('Z', '+00:00')); ended = datetime.fromisoformat(run['completedAt'].replace('Z', '+00:00'))
                require(started <= ended <= timezone.now(), 'Run timestamps must be chronological and not future.')
                require(not any(datetime.fromisoformat(r['completedAt'].replace('Z', '+00:00')) > started for r in old_runs.values() if r['stageId'] == stage['id']), 'Stage runs cannot overlap.')
                quantity(run['inputQuantity'], True); quantity(run['outputQuantity']); quantity(run['wasteQuantity'])
                if run['inputUnit'] == run['outputUnit']:
                    require(quantity(run['outputQuantity']) <= quantity(run['inputQuantity']) and (run['wasteUnit'] != run['inputUnit'] or quantity(run['outputQuantity']) + quantity(run['wasteQuantity']) <= quantity(run['inputQuantity'])), 'Run output/waste exceeds its own input.')
            for stage_id, stage in after_stages.items():
                prior = stages[stage_id]
                if stage == prior:
                    continue
                if stage.get('kind') == 'stock':
                    authorize(user, 'stock-receipt'); continue
                authorize(user, 'production')
                require(before.get('approval') and before['status'] in ('MaterialIssued', 'Issued', 'InProduction', 'OnHold'), 'Approve and issue materials before production.')
                require(prior['status'] != 'Completed' and stage.get('required') and stage.get('kind') == 'production', 'Only an incomplete required production stage can change.')
                fields = {'status', 'inputQuantity', 'outputQuantity', 'wasteQuantity', 'operator', 'startedAt', 'completedAt', 'remarks', 'sentToRecycling', 'handover', 'wasteType', 'wasteReason'}
                require(all(stage.get(field) == prior.get(field) for field in (set(stage) | set(prior)) - fields), 'Actual production cannot change approved routing or units.')
                predecessors = [s for s in after_stages.values() if s.get('required') and s.get('kind') == 'production' and s['sequence'] < stage['sequence']]
                require(all(s['status'] == 'Completed' for s in predecessors), 'Complete all predecessor stages before production.')
                require(not any(i['subject'].get('machineId') == stage.get('machineId') and stage.get('machineId') and i['status'] != 'Approved/Released' for i in qc['inspections']), 'Machine QC hold prevents production.')
                require(stage.get('status') in ('Pending', 'Received', 'InProgress', 'OnHold', 'Completed'), 'Invalid production stage status.')
                inp = quantity(stage.get('inputQuantity') or 0); out = quantity(stage.get('outputQuantity') or 0); waste = quantity(stage.get('wasteQuantity') or 0)
                require(quantity(stage.get('sentToRecycling') or 0) <= waste, 'Recycling cannot exceed recorded stage waste.')
                if stage['status'] in ('InProgress', 'OnHold', 'Completed'):
                    require(stage.get('operator') and stage.get('startedAt'), 'Record the responsible operator and start time.')
                    start = datetime.fromisoformat(stage['startedAt'].replace('Z', '+00:00')); require(start <= timezone.now(), 'Production cannot start in the future.')
                    if stage.get('completedAt'):
                        end = datetime.fromisoformat(stage['completedAt'].replace('Z', '+00:00')); require(stage['status'] == 'Completed' and start <= end <= timezone.now(), 'Production completion must be chronological and not future.')
                if stage['inputUnit'] == stage['outputUnit']:
                    require(out <= inp and (stage['wasteUnit'] != stage['inputUnit'] or out + waste <= inp), 'Production output/waste exceeds input.')
                if stage.get('status') == 'Completed':
                    require(stage.get('startedAt') and stage.get('completedAt'), 'Completed stages require timestamps.')
                if prior.get('handover') and prior['handover']['unit'] == stage['inputUnit']:
                    require(inp == quantity(prior['handover']['quantity']), 'Stage input must match the predecessor handover.')
                stage_runs = [r for r in runs.values() if r['stageId'] == stage_id]
                if stage_runs:
                    require(out == sum((quantity(r['outputQuantity']) for r in stage_runs), Decimal(0)) and waste == sum((quantity(r['wasteQuantity']) for r in stage_runs), Decimal(0)), 'Stage output/waste must equal run aggregates.')
                    run_input = sum((quantity(r['inputQuantity']) for r in stage_runs), Decimal(0))
                    require(run_input <= inp and (stage['status'] != 'Completed' or run_input == inp), 'Account for the exact handed-over input before completing a run-based stage.')
            transfers = rows(child.get('stockTransfers', [])); old_transfers = rows(before.get('stockTransfers', [])); retained(old_transfers, transfers)
            if transfers != old_transfers or child.get('finishedProductId') != before.get('finishedProductId'):
                authorize(user, 'stock-receipt')
                require(all(s['status'] == 'Completed' for s in stages.values() if s.get('required') and s.get('kind') == 'production'), 'Complete production before a stock transfer.')
                for transfer_id in transfers.keys() - old_transfers.keys():
                    transfer = transfers[transfer_id]
                    release = next((r for r in qc['releases'] if r['purpose'] == 'finished_stock' and r['subject'].get('manufacturingItemId') == identity and r['subject'].get('batchNumber') == transfer['batch']), None)
                    require(release and quantity(transfer['quantity']) <= quantity(release['acceptedQuantity']) and quantity(transfer['rejectedQuantity']) <= quantity(release['rejectedQuantity']), 'Stock transfer requires exact final QC authorization and bounded quantities.')
                    require(sum((quantity(t['quantity']) for t in transfers.values() if t['batch'] == transfer['batch']), Decimal(0)) <= quantity(release['acceptedQuantity']) and sum((quantity(t['rejectedQuantity']) for t in transfers.values() if t['batch'] == transfer['batch']), Decimal(0)) <= quantity(release['rejectedQuantity']), 'Repeated partial transfers cannot exceed the authorized lot totals.')
                require(quantity(child.get('acceptedQuantity', 0)) == sum((quantity(t['quantity']) for t in transfers.values()), Decimal(0)) and quantity(child.get('rejectedQuantity', 0)) == sum((quantity(t['rejectedQuantity']) for t in transfers.values()), Decimal(0)), 'Accepted/rejected totals must reconcile to authorized stock transfers.')
            elif child.get('acceptedQuantity') != before.get('acceptedQuantity') or child.get('rejectedQuantity') != before.get('rejectedQuantity'):
                require(False, 'Accepted/rejected totals can change only with a validated transfer.')
            production = [s for s in after_stages.values() if s.get('required') and s.get('kind') == 'production']
            if before['status'] in ('Draft', 'PendingApproval', 'StoreIssuePending'):
                require(child['status'] == before['status'], 'Tracking cannot approve or issue a Job Card.')
            elif production:
                complete = all(s['status'] == 'Completed' for s in production)
                produced = quantity(sorted(production, key=lambda s: s['sequence'])[-1].get('outputQuantity') or 0)
                transferred = sum((quantity(t['quantity']) + quantity(t['rejectedQuantity']) for t in transfers.values()), Decimal(0))
                require(transferred <= produced, 'Stock transfers exceed final production output.')
                expected_status = 'Completed' if complete and transfers and transferred == produced else 'ReadyForStock' if complete else 'OnHold' if any(s['status'] == 'OnHold' for s in production) else 'InProduction' if any(s['status'] in ('InProgress', 'Completed') for s in production) else 'MaterialIssued'
                require(child['status'] == expected_status, 'Job status must follow validated stage/transfer progress.')
            child['activityHistory'] = before.get('activityHistory', []) + [{'id': str(__import__('uuid').uuid4()), 'timestamp': timezone.now().isoformat(), 'action': 'Tracking updated', 'userId': str(user.pk), 'user': user.get_full_name() or user.username, 'department': user.department, 'description': 'Server-validated production/QC/stock tracking change.'}]
        # Parent summaries are derived from persisted children, not client status labels.
        current['manufacturingItems'] = list(changed.values())
        states = [row['status'] for row in changed.values()]
        current['status'] = 'Completed' if all(s == 'Completed' for s in states) else 'ReadyForStock' if all(s in ('Completed', 'ReadyForStock') for s in states) else 'OnHold' if 'OnHold' in states else 'InProduction' if 'InProduction' in states else current['status']
        current['activityHistory'] = current.get('activityHistory', []) + [{'id': str(__import__('uuid').uuid4()), 'timestamp': timezone.now().isoformat(), 'action': 'Production tracking saved', 'user': user.get_full_name() or user.username, 'userId': str(user.pk), 'department': user.department, 'description': 'Server-validated runs, stages, QC lots or stock transfer saved.'}]
        job.version += 1; current['workflowVersion'] = job.version; job.data = current; job.save(update_fields=['data', 'version', 'updated_at'])


def validate_order_links(content, previous, user):
    old = previous or []; require(content[:len(old)] == old, 'Order link history is append-only.')
    require(user.effective_role in ('admin', 'store_keeper', 'accounts', 'receptionist', 'production_manager'), 'This role cannot link order records.')
    for command in content[len(old):]:
        order = WorkflowOrder.objects.select_for_update().get(pk=int(command['orderId'].removeprefix('wf-order-')))
        require(order.confirmed_revision_id is None, 'Controlled order links require controlled execution commands.')
        if command['kind'] == 'job':
            require(WorkflowJob.objects.filter(order=order, data__id=command['value']).exists(), 'Link only this order’s saved Job Card.')
            order.data['jobCardIds'] = list(dict.fromkeys(order.data.get('jobCardIds', []) + [command['value']]))
        else:
            item = next(row for row in order.data['items'] if row['id'] == command['itemId'])
            if command['kind'] == 'product':
                authorize(user, 'stock-receipt')
                product = FinishedStockItem.objects.get(pk=command['value'])
                require(product.unit == item['unit'] and (not item.get('itemId') or item['itemId'] == str(product.pk)), 'Finished output must use its exact product/unit.')
                item['itemId'] = str(product.pk)
            elif command['kind'] == 'estimate':
                estimate = next(row for row in shared('costing-estimates', []) if row['id'] == command['value'])
                require((estimate['input'].get('orderId') == order.data['id'] and estimate['input']['itemId'] == item['id']) or (estimate['input'].get('quotationId') == order.data.get('quotationId') and estimate['input']['itemId'] == item.get('quotationLineId')), 'Costing must refer to this exact order/quotation item.')
                item['estimateId'] = estimate['id']
            else:
                require(False, 'Unknown order-link action.')
        order.save(update_fields=['data', 'updated_at'])


def raw_held_quantity(material):
    state = shared('quality-state', {'inspections': [], 'releases': []})
    released = {r['inspectionId'] for r in state['releases'] if r['purpose'] == 'quality'}
    accepted = {i['subject'].get('receiptId') for i in state['inspections'] if i['id'] in released and i['subject'].get('kind') == 'raw' and i['status'] == 'Approved/Released'}
    return sum((row.quantity for row in material.movements.filter(movement_type='IN') if str(row.pk) not in accepted), Decimal(0))
