from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from .models import Book, Account, Party, Document, JournalLine, StockValue, ValueMovement, CostAssignment, AccountGroup
from .services import day, require, ZERO, outstanding


def document_data(d, as_of=None):
    dated_allocations = [a for a in d.allocations.select_related('bill', 'item_line')
                         if not as_of or (a.date or d.date) <= as_of]
    allocations = [{'bill': a.bill_id, 'billNumber': a.bill.number, 'itemLine': a.item_line_id,
                    'amount': str(a.amount), 'items': [a.item_line.description] if a.item_line else list(a.bill.items.values_list('description', flat=True))}
                   for a in dated_allocations]
    return {'id': d.pk, 'kind': d.kind, 'number': d.number, 'date': str(d.date), 'party': d.party_id,
        'partyName': d.party.name if d.party else '', 'currency': d.currency, 'fx': str(d.fx), 'total': str(d.total),
        'outstanding': str(outstanding(d, as_of)) if d.kind in {'invoice','bill'} else None,
        'unallocated': str(d.total - sum((a.amount for a in dated_allocations), ZERO)) if d.kind in {'receipt','payment'} else None,
        'reversed': hasattr(d, 'reversal') and (not as_of or d.reversal.date <= as_of), 'reversalOf': d.reversal_of_id, 'notes': d.notes, 'source': d.source,
        'actor': d.actor_id, 'createdAt': d.created_at.isoformat(), 'allocations': allocations,
        'voucherType':d.voucher_type_id, 'voucherTypeName':d.voucher_type.name if d.voucher_type else '',
        'costCategory':d.cost_category_id, 'costCategoryName':d.cost_category.name if d.cost_category else '',
        'journal': [{'id': j.pk, 'account': j.account_id, 'accountName': j.account.name, 'cash': j.account.cash,
                     'party': j.party_id, 'partyName': j.party.name if j.party else '',
                     'debit': str(j.debit), 'credit': str(j.credit)}
                    for j in d.journal.select_related('account', 'party')],
        'costSource': ({'line': d.cost_assignment.source_line_id,
                        'document': d.cost_assignment.source_line.document.number}
                       if hasattr(d, 'cost_assignment') else None),
        'items': [{'id': i.pk, 'item': i.item, 'description': i.description, 'quantity': str(i.quantity), 'unit': i.unit, 'price': str(i.price), 'net': str(i.net), 'tax': str(i.tax)} for i in d.items.all()]}


