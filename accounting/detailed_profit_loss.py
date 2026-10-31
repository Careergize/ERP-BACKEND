"""Read-only P&L from posted journals; supporting inventory is never re-expensed."""
from collections import defaultdict
from decimal import Decimal
from .api import AccountingAPIView
from .models import AccountGroup, Book, Document, JournalLine, Party, PostingEvent, ValueMovement
from .services import ZERO, day, money, require
from rest_framework.response import Response

SECTIONS = [('sales','Sales accounts'),('cost_of_sales','Cost of goods sold'),('direct_expense','Direct expenses'),
            ('other_income','Indirect income'),('indirect_expense','Indirect expenses'),
            ('unclassified_income','Unclassified income'),('unclassified_expense','Unclassified expenses')]


def section_for(account):
    if account.code == '4100': return 'sales'
    if account.code == '5100': return 'cost_of_sales'
    allowed = {'sales','other_income'} if account.kind == 'income' else {'cost_of_sales','direct_expense','indirect_expense'}
    if account.pnl_section in allowed: return account.pnl_section
    if account.code == '4200' and account.kind == 'income': return 'other_income'
    return 'unclassified_'+account.kind


def attributed_customer(line):
    # An assignment's untagged expense credit must remain unassigned, even
    # though its document has a party. This matches the existing profit report.
    party = line.party if line.party_id else line.document.party if line.account.kind == 'income' else None
    return party if party and party.kind == 'customer' else None


def group_path(account, groups):
    names=[]; seen=set(); key=account.group_id
    while key in groups and key not in seen:
        seen.add(key); group=groups[key]; names.insert(0,group.name); key=group.parent_id
    return ' / '.join(names) or 'Ungrouped'


def detailed_profit_loss(start, end, customer=None):
    start,end=day(start),day(end);require(start<=end,'Start date must be on or before end date.')
    book=Book.objects.first();require(book is not None,'Configure accounting first.')
    selected=None
    if customer not in (None,''):
        require(str(customer).isdigit(),'Select a customer ledger by ID.')
        selected=Party.objects.filter(pk=customer,kind='customer').first()
        require(selected is not None,'Select an existing customer ledger.')
    from erp.models import SharedEntry
    snapshot=SharedEntry.objects.filter(pk='invoices').first()
    payment_types={i['id']:(i.get('dispatchPayment') or {}).get('kind') for i in (snapshot.content if snapshot else [])}
    groups={g.pk:g for g in AccountGroup.objects.all()}
    accounts={}; totals=defaultdict(lambda:ZERO); split=defaultdict(lambda:ZERO)
    unassigned_income=ZERO;unassigned_expense=ZERO
    lines=JournalLine.objects.filter(document__date__range=(start,end),account__kind__in=['income','expense']).select_related(
        'account','party','document__party','document__reversal_of','document__cost_category','document__voucher_type').order_by('document__date','document_id','pk')
    for line in lines:
        party=attributed_customer(line)
        amount=line.credit-line.debit if line.account.kind=='income' else line.debit-line.credit
        if party is None:
            if line.account.kind=='income':unassigned_income+=amount
            else:unassigned_expense+=amount
        if selected and (party is None or party.pk!=selected.pk):continue
        section=section_for(line.account);totals[section]+=amount
        row=accounts.setdefault(line.account_id,{'code':line.account_id,'name':line.account.name,'section':section,
            'group':group_path(line.account,groups),'amount':ZERO,'entries':[]})
        row['amount']+=amount
        original=line.document.reversal_of if line.document.reversal_of_id else line.document
        source=original.source or ''
        payment=payment_types.get(source[len('invoice:'):]) if source.startswith('invoice:') else None
        payment=payment if payment in {'cash','credit'} else 'unclassified'
        if section=='sales':split[payment]+=amount
        row['entries'].append({'line':line.pk,'document':line.document_id,'number':line.document.number,'date':str(line.document.date),
            'kind':line.document.kind,'voucherType':line.document.voucher_type.name if line.document.voucher_type else line.document.kind,
            'category':line.document.cost_category.name if line.document.cost_category else 'Uncategorised',
            'customer':party.name if party else 'Company / unassigned','customerId':party.pk if party else None,
            'debit':str(line.debit),'credit':str(line.credit),'amount':str(amount),'currency':line.document.currency,
            'fx':str(line.document.fx),'paymentType':payment if section=='sales' else ''})
    sections=[]
    for key,label in SECTIONS:
        ledger_rows=[]
        for row in sorted(accounts.values(),key=lambda r:(r['group'],r['code'])):
            if row['section']==key:ledger_rows.append({**row,'amount':str(row['amount'])})
        group_totals=defaultdict(lambda:ZERO)
        for row in ledger_rows:group_totals[row['group']]+=Decimal(row['amount'])
        sections.append({'key':key,'label':label,'total':str(totals[key]),'accounts':ledger_rows,'groups':[{'name':name,'total':str(value)} for name,value in group_totals.items()]})
    gross=totals['sales']-totals['cost_of_sales']-totals['direct_expense']
    income=totals['sales']+totals['other_income']+totals['unclassified_income']
    expenses=totals['cost_of_sales']+totals['direct_expense']+totals['indirect_expense']+totals['unclassified_expense']
    warnings=[]
    if totals['unclassified_income'] or totals['unclassified_expense']:
        warnings.append('Unclassified ledgers affect net profit but are excluded from gross profit. Classify them in Masters → Ledgers for a meaningful gross margin.')
    pending=[]
    for event in PostingEvent.objects.filter(document__isnull=True,cancelled=False):
        when=event.payload.get('date')
        if not when or str(start)<=when<=str(end):
            pending.append({'source':event.source,'error':event.error})
    if pending:warnings.append(f'{len(pending)} company operational event(s) await posting for this period; these statements exclude their missing entries.')
    stock=None;purchases=None
    if selected:
        warnings.append('Customer P&L includes only attributed income and costs. Unassigned company costs are not allocated automatically. Company opening/closing stock and purchases are not treated as customer-owned amounts.')
    else:
        stock=stock_schedule(start,end)
        purchases=purchase_schedule(start,end)
    return {'company':book.company,'currency':book.currency,'start':str(start),'end':str(end),
        'customer':{'id':selected.pk,'name':selected.name} if selected else None,'sections':sections,
        'salesBreakdown':[{'kind':key,'label':label,'amount':str(split[key])} for key,label in [('cash','Cash sales'),('credit','Credit sales'),('unclassified','Sales without recorded cash/credit type')]],
        'totals':{'income':str(income),'expenses':str(expenses),'grossProfit':str(gross),'netProfit':str(income-expenses),'tradingTotal':str(totals['cost_of_sales']+totals['direct_expense']+max(gross,ZERO)),'operatingTotal':str(totals['indirect_expense']+totals['unclassified_expense']+max(-gross,ZERO)+max(income-expenses,ZERO))},
        'companyUnassigned':{'income':str(unassigned_income),'expenses':str(unassigned_expense)},
        'stock':stock,'purchases':purchases,'warnings':warnings,'pendingEvents':pending,
        'basis':'Posted journals in reporting currency, inclusive of dated reversals and customer cost assignments. Perpetual inventory: cost of goods sold is already expensed; stock and purchase schedules are supporting information only.'}


