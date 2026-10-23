"""Planned costing snapshots, calculated from the saved Job Card scope."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.utils import timezone
from rest_framework import serializers

MASS = {'kg': Decimal(1), 'g': Decimal('.001'), 'tonne': Decimal(1000)}


def value(raw, label, positive=False):
    try:
        result = Decimal(str(raw))
        if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal('99999999999999'):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise serializers.ValidationError(f'Enter a valid {label}.')


def money(n):
    if not n.is_finite() or abs(n) > Decimal('9999999999999.99'):
        raise serializers.ValidationError('Cost exceeds the supported amount range.')
    return float(n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def convert(quantity, source, target):
    if source == target and source:
        return quantity
    if source in MASS and target in MASS:
        return quantity * MASS[source] / MASS[target]
    raise serializers.ValidationError(f'No configured conversion from {source} to {target}. Use matching units.')


def new_sheet(child, model, previous=None, item=None, tax_rate=None):
    previous = previous or {}
    prior = previous.get('input', {})
    recipe = {r['materialId']: r for r in model.get('recipe', [])}
    saved_materials = {r['materialId']: r for r in prior.get('materials', [])}
    materials = []
    for requirement in child['requiredMaterials']:
        source = saved_materials.get(requirement['materialId'], recipe.get(requirement['materialId'], {}))
        materials.append({**{k: deepcopy(source.get(k)) for k in ('price', 'priceUnit', 'currency', 'exchangeRate')},
                          'priceIncludesVAT': source.get('priceIncludesVAT', False), 'purchaseTaxRate': source.get('purchaseTaxRate', 0),
                          'materialId': requirement['materialId'], 'name': requirement['name'], 'quantity': requirement['requiredQuantity'],
                          'unit': requirement['unit'], 'priceUnit': source.get('priceUnit') or requirement['unit']})
    saved_processes = {p['stageId']: p for p in prior.get('processes', [])}
    template_processes = {p['stageId']: p for p in model.get('processes', []) if p.get('stageId')}
    processes = []
    for stage in child['productionStages']:
        if stage['required']:
            source = saved_processes.get(stage['id'], template_processes.get(stage['id'], {}))
            quantity = source.get('quantity', 1 if source.get('basis', 'fixed') == 'fixed' else None)
            if stage['id'] not in saved_processes and source.get('basis', 'fixed') != 'fixed' and quantity is not None:
                quantity = float(value(quantity, 'template process quantity') * value(child['qty'], 'production quantity', True) / value(model.get('productionBatchQuantity', model.get('batchQuantity')), 'model output quantity', True))
            processes.append({'stageId': stage['id'], 'name': stage['name'], 'basis': source.get('basis', 'fixed'),
                              'rate': source.get('rate'), 'quantity': quantity, 'unit': source.get('unit', 'batch')})
    config = model.get('costingConfig', {})
    led = (item or {}).get('led') or {}
    inputs = {**prior, 'currency': prior.get('currency', config.get('currency', 'UGX')), 'materials': materials, 'processes': processes,
              'overheads': prior.get('overheads', model.get('overheads', 0)),
              'additionalCosts': prior.get('additionalCosts', 0), 'taxRate': prior.get('taxRate', tax_rate if tax_rate is not None else config.get('taxPercent', 0)),
              'ledApplies': prior.get('ledApplies', bool(led.get('applies'))),
              'eligibleWeightKG': prior.get('eligibleWeightKG', float(value(child['qty'], 'quantity') * MASS[child['unit']]) if child['unit'] in MASS else None),
              'weightReference': prior.get('weightReference', ''), 'usdToUGX': prior.get('usdToUGX', led.get('usdToUGX')),
              'fxDate': prior.get('fxDate', led.get('fxDate', '')), 'vatOnLED': prior.get('vatOnLED', led.get('vatOnLED', False)),
              'ledBase': prior.get('ledBase', led.get('percentageBase') or 'net-selling')}
    sheet = {'revision': previous.get('revision', 0) + 1, 'status': 'Draft', 'input': inputs,
             'createdAt': timezone.now().isoformat(), 'basis': 'planned', 'actualCost': None,
             'history': previous.get('history', []) + ([{k: deepcopy(v) for k, v in previous.items() if k != 'history'}] if previous else [])}
    return recalculate(sheet, child)


def validate_scope(inputs, child):
    try:
        rows = inputs['materials']
        expected = {r['materialId']: r for r in child['requiredMaterials']}
        if len(rows) != len(expected) or {r['materialId'] for r in rows} != set(expected):
            raise ValueError()
        for row in rows:
            required = expected[row['materialId']]
            if row['unit'] != required['unit'] or value(row['quantity'], 'material quantity') != value(required['requiredQuantity'], 'required quantity'):
                raise ValueError()
        stages = {s['id'] for s in child['productionStages'] if s['required']}
        if len(inputs['processes']) != len(stages) or {p['stageId'] for p in inputs['processes']} != stages:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise serializers.ValidationError('Costing must reference the exact saved Job Card material quantities, units and required process steps.')


def calculate(inputs, child):
    validate_scope(inputs, child)
    if not isinstance(inputs.get('ledApplies'), bool) or not isinstance(inputs.get('vatOnLED'), bool):
        raise serializers.ValidationError('Select valid LED applicability and VAT treatment.')
    currency = inputs.get('currency')
    if currency not in ('UGX', 'USD'):
        raise serializers.ValidationError('Select UGX or USD for costing.')
    if len(inputs.get('materials', [])) != len(child['requiredMaterials']) or {r['materialId'] for r in inputs['materials']} != {r['materialId'] for r in child['requiredMaterials']}:
        raise serializers.ValidationError('Cost every approved Job Card material exactly once.')
    requirements = {r['materialId']: r for r in child['requiredMaterials']}
    material_totals = []
    for row in inputs['materials']:
        if not isinstance(row.get('priceIncludesVAT'), bool):
            raise serializers.ValidationError('Select whether each purchase price includes VAT.')
        requirement = requirements[row['materialId']]
        if value(row['quantity'], 'material quantity') != value(requirement['requiredQuantity'], 'required quantity') or row['unit'] != requirement['unit']:
            raise serializers.ValidationError('Costing quantities and units must match the saved Job Card materials.')
        price = value(row.get('price'), 'material purchase price')
        purchase_tax = value(row.get('purchaseTaxRate'), 'purchase VAT rate')
        if purchase_tax > 100:
            raise serializers.ValidationError('Purchase VAT cannot exceed 100%.')
        if row.get('priceIncludesVAT'):
            price /= 1 + purchase_tax / 100
        if row.get('currency') not in ('UGX', 'USD'):
            raise serializers.ValidationError('Select each material purchase currency.')
        fx = Decimal(1) if row['currency'] == currency else value(row.get('exchangeRate'), 'exchange rate', True)
        amount = convert(value(row['quantity'], 'material quantity', True), row['unit'], row.get('priceUnit')) * price * fx
        material_totals.append({'materialId': row['materialId'], 'netUnitPrice': money(price * fx), 'total': money(amount)})
    required_stages = {s['id']: s for s in child['productionStages'] if s['required']}
    rows = inputs.get('processes', [])
    if len(rows) != len(required_stages) or {p['stageId'] for p in rows} != set(required_stages):
        raise serializers.ValidationError('Cost each required production stage exactly once.')
    process_totals = []
    for row in rows:
        if row.get('basis') not in ('fixed', 'per kg', 'per piece', 'per batch'):
            raise serializers.ValidationError('Select a process cost basis.')
        if row['basis'] in ('per kg', 'per piece') and row.get('unit') != ('kg' if row['basis'] == 'per kg' else 'pcs'):
            raise serializers.ValidationError('Process cost unit must match its cost basis.')
        quantity = Decimal(1) if row['basis'] == 'fixed' else value(row.get('quantity'), 'process cost quantity', True)
        process_totals.append({'stageId': row['stageId'], 'total': money(value(row.get('rate'), 'process cost rate') * quantity)})
    material_total = sum((Decimal(str(r['total'])) for r in material_totals), Decimal(0))
    process_total = sum((Decimal(str(r['total'])) for r in process_totals), Decimal(0))
    cost = material_total + process_total + value(inputs.get('overheads'), 'overheads') + value(inputs.get('additionalCosts'), 'additional costs')
    tax_rate = value(inputs.get('taxRate'), 'sales VAT rate')
    if tax_rate > 100:
        raise serializers.ValidationError('Sales VAT cannot exceed 100%.')
    scenarios = []
    for markup in (10, 15, 20, 25):
        profit = cost * Decimal(markup) / 100
        before_tax = cost + profit
        led_amount = Decimal(0)
        if inputs.get('ledApplies'):
            if currency != 'UGX' or inputs.get('ledBase') not in ('net-selling', 'production-cost'):
                raise serializers.ValidationError('LED needs UGX costing and an explicit percentage base.')
            kg = value(inputs.get('eligibleWeightKG'), 'eligible LED weight', True)
            if child['unit'] in MASS:
                if kg != value(child['qty'], 'production quantity') * MASS[child['unit']]:
                    raise serializers.ValidationError('LED weight must match the Job Card mass quantity.')
            elif not str(inputs.get('weightReference', '')).strip():
                raise serializers.ValidationError('Record the source for conversion from production units to eligible LED kg.')
            serializers.DateField().run_validation(inputs.get('fxDate'))
            fx = value(inputs.get('usdToUGX'), 'USD to UGX exchange rate', True)
            base = cost if inputs['ledBase'] == 'production-cost' else before_tax
            led_amount = max(base * Decimal('.025'), kg / 1000 * 70 * fx)
        led_amount = Decimal(str(money(led_amount)))
        tax = Decimal(str(money((before_tax + (led_amount if inputs.get('vatOnLED') else 0)) * tax_rate / 100)))
        total = Decimal(str(money(before_tax + led_amount + tax)))
        scenarios.append({'markup': markup, 'profit': money(profit), 'beforeTax': money(before_tax), 'led': money(led_amount), 'vat': money(tax), 'total': money(total), 'unitPrice': money(total / value(child['qty'], 'production quantity', True))})
    return {'materials': material_totals, 'processes': process_totals, 'materialCost': money(material_total), 'processCost': money(process_total),
            'productionCost': money(cost), 'costPerUnit': money(cost / value(child['qty'], 'production quantity', True)), 'scenarios': scenarios}


def recalculate(sheet, child):
    try:
        sheet['totals'] = calculate(sheet['input'], child)
        sheet['validationError'] = ''
    except (serializers.ValidationError, KeyError, TypeError) as error:
        sheet['totals'] = None
        sheet['validationError'] = str(error.detail if isinstance(error, serializers.ValidationError) else 'Complete the costing inputs.')
    return sheet
