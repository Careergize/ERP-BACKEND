"""Atomic accounting commands. No browser-computed totals are authoritative."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Sum, Q
from rest_framework.exceptions import ValidationError

from .models import Book, Account, Party, Document, DocumentLine, JournalLine, Allocation, StockValue, ValueMovement, CostAssignment, VoucherType, CostCategory

ZERO = Decimal('0')
CENT = Decimal('.01')
ACCOUNTS = [
    ('1100', 'Cash', 'asset', True), ('1200', 'Bank', 'asset', True),
    ('1300', 'Customer receivables', 'asset', False), ('1400', 'Inventory', 'asset', False),
    ('1500', 'Recoverable purchase tax', 'asset', False),
    ('2100', 'Supplier payables', 'liability', False), ('2200', 'Sales tax payable', 'liability', False),
    ('2300', 'Goods received clearing', 'liability', False), ('3100', 'Opening equity', 'equity', False),
    ('4100', 'Sales', 'income', False), ('4200', 'Exchange differences', 'income', False),
    ('5100', 'Cost of goods sold', 'expense', False), ('6100', 'General expenses', 'expense', False),
    ('1600', 'Work in progress', 'asset', False),
]


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def dec(value, places=2, positive=False):
    try:
        require(not isinstance(value, bool), 'Invalid numeric value.')
        n = Decimal(str(value))
        require(n.is_finite() and ZERO <= n <= Decimal('1e14') and (not positive or n > 0), 'Enter a finite nonnegative amount (maximum 100 trillion).')
        require(n == n.quantize(Decimal(1).scaleb(-places)), f'Use at most {places} decimal places.')
        return n
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('Enter a valid decimal number.')


def money(value):
    require(value.is_finite() and abs(value) <= Decimal('1e20'), 'Amount exceeds the supported accounting range.')
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def day(value):
    try:
        result = date.fromisoformat(value)
        require(result.isoformat() == value, 'Use YYYY-MM-DD dates.')
        return result
    except (TypeError, ValueError):
        raise ValidationError('Enter a valid date (YYYY-MM-DD).')


def text(value, limit=250):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit and not re.search(r'[\x00-\x1f]', value), 'Enter nonempty text within the field length limit.')
    return value.strip()


def account(code, kind=None, cash=None):
    a = Account.objects.filter(pk=code).first()
    require(a is not None and (kind is None or a.kind == kind) and (cash is None or a.cash == cash), 'Select an appropriate account.')
    return a


def journal(doc, code, signed, party=None, foreign=ZERO):
    if not signed:
        return
    JournalLine.objects.create(document=doc, account=account(code), party=party,
        debit=max(signed, ZERO), credit=max(-signed, ZERO), foreign_amount=foreign)


def outstanding(doc, as_of=None):
    rows = doc.settlements.select_related('payment').all()
    allocated = sum((a.amount for a in rows if (not as_of or (a.date or a.payment.date) <= as_of) and (not hasattr(a.payment,'reversal') or (as_of and a.payment.reversal.date > as_of))), ZERO)
    return doc.total - allocated


def stock_move(doc, item, unit, qty, value=None):
    from .valuation_items import catalog_unit
    expected_unit = catalog_unit(item)
    require(not expected_unit or unit == expected_unit, f'{item}: item unit is {expected_unit}; received {unit}. Select the item to use its automatic unit. No unit conversion is performed.')
    stock = StockValue.objects.select_for_update().filter(pk=item).first()
    if not stock:
        require(qty > 0 and value is not None, f'Opening quantity/value required for {item}.')
        stock = StockValue.objects.create(item=item, unit=unit, last_date=doc.date)
    require(stock.unit == unit, f'{item}: existing accounting valuation uses {stock.unit}, but this movement uses {unit}. Reconcile the earlier valuation; existing quantities cannot be relabeled or converted automatically.')
    require(doc.date >= stock.last_date, f'{item}: valuation date {doc.date} is before the latest valued movement on {stock.last_date}. Backdated valuation would invalidate later costs. Review the valuation history before correcting it.')
    if qty < 0:
        require(stock.quantity >= -qty, f'Insufficient valued stock: {item}. Set reconciled opening values first.')
        value = -stock.value if stock.quantity == -qty else -money(stock.value * -qty / stock.quantity)
    require(value is not None and stock.value + value >= 0, 'Invalid stock value.')
    stock.quantity += qty; stock.value += value; stock.last_date = doc.date
    stock.save()
    ValueMovement.objects.create(document=doc, stock=stock, quantity=qty, value=value)
    return value


@transaction.atomic
def post(payload, user, source=None):
    require(isinstance(payload, dict), 'Supply an accounting command object.')
    try:
        command = UUID(str(payload['commandId']))
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (KeyError, ValueError, TypeError):
        raise ValidationError('Supply a valid commandId UUID and finite JSON fields.')
    # Serialize all commands against the book, including first writes, allocations,
    # period close and stock valuation. Keep this lock before reading balances.
    book = Book.objects.select_for_update().filter(pk=1).first()
    require(book is not None, 'Configure the accounting book first.')
    existing = Document.objects.filter(command=command).first()
    if existing:
        require(existing.digest == digest and existing.actor_id == user.pk, 'Command ID was used for different details or another account.')
        return existing
    if source:
        require(not Document.objects.filter(source=source).exists(), 'This source document is already posted.')
    kind = payload.get('kind')
    require(kind in {'invoice', 'bill', 'receipt', 'payment', 'allocate', 'expense', 'transfer', 'opening', 'stock-receipt', 'stock-issue', 'reverse', 'assign-cost', 'journal'}, 'Unsupported accounting command.')
    voucher_type = None; cost_category = None
    if payload.get('voucherType') not in (None, ''):
        require(type(payload['voucherType']) is int, 'Select a saved voucher type.')
        voucher_type = VoucherType.objects.filter(pk=payload['voucherType'],base_kind=kind).first()
        require(voucher_type is not None, 'Voucher type must match this transaction behaviour.')
    if payload.get('costCategory') not in (None, ''):
        require(type(payload['costCategory']) is int, 'Select a saved cost category.')
        cost_category = CostCategory.objects.filter(pk=payload['costCategory']).first()
        require(cost_category is not None, 'Select a valid cost category.')
    when = day(payload.get('date'))
    require(when >= book.start_date and (not book.closed_through or when > book.closed_through), 'Date is before cutover or in a closed period.')
    currency = payload.get('currency', book.currency)
    require(isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency), 'Select a three-letter currency.')
    fx = dec(payload.get('fx', '1'), 8, True)
    require(currency == book.currency or 'fx' in payload, 'An explicit exchange rate is required for foreign currency.')
    require(currency != book.currency or fx == 1, 'Reporting currency exchange rate must be 1.')
    party = None
    if kind in {'invoice', 'bill', 'receipt', 'payment', 'allocate'}:
        party = Party.objects.filter(pk=payload.get('party')).first()
        require(party is not None and (kind=='allocate' or party.kind == ('customer' if kind in {'invoice', 'receipt'} else 'supplier')), 'Select the correct customer or supplier.')
    customer = None
    if payload.get('customer'):
        customer = Party.objects.filter(pk=payload['customer'], kind='customer').first()
        require(customer is not None, 'Select a valid customer for cost attribution.')
    if kind == 'assign-cost':
        require(customer is not None, 'Select the customer receiving this cost.')
        party = customer
    doc = Document.objects.create(command=command, digest=digest, source=source, kind=kind,
        number=text(payload.get('number'), 80), date=when, currency=currency, fx=fx, party=party,
        total=0, notes=str(payload.get('notes', ''))[:2000], actor=user,
        cash_class=payload.get('cashClass', 'operating'), voucher_type=voucher_type, cost_category=cost_category)
    require(doc.cash_class in {'operating', 'investing', 'financing'}, 'Select a cash-flow classification.')
    if kind in {'invoice', 'bill', 'expense'}:
        items = payload.get('items')
        require(isinstance(items, list) and 1 <= len(items) <= 200 and all(isinstance(i, dict) for i in items), 'Enter 1–200 item lines.')
        total = ZERO
        for row in items:
            qty, price, tax = dec(row.get('quantity'), 6, True), dec(row.get('price'), 6), dec(row.get('tax', '0'))
            net = dec(row['sourceNet']) if source and 'sourceNet' in row else money(qty * price)
            total += net + tax
            line = DocumentLine.objects.create(document=doc, item=str(row.get('item', ''))[:120],
                description=text(row.get('description')), quantity=qty, unit=str(row.get('unit', ''))[:30], price=price, net=net, tax=tax)
            if kind == 'invoice':
                tax_account=row.get('taxAccount','2200') if source else '2200'
                require(account(tax_account).kind=='liability','Sales tax must map to a liability account.')
                journal(doc, '4100', -money(net * fx), party); journal(doc, tax_account, -money(tax * fx))
            else:
                target = row.get('account', '6100')
                a = account(target)
                require((not a.control and not a.cash and a.kind in {'expense', 'asset'}) or (kind == 'bill' and target == '2300'), 'Select an expense/asset category or goods-received clearing for a matched stock bill.')
                require(not customer or a.kind == 'expense', 'Customer costs must use expense categories, not assets or clearing.')
                journal(doc, target, money(net * fx), customer); journal(doc, '1500', money(tax * fx))
                line.category = a; line.save(update_fields=['category'])
        doc.total = money(total)
        require(doc.total > 0, 'Document total must be positive.')
        control = '1300' if kind == 'invoice' else '2100' if kind == 'bill' else account(payload.get('cashAccount'), cash=True).pk
        signed = money(doc.total * fx) * (1 if kind == 'invoice' else -1)
        journal(doc, control, signed, party, doc.total * (1 if kind == 'invoice' else -1))
    elif kind in {'receipt', 'payment', 'allocate'}:
        payment_doc=doc
        if kind=='allocate':
            payment_doc=Document.objects.select_for_update().filter(pk=payload.get('paymentDocument'),kind__in=['receipt','payment'],party=party,currency=currency,reversal__isnull=True).first()
            require(payment_doc is not None and payment_doc.date<=when and payment_doc.fx==fx,'Select an earlier active payment with the same party, currency and original FX rate.')
            doc.total=ZERO
            sign=1 if payment_doc.kind=='receipt' else -1
        else:
            doc.total = dec(payload.get('amount'), positive=True)
            cash = account(payload.get('cashAccount'), cash=True)
            sign = 1 if kind == 'receipt' else -1
            journal(doc, cash.pk, money(doc.total * fx) * sign)
        # Full payment is recorded in the party control, with unapplied amounts
        # remaining explicit customer/supplier advances in the party ledger.
        if kind!='allocate':journal(doc, '1300' if sign == 1 else '2100', -money(doc.total * fx) * sign, party, -doc.total * sign)
        allocations = payload.get('allocations', [])
        require(isinstance(allocations, list) and len(allocations) <= 200, 'Invalid allocations.')
        used = ZERO
        for row in allocations:
            bill = Document.objects.select_for_update().filter(pk=row.get('bill'), kind='invoice' if sign==1 else 'bill', party=party, currency=currency, reversal__isnull=True).first()
            require(bill is not None and bill.date <= when, 'Allocate only to an active earlier invoice/bill for this party and currency.')
            n = dec(row.get('amount'), positive=True)
            require(n <= outstanding(bill), 'Allocation exceeds the outstanding invoice/bill balance.')
            item = None
            if row.get('itemLine'):
                item = bill.items.filter(pk=row['itemLine']).first()
                require(item is not None, 'Allocation item must belong to the selected bill.')
                previous = Allocation.objects.filter(item_line=item, payment__reversal__isnull=True).aggregate(n=Sum('amount'))['n'] or ZERO
                require(n + previous <= item.net + item.tax, 'Item allocation exceeds its value.')
            Allocation.objects.create(payment=payment_doc, bill=bill, item_line=item, amount=n,date=when,recorded_document=doc)
            used += n
            # Settle historical base-currency receivables/payables at bill rate.
            delta = money(n * fx) - money(n * bill.fx)
            journal(doc, '1300' if sign == 1 else '2100', delta * sign, party)
            journal(doc, '4200', -delta * sign)
        if kind=='allocate':
            require(used>0,'Select at least one allocation.')
            allocated=payment_doc.allocations.aggregate(n=Sum('amount'))['n'] or ZERO
            require(allocated<=payment_doc.total,'Allocations exceed the original payment amount.')
        else:require(used <= doc.total, 'Allocations exceed payment amount.')
    elif kind == 'assign-cost':
        require(currency == book.currency, 'Assign costs in reporting currency.')
        line = JournalLine.objects.select_related('document', 'account').filter(pk=payload.get('sourceLine')).first()
        require(line is not None and line.account.kind == 'expense' and line.debit > 0 and not line.party_id,
                'Select an unassigned expense debit.')
        require(line.document.kind not in {'assign-cost', 'reverse'} and not hasattr(line.document, 'reversal'),
                'Select an active original expense, not a reclassification or reversal.')
        require(when >= line.document.date, 'Cost assignment cannot precede the source expense.')
        previous = CostAssignment.objects.filter(source_line=line).select_related('document__reversal')
        require(all(when >= (a.document.reversal.date if hasattr(a.document, 'reversal') else a.document.date)
                    for a in previous), 'Assignment must follow all earlier assignments and their reversals for this source.')
        used = CostAssignment.objects.filter(source_line=line, document__reversal__isnull=True).aggregate(n=Sum('amount'))['n'] or ZERO
        doc.total = dec(payload.get('amount'), positive=True)
        require(doc.total <= line.debit-used, 'Assignment exceeds the remaining unassigned source cost.')
        require(bool(doc.notes.strip()), 'Record the reason for assigning this cost.')
        journal(doc, line.account_id, doc.total, customer)
        journal(doc, line.account_id, -doc.total)
        CostAssignment.objects.create(document=doc, source_line=line, amount=doc.total)
    elif kind == 'transfer':
        doc.total = dec(payload.get('amount'), positive=True)
        require(currency == book.currency, 'Cash transfers currently require reporting currency.')
        a, b = account(payload.get('fromAccount'), cash=True), account(payload.get('toAccount'), cash=True)
        require(a != b, 'Choose different cash/bank accounts.')
        journal(doc, a.pk, -doc.total); journal(doc, b.pk, doc.total)
    elif kind in {'opening', 'journal'}:
        require(currency == book.currency, 'Journal and opening entries use reporting currency.')
        if kind == 'opening': require(when == book.start_date, 'Opening balances must use the cutover date.')
        rows = payload.get('lines')
        require(isinstance(rows, list) and 2 <= len(rows) <= 500, 'Enter a balanced opening journal.')
        for row in rows:
            a = account(row.get('account')); debit, credit = dec(row.get('debit', '0')), dec(row.get('credit', '0'))
            if kind == 'opening': require(a.kind in {'asset','liability','equity'}, 'Opening balances use balance sheet accounts.')
            require((debit > 0) != (credit > 0), 'Each opening line must have only a debit or a credit.')
            p = Party.objects.filter(pk=row.get('party')).first() if row.get('party') else None
            require(not row.get('party') or p is not None, 'Select a valid journal party.')
            require(a.pk not in {'1300','2100'} or (p and p.kind == ('customer' if a.pk == '1300' else 'supplier')), 'Opening control balances require the correct party.')
            require(a.pk != '1400', 'Inventory entries must use the valuation workflow.')
            journal(doc, a.pk, debit-credit, p, debit-credit if p else ZERO)
        doc.total = sum((j.debit for j in doc.journal.all()), ZERO)
    elif kind in {'stock-receipt', 'stock-issue'}:
        require(currency == book.currency, 'Valuation commands use reporting currency.')
        qty = dec(payload.get('quantity'), 6, True)
        item, unit = text(payload.get('item'), 120), text(payload.get('unit'), 30)
        value = dec(payload.get('value'), positive=True) if kind == 'stock-receipt' else None
        moved = stock_move(doc, item, unit, qty if kind == 'stock-receipt' else -qty, value)
        doc.total = abs(moved)
        contra = payload.get('contra', '2300' if kind == 'stock-receipt' else '5100')
        require(contra in ({'2300','3100','1600'} if kind == 'stock-receipt' else {'5100','1600','6100'}), 'Select an approved stock contra account.')
        require(not customer or (kind == 'stock-issue' and account(contra).kind == 'expense'), 'Customer stock costs require an expense issue.')
        journal(doc, '1400', moved); journal(doc, contra, -moved, customer)
    elif kind == 'reverse':
        original = Document.objects.select_for_update().filter(pk=payload.get('document')).first()
        require(original is not None and original.kind != 'reverse' and not Document.objects.filter(reversal_of=original).exists() and when >= original.date, 'Select an unreversed original document dated on or before reversal.')
        require(not original.settlements.filter(payment__reversal__isnull=True).exists(), 'Reverse allocated payments before reversing an invoice/bill.')
        require(not CostAssignment.objects.filter(source_line__document=original).filter(
                    Q(document__reversal__isnull=True) | Q(document__reversal__date__gt=when)).exists(),
                'Reverse active customer cost assignments before reversing their source expense.')
        require(original.kind!='allocate', 'Reverse the original payment to undo its allocations.')
        for movement in original.valuations.select_related('stock'):
            stock=StockValue.objects.select_for_update().get(pk=movement.stock_id)
            require(when>=stock.last_date,'Inventory reversal cannot precede later valuation movements.')
            if movement.quantity>0:
                require(not ValueMovement.objects.filter(stock=stock,pk__gt=movement.pk).exists(),'Receipt reversal requires no subsequent valuation movements; use a reviewed supplier return workflow otherwise.')
            require(stock.quantity-movement.quantity>=0 and stock.value-movement.value>=0,'Reversal exceeds available valued stock.')
            stock.quantity-=movement.quantity;stock.value-=movement.value;stock.last_date=when;stock.save()
            ValueMovement.objects.create(document=doc,stock=stock,quantity=-movement.quantity,value=-movement.value)
        require(bool(doc.notes.strip()), 'A reversal reason is required.')
        doc.reversal_of = original; doc.total = original.total; doc.currency = original.currency; doc.fx = original.fx; doc.party = original.party; doc.cash_class = original.cash_class
        doc.cost_category = original.cost_category
        for line in original.journal.all():
            journal(doc, line.account_id, line.credit-line.debit, line.party, -line.foreign_amount)
        # Later application of an advance can have its own FX settlement journal.
        # Reverse those differences together with the original payment.
        for allocation_doc in Document.objects.filter(allocation_commands__payment=original).exclude(pk=original.pk).distinct():
            for line in allocation_doc.journal.all():
                journal(doc,line.account_id,line.credit-line.debit,line.party,-line.foreign_amount)
    balances = doc.journal.aggregate(dr=Sum('debit'), cr=Sum('credit'))
    # Explicit rounding account handles only cent differences produced by line FX.
    diff = (balances['dr'] or ZERO) - (balances['cr'] or ZERO)
    if diff and kind in {'invoice','bill','expense'} and abs(diff) <= CENT * 200:
        journal(doc, '4200', -diff)
    balances = doc.journal.aggregate(dr=Sum('debit'), cr=Sum('credit'))
    require((kind=='allocate' and not balances['dr'] and not balances['cr']) or (balances['dr'] and balances['dr'] == balances['cr']), 'Journal must balance and contain a nonzero posting.')
    require(doc.total <= Decimal('1e20'), 'Document total exceeds the supported range.')
    doc.save()
    return doc
