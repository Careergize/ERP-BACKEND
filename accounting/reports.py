from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from .models import Book, Account, Party, Document, JournalLine, StockValue, ValueMovement
from .services import day, require, ZERO, outstanding


def document_data(d, as_of=None):
    allocations = [{'bill': a.bill_id, 'billNumber': a.bill.number, 'itemLine': a.item_line_id,
                    'amount': str(a.amount), 'items': [a.item_line.description] if a.item_line else list(a.bill.items.values_list('description', flat=True))}
                   for a in d.allocations.select_related('bill', 'item_line')]
    return {'id': d.pk, 'kind': d.kind, 'number': d.number, 'date': str(d.date), 'party': d.party_id,
        'partyName': d.party.name if d.party else '', 'currency': d.currency, 'fx': str(d.fx), 'total': str(d.total),
        'outstanding': str(outstanding(d, as_of)) if d.kind in {'invoice','bill'} else None,
        'unallocated': str(d.total - sum((a.amount for a in d.allocations.all()), ZERO)) if d.kind in {'receipt','payment'} else None,
        'reversed': hasattr(d, 'reversal') and (not as_of or d.reversal.date <= as_of), 'reversalOf': d.reversal_of_id, 'notes': d.notes, 'source': d.source,
        'actor': d.actor_id, 'createdAt': d.created_at.isoformat(), 'allocations': allocations,
        'items': [{'id': i.pk, 'item': i.item, 'description': i.description, 'quantity': str(i.quantity), 'unit': i.unit, 'price': str(i.price), 'net': str(i.net), 'tax': str(i.tax)} for i in d.items.all()]}