def stock_schedule(start,end):
    from erp.models import FinishedStockItem
    from inventory.models import RawMaterial
    names={'finished:'+str(p.pk):p.name for p in FinishedStockItem.objects.all()}
    names.update({'raw:'+str(p.pk):p.name for p in RawMaterial.objects.all()})
    rows={}
    for m in ValueMovement.objects.filter(document__date__lte=end).select_related('stock','document__reversal_of').order_by('document__date','pk'):
        row=rows.setdefault(m.stock_id,{'item':m.stock_id,'name':names.get(m.stock_id,m.stock_id),'unit':m.stock.unit,
            'openingQuantity':ZERO,'openingValue':ZERO,'receipts':ZERO,'issues':ZERO,'closingQuantity':ZERO,'closingValue':ZERO})
        row['closingQuantity']+=m.quantity;row['closingValue']+=m.value
        if m.document.date<start:
            row['openingQuantity']+=m.quantity;row['openingValue']+=m.value
        else:
            kind=m.document.reversal_of.kind if m.document.reversal_of_id else m.document.kind
            if kind=='stock-receipt':row['receipts']+=m.value
            else:row['issues']-=m.value
    keys=['openingQuantity','openingValue','receipts','issues','closingQuantity','closingValue']
    result=[{**r,**{k:str(r[k]) for k in keys}} for r in sorted(rows.values(),key=lambda r:r['item'])]
    return {'rows':result,**{k:str(sum((r[k] for r in rows.values()),ZERO)) for k in ['openingValue','receipts','issues','closingValue']}}


def purchase_schedule(start,end):
    rows=[];total=ZERO
    for doc in Document.objects.filter(date__range=(start,end)).select_related('party','reversal_of__party').prefetch_related('items','reversal_of__items'):
        original=doc.reversal_of if doc.reversal_of_id else doc
        if original.kind!='bill':continue
        sign=-1 if doc.reversal_of_id else 1
        amount=money(sum((i.net for i in original.items.all()),ZERO)*original.fx*sign);total+=amount
        rows.append({'document':doc.pk,'number':doc.number,'date':str(doc.date),'supplier':original.party.name if original.party else 'Unassigned',
            'amount':str(amount),'currency':original.currency,'fx':str(original.fx)})
    return {'total':str(total),'rows':sorted(rows,key=lambda r:(r['date'],r['document']))}


class DetailedProfitLoss(AccountingAPIView):
    def get(self,request):
        return Response(detailed_profit_loss(request.query_params.get('start'),request.query_params.get('end'),request.query_params.get('customer')))