def report(start, end, party=None, account_code=None):
    start, end = day(start), day(end)
    require(start <= end, 'Start date must be on or before end date.')
    book = Book.objects.first()
    require(book is not None, 'Configure accounting first.')
    all_lines = JournalLine.objects.select_related('document__party', 'document__reversal', 'document__cost_category', 'document__voucher_type', 'account', 'party').filter(document__date__lte=end).order_by('document__date', 'document_id', 'id')
    balances = defaultdict(lambda: [ZERO, ZERO, ZERO])
    customer = defaultdict(lambda: [ZERO, ZERO, ZERO])
    profitability = defaultdict(lambda: [ZERO, ZERO, ZERO])
    profit_details = []
    unassigned_income = ZERO; unassigned_cost = ZERO
    category_totals = defaultdict(lambda: [ZERO, ZERO])
    category_names = {}
    ledger = []; running = ZERO; opening = ZERO
    for l in all_lines:
        signed = l.debit - l.credit
        if l.document.date >= start and l.account.kind in {'income', 'expense'}:
            category = l.document.cost_category
            category_names[l.document.cost_category_id] = category.name if category else 'Uncategorised'
            category_totals[l.document.cost_category_id][0 if l.account.kind=='income' else 1] += -signed if l.account.kind=='income' else signed
            # Older sales journals did not tag income lines; their document party
            # is still canonical evidence. Expense reclassification credits must
            # remain unassigned even though the command itself has a customer.
            p = l.party if l.party_id else l.document.party if l.account.kind == 'income' else None
            if p and p.kind == 'customer':
                v = profitability[p.pk]
                if l.account.kind == 'income': v[0] -= signed
                elif l.account_id == '5100': v[1] += signed
                else: v[2] += signed
                profit_details.append({'customerId': p.pk, 'customer': p.name, 'date': str(l.document.date),
                    'document': l.document_id, 'number': l.document.number, 'account': l.account_id,
                    'category': l.account.name, 'income': str(-signed if l.account.kind == 'income' else ZERO),
                    'cost': str(signed if l.account.kind == 'expense' else ZERO)})
            elif l.account.kind == 'income': unassigned_income -= signed
            else: unassigned_cost += signed
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
                    'voucherType':l.document.voucher_type.name if l.document.voucher_type else l.document.kind,
                    'kind': l.document.kind, 'party': l.party.name if l.party else '', 'debit': str(l.debit), 'credit': str(l.credit),
                    'balance': str(running), 'currency': l.document.currency, 'foreignAmount': str(l.foreign_amount)})
    trial = []; income = ZERO; expenses = ZERO
    sheet = defaultdict(lambda: ZERO)
    for a in Account.objects.select_related('group').order_by('code'):
        b = balances[a.pk]; close = b[0]+b[1]-b[2]
        trial.append({'code': a.pk, 'name': a.name, 'kind': a.kind, 'groupId':a.group_id, 'group':a.group.name if a.group else 'Ungrouped', 'opening': str(b[0]), 'debit': str(b[1]), 'credit': str(b[2]), 'balance': str(close), 'closingDebit': str(max(close, ZERO)), 'closingCredit': str(max(-close, ZERO))})
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
    parties = {p.pk:p for p in Party.objects.select_related('group')}
    party_totals = [{'id': pk, 'name': parties[pk].name, 'kind': parties[pk].kind, 'groupId':parties[pk].group_id,
                    'group':parties[pk].group.name if parties[pk].group else 'Ungrouped', 'opening': str(v[0]), 'debit': str(v[1]), 'credit': str(v[2]), 'balance': str(v[0]+v[1]-v[2])} for pk,v in customer.items()]
    docs = Document.objects.filter(date__range=(start,end)).select_related('party','reversal_of').prefetch_related('items','allocations__bill__items','allocations__item_line','settlements__payment','journal').order_by('-date','-id')
    # Financial sales/purchases and expense rows include reversals as signed rows.
    transaction_rows = []
    for d in docs:
        original = d.reversal_of if d.kind == 'reverse' else d
        if original.kind not in {'invoice','bill','expense'}: continue
        sign = -1 if d.kind == 'reverse' else 1
        for i in original.items.all():
            transaction_rows.append({'date': str(d.date), 'document': d.pk, 'number': d.number, 'kind': original.kind,
                'voucherType':original.voucher_type.name if original.voucher_type else original.kind,
                'costCategory':original.cost_category.name if original.cost_category else 'Uncategorised',
                'party': original.party.name if original.party else '', 'item': i.item, 'description': i.description,
                'quantity': str(i.quantity*sign), 'unit': i.unit, 'price': str(i.price), 'net': str(i.net*sign), 'tax': str(i.tax*sign),
                'currency': original.currency, 'baseNet': str(i.net*original.fx*sign), 'category': i.category.name if i.category else ''})
    inventory=[]
    for s in StockValue.objects.all():
        moves=ValueMovement.objects.filter(stock=s,document__date__lte=end)
        totals=moves.aggregate(qty=Sum('quantity'),value=Sum('value'))
        if totals['qty'] is not None:
            inventory.append({'item':s.item,'unit':s.unit,'quantity':str(totals['qty']),'value':str(totals['value']),'asOf':str(end)})
    profit_rows = [{'customerId': p.pk, 'customer': p.name, 'income': str(profitability[p.pk][0]),
                   'costOfSales': str(profitability[p.pk][1]), 'otherCosts': str(profitability[p.pk][2]),
                   'netProfit': str(profitability[p.pk][0]-profitability[p.pk][1]-profitability[p.pk][2])}
                  for p in Party.objects.filter(kind='customer').order_by('name', 'pk')]
    assignable = []
    for line in JournalLine.objects.filter(account__kind='expense', debit__gt=0, party__isnull=True,
            document__date__lte=end).exclude(document__kind__in=['assign-cost','reverse']).select_related('document','account').prefetch_related('cost_assignments__document'):
        if hasattr(line.document,'reversal') and line.document.reversal.date <= end: continue
        used = sum((a.amount for a in line.cost_assignments.all() if a.document.date <= end and
                    (not hasattr(a.document,'reversal') or a.document.reversal.date > end)), ZERO)
        if line.debit > used:
            assignable.append({'line':line.pk,'document':line.document.number,'date':str(line.document.date),
                               'account':line.account.name,'remaining':str(line.debit-used)})
    from .commitments import order_commitments
    groups = {g.pk:g for g in AccountGroup.objects.all()}
    group_totals = defaultdict(lambda: [ZERO,ZERO,ZERO,0])
    for row in trial:
        group_id = row['groupId']; visited = set()
        while group_id in groups and group_id not in visited:
            visited.add(group_id); values = group_totals[group_id]
            values[0] += Decimal(row['opening']); values[1] += Decimal(row['debit']); values[2] += Decimal(row['credit']); values[3] += 1
            group_id = groups[group_id].parent_id
    party_group_totals = defaultdict(lambda: [ZERO,ZERO,ZERO,0])
    for row in party_totals:
        group_id = row['groupId']; visited = set()
        while group_id in groups and group_id not in visited:
            visited.add(group_id); values = party_group_totals[group_id]
            values[0] += Decimal(row['opening']); values[1] += Decimal(row['debit']); values[2] += Decimal(row['credit']); values[3] += 1
            group_id = groups[group_id].parent_id
    return {'start': str(start), 'end': str(end), 'currency': book.currency,
        'partyGroupSummary':[{'id':g.pk,'name':g.name,'kind':g.kind,'parent':groups[g.parent_id].name if g.parent_id else '',
                         'opening':str(party_group_totals[g.pk][0]),'debit':str(party_group_totals[g.pk][1]),'credit':str(party_group_totals[g.pk][2]),
                         'balance':str(party_group_totals[g.pk][0]+party_group_totals[g.pk][1]-party_group_totals[g.pk][2]),'partyCount':party_group_totals[g.pk][3]}
                        for g in sorted(groups.values(),key=lambda g:g.name) if g.kind in {'asset','liability'}],
        'groupSummary':[{'id':g.pk,'name':g.name,'kind':g.kind,'parent':groups[g.parent_id].name if g.parent_id else '',
                         'opening':str(group_totals[g.pk][0]),'debit':str(group_totals[g.pk][1]),'credit':str(group_totals[g.pk][2]),
                         'balance':str(group_totals[g.pk][0]+group_totals[g.pk][1]-group_totals[g.pk][2]),'ledgerCount':group_totals[g.pk][3]}
                        for g in sorted(groups.values(),key=lambda g:g.name)],
        'costCategorySummary':[{'id':pk,'name':category_names[pk],'income':str(v[0]),'expenses':str(v[1]),'netProfit':str(v[0]-v[1])}
                               for pk,v in category_totals.items()],
        'customerProfit': {'rows':profit_rows,'details':profit_details,'unassignedIncome':str(unassigned_income),
                          'unassignedCosts':str(unassigned_cost),'unassignedNet':str(unassigned_income-unassigned_cost),
                          'companyNetProfit':str(income-expenses)},
        'assignableCosts': assignable, 'orderCommitments':order_commitments(end),
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