def report(start, end, party=None, account_code=None):
    start, end = day(start), day(end)
    require(start <= end, 'Start date must be on or before end date.')
    book = Book.objects.first()
    require(book is not None, 'Configure accounting first.')
    all_lines = JournalLine.objects.select_related('document', 'account', 'party').filter(document__date__lte=end).order_by('document__date', 'document_id', 'id')
    balances = defaultdict(lambda: [ZERO, ZERO, ZERO])
    customer = defaultdict(lambda: [ZERO, ZERO, ZERO])
    ledger = []; running = ZERO; opening = ZERO
    for l in all_lines:
        signed = l.debit - l.credit
        b = balances[l.account_id]
        if l.document.date < start:
            b[0] += signed
        else:
            b[1] += l.debit; b[2] += l.credit
        if l.party_id and l.account_id in {'1300','2100'}:
            p = customer[l.party_id]
            if l.document.date < start: p[0] += signed
            else: p[1] += l.debit; p[2] += l.credit
        if (party and str(l.party_id) == str(party) and l.account_id in {'1300','2100'}) or (account_code and l.account_id == account_code):
            running += signed
            if l.document.date < start:
                opening += signed
            else:
                ledger.append({'date': str(l.document.date), 'document': l.document_id, 'number': l.document.number,
                    'kind': l.document.kind, 'party': l.party.name if l.party else '', 'debit': str(l.debit), 'credit': str(l.credit),
                    'balance': str(running), 'currency': l.document.currency, 'foreignAmount': str(l.foreign_amount)})
    trial = []; income = ZERO; expenses = ZERO
    sheet = defaultdict(lambda: ZERO)
    for a in Account.objects.all().order_by('code'):
        b = balances[a.pk]; close = b[0]+b[1]-b[2]
        trial.append({'code': a.pk, 'name': a.name, 'kind': a.kind, 'opening': str(b[0]), 'debit': str(b[1]), 'credit': str(b[2]), 'balance': str(close), 'closingDebit': str(max(close, ZERO)), 'closingCredit': str(max(-close, ZERO))})
        if a.kind == 'income': income += b[2]-b[1]
        if a.kind == 'expense': expenses += b[1]-b[2]
        sheet[a.kind] += close
    cash_flow = defaultdict(lambda: ZERO); cash_details=defaultdict(lambda:[ZERO,ZERO]); cash_accounts = []
    for a in Account.objects.filter(cash=True):
        b = balances[a.pk]
        cash_accounts.append({'code': a.pk, 'name': a.name, 'opening': str(b[0]), 'inflows': str(b[1]), 'outflows': str(b[2]), 'closing': str(b[0]+b[1]-b[2])})
    for d in Document.objects.filter(date__range=(start,end)):
        if d.kind == 'transfer' or (d.kind == 'reverse' and d.reversal_of and d.reversal_of.kind == 'transfer'): continue
        n = sum((j.debit-j.credit for j in d.journal.filter(account__cash=True)), ZERO)
        # Opening journals are reconciliation balances, not period cash generation.
        if d.kind != 'opening':
            cash_flow[d.cash_class] += n
            cash_details[d.cash_class][0] += max(n,ZERO); cash_details[d.cash_class][1] += max(-n,ZERO)
    parties = {p.pk:p for p in Party.objects.all()}
    party_totals = [{'id': pk, 'name': parties[pk].name, 'kind': parties[pk].kind, 'opening': str(v[0]), 'debit': str(v[1]), 'credit': str(v[2]), 'balance': str(v[0]+v[1]-v[2])} for pk,v in customer.items()]
    docs = Document.objects.filter(date__range=(start,end)).select_related('party','reversal_of').prefetch_related('items','allocations__bill__items','allocations__item_line','settlements__payment','journal').order_by('-date','-id')
    # Financial sales/purchases and expense rows include reversals as signed rows.
    transaction_rows = []
    for d in docs:
        original = d.reversal_of if d.kind == 'reverse' else d
        if original.kind not in {'invoice','bill','expense'}: continue
        sign = -1 if d.kind == 'reverse' else 1
        for i in original.items.all():
            transaction_rows.append({'date': str(d.date), 'document': d.pk, 'number': d.number, 'kind': original.kind,
                'party': original.party.name if original.party else '', 'item': i.item, 'description': i.description,
                'quantity': str(i.quantity*sign), 'unit': i.unit, 'price': str(i.price), 'net': str(i.net*sign), 'tax': str(i.tax*sign),
                'currency': original.currency, 'baseNet': str(i.net*original.fx*sign), 'category': i.category.name if i.category else ''})
    inventory=[]
    for s in StockValue.objects.all():
        moves=ValueMovement.objects.filter(stock=s,document__date__lte=end)
        totals=moves.aggregate(qty=Sum('quantity'),value=Sum('value'))
        if totals['qty'] is not None:
            inventory.append({'item':s.item,'unit':s.unit,'quantity':str(totals['qty']),'value':str(totals['value']),'asOf':str(end)})
    return {'start': str(start), 'end': str(end), 'currency': book.currency,
        'profitLoss': {'income': str(income), 'expenses': str(expenses), 'netProfit': str(income-expenses),
            'accounts': [r for r in trial if r['kind'] in {'income','expense'}]},
        'balanceSheet': {'assets': str(sheet['asset']), 'liabilities': str(-sheet['liability']), 'equity': str(-sheet['equity']),
            'retainedEarnings': str(-sheet['income']-sheet['expense']),
            'difference': str(sum(sheet.values(),ZERO)), 'accounts': [r for r in trial if r['kind'] in {'asset','liability','equity'}]},
        'trialBalance': trial, 'trialDifference': str(sum((Decimal(r['balance']) for r in trial),ZERO)),
        'cashFlow': dict((k,str(v)) for k,v in cash_flow.items()), 'cashBank': cash_accounts,
        'cashFlowDetails':[{'category':k,'inflows':str(v[0]),'outflows':str(v[1]),'net':str(v[0]-v[1])} for k,v in cash_details.items()],
        'partyBalances': party_totals, 'ledger': ledger, 'ledgerOpening': str(opening), 'ledgerClosing': str(running),
        'transactions': transaction_rows, 'documents': [document_data(d,end) for d in docs],
        'inventory': inventory}
