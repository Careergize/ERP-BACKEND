"""Approved-order dispatch settlement; caller holds the inventory entry transaction."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from erp.operational_policy import authorize
from erp.operational_records import order_line, quantity, money, commercial_totals, validate_invoices
from erp.models import Customer, SharedEntry, EntryRevision
from .api import AccountingAPIView
from .models import Book, Party, Account, JournalLine, PostingEvent
from .services import require, post
from rest_framework.response import Response


class DispatchSaleOptions(AccountingAPIView):
    def check_access(self, request):
        authorize(request.user, 'dispatch')
        authorize(request.user, 'invoice')

    def get(self, request):
        order_id = request.query_params.get('orderId')
        from erp.models import WorkflowOrder
        key = str(order_id).removeprefix('wf-order-')
        require(key.isdigit(), 'Select an approved Sales Order.')
        row = WorkflowOrder.objects.filter(pk=key).first()
        require(row and row.data.get('items'), 'Select a saved Sales Order.')
        order, _ = order_line(order_id, row.data['items'][0]['id'])
        customer = customer_for(order)
        book = Book.objects.first()
        require(book is not None, 'Configure accounting before cash/credit dispatch.')
        party = Party.objects.filter(source=f'customer:{customer.pk}', kind='customer').first()
        totals = JournalLine.objects.filter(party=party, account_id='1300', document__date__lte=timezone.localdate()).aggregate(debit=Sum('debit'), credit=Sum('credit')) if party else {}
        balance = (totals.get('debit') or Decimal(0)) - (totals.get('credit') or Decimal(0))
        return Response({'partyName':party.name if party else customer.name, 'partyId':party.pk if party else None,
            'balance':str(balance), 'reportingCurrency':book.currency, 'asOf':str(timezone.localdate()),
            'pendingPostings':PostingEvent.objects.filter(document__isnull=True,cancelled=False).count(),
            'cashAccounts':list(Account.objects.filter(cash=True,control=False,kind='asset').order_by('code').values('code','name'))})


def customer_for(order):
    key = str(order.get('customerId','')).removeprefix('db:')
    customer = Customer.objects.filter(pk=key,is_active=True).first() if key.isdigit() else None
    require(customer is not None, 'The approved order must link to an active ERP customer before cash/credit dispatch.')
    return customer


def settle_dispatch_sales(content, previous, user):
    old = {d['id']:d for d in (previous or {}).get('dispatches',[])}
    groups = {}
    for dispatch in content.get('dispatches',[]):
        sale = dispatch.get('sale') or {}
        if dispatch['id'] not in old and sale.get('payment') is not None:
            authorize(user, 'invoice')
            identity = str(serializers.UUIDField().run_validation(sale.get('id')))
            groups.setdefault(identity,[]).append(dispatch)
    if not groups: return
    book = Book.objects.select_for_update().filter(pk=1).first()
    require(book is not None, 'Configure accounting before cash/credit dispatch.')
    entry = SharedEntry.objects.select_for_update().filter(pk='invoices').first()
    invoices = deepcopy(entry.content) if entry else []
    previous_invoices = deepcopy(invoices)
    for identity, dispatches in groups.items():
        first = dispatches[0]; sale = first['sale']; payment = sale['payment']
        require(isinstance(payment,dict) and payment.get('kind') in {'cash','credit'}, 'Choose Cash sale or Credit sale.')
        require(all(d.get('sale') == sale and d['orderId'] == first['orderId'] and d['date'] == first['date'] for d in dispatches), 'A sale must use one order, date and payment choice.')
        require(not any((d.get('sale') or {}).get('id') == identity for d in old.values()), 'This sale ID was already dispatched. Reload before starting another sale.')
        order, _ = order_line(first['orderId'], first['itemId'])
        customer = customer_for(order)
        party, _ = Party.objects.get_or_create(source=f'customer:{customer.pk}',defaults={'name':customer.name,'kind':'customer'})
        require(party.kind == 'customer', 'The ERP customer link is not a customer ledger.')
        require(not any(set(i.get('dispatchIds',[])) & {d['id'] for d in dispatches} for i in invoices), 'A dispatch cannot be invoiced twice.')
        lines = []
        items = []
        for source in order['items']:
            amount = sum((quantity(d['quantity'],True) for d in dispatches if d['itemId'] == source['id']), Decimal(0))
            if not amount: continue
            line = {**deepcopy(source),'quantity':float(amount)}
            if source.get('ledSnapshot'):
                line['ledSnapshot']['selectedAmount'] = float(money(quantity(source['ledSnapshot']['selectedAmount']) * amount / quantity(source['quantity'],True)))
            lines.append(line)
            items.append({'itemId':source['id'],'description':source['itemName'],'quantity':float(amount),'unit':source['unit'],'rate':source['unitPrice'],'amount':float(money(amount*quantity(source['unitPrice']))), **({'ledSnapshot':line['ledSnapshot']} if line.get('ledSnapshot') else {})})
        pricing = order.get('commercialPricing') or {'taxMode':'exclusive','discount':0}
        rate = quantity(order.get('taxRate',0)); mode = pricing['taxMode']
        full_subtotal, full_tax, full_total, full_led = commercial_totals(order['items'],quantity(pricing['discount']),rate,mode)
        prior = [i for i in invoices if i['orderId'] == order['id']]
        subtotal = sum((money(quantity(l['quantity'])*quantity(l['unitPrice'])) for l in lines),Decimal(0))
        final = all(sum((quantity(i['quantity']) for inv in prior for i in inv['items'] if i['itemId']==l['id']),Decimal(0)) + sum((quantity(i['quantity']) for i in lines if i['id']==l['id']),Decimal(0)) == quantity(l['quantity']) for l in order['items'])
        remaining_discount = quantity(pricing['discount']) - sum((quantity(i.get('quotationDiscount',0)) for i in prior),Decimal(0))
        discount = money(remaining_discount if final else min(money(quantity(pricing['discount'])*subtotal/full_subtotal) if full_subtotal else Decimal(0),remaining_discount))
        subtotal, tax, total, led = commercial_totals(lines,discount,rate,mode)
        if final:
            tax = money(full_tax-sum((quantity(i['tax']) for i in prior),Decimal(0)))
            total = money(full_total-sum((quantity(i['total']) for i in prior),Decimal(0)))
            led = money(full_led-sum((quantity(i.get('led',0)) for i in prior),Decimal(0)))
        require(total > 0, 'Cash/credit dispatch requires a positive invoice total.')
        invoice = {'id':'dispatch-sale-'+identity,'number':'DS-'+identity,'orderId':order['id'],'orderNumber':order['orderNumber'],
            'date':first['date'],'dispatchIds':[d['id'] for d in dispatches],'dispatchNumbers':[d['number'] for d in dispatches],
            **{key:str(payment.get(key,''))[:250] for key in ['seller','sellerAddress','sellerTax','email','contact','billingAddress','buyerTax']},
            'buyer':order['customerName'],'deliveryAddress':first['destination'],'destination':first['destination'],
            'po':order.get('requisitionOrder',''),'challans':', '.join(d['challan'] for d in dispatches),
            'paymentTerms':'Paid at dispatch' if payment['kind']=='cash' else 'Credit sale',
            'currency':order['currency'],'taxRate':float(rate),'taxMode':mode,'discount':float(discount),'quotationDiscount':float(discount),
            'subtotal':float(subtotal),'tax':float(tax),'led':float(led),'total':float(total),'items':items,
            'dispatchPayment':{'kind':payment['kind'],'fx':payment.get('fx','1')}}
        validate_invoices([*invoices,invoice],invoices,user)
        from .integration import shared_saved
        shared_saved('invoices',[*invoices,invoice],invoices,user)
        event = PostingEvent.objects.get(source='invoice:'+invoice['id'])
        require(event.document_id is not None, 'Invoice could not post: '+event.error)
        if payment['kind'] == 'cash':
            receipt = post({'commandId':str(uuid5(NAMESPACE_URL,'dispatch-receipt:'+identity)), 'kind':'receipt',
                'number':'DR-'+identity,'date':first['date'],'party':party.pk,'currency':order['currency'],'fx':payment.get('fx','1'),
                'cashAccount':payment.get('cashAccount'),'amount':str(total),'allocations':[{'bill':event.document_id,'amount':str(total)}]},user,source='dispatch-receipt:'+identity)
            invoice['dispatchPayment']['receiptId'] = receipt.pk
        invoices.append(invoice)
    version = (entry.version if entry else 0)+1
    if entry:
        entry.content=invoices;entry.version=version;entry.updated_by=user;entry.save()
    else: entry=SharedEntry.objects.create(key='invoices',content=invoices,version=version,updated_by=user)
    digest = hashlib.sha256(json.dumps(invoices,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    EntryRevision.objects.create(entry=entry,version=version,command_id=uuid5(NAMESPACE_URL,'dispatch-invoices:'+':'.join(sorted(groups))),content=invoices,content_hash=digest,actor=user)
