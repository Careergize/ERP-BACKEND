"""Durable posting queue. Missing cost/FX/tax setup is exposed, never guessed."""
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL
from django.db import transaction, IntegrityError
from rest_framework.exceptions import ValidationError
from .models import Book, Party, PostingEvent
from .services import post, require, money, ZERO


def enqueue(source, payload, user):
    if not Book.objects.exists():
        return
    payload = {**payload, 'commandId':str(uuid5(NAMESPACE_URL, 'navapack-accounting:'+source))}
    event, _ = PostingEvent.objects.get_or_create(source=source, defaults={'payload':payload,'actor':user})
    if event.document_id:
        return
    try:
        with transaction.atomic():
            require(not event.payload.get('blocked'), event.payload.get('blocked',''))
            doc = post(event.payload,user,source=source)
            event.document = doc; event.error = ''; event.save(update_fields=['document','error'])
    except (ValidationError, IntegrityError) as error:
        event.error = str(error.detail) if isinstance(error,ValidationError) else 'A document/source number already exists. Reconcile the duplicate before posting.'
        event.save(update_fields=['error'])


def shared_saved(key, content, previous, user):
    if not Book.objects.exists(): return
    old = {r['id']:r for r in previous or []} if isinstance(previous,list) else {}
    if key == 'invoices':
        from erp.models import WorkflowOrder
        for r in content:
            if r['id'] in old: continue
            order = WorkflowOrder.objects.filter(pk=r['orderId']).first()
            customer = order.data.get('customerId') if order else None
            party = None
            if customer:
                party,_ = Party.objects.get_or_create(source='customer:'+str(customer), defaults={'name':r['buyer'],'kind':'customer'})
            led=Decimal(str(r.get('led') or 0))
            items=[]; total_net=Decimal(str(r['total']))-Decimal(str(r['tax']))-led; remaining=total_net
            subtotal=sum((Decimal(str(i['amount'])) for i in r['items']),ZERO)
            for index,i in enumerate(r['items']):
                net = remaining if index==len(r['items'])-1 else money(total_net*Decimal(str(i['amount']))/subtotal) if subtotal else ZERO
                remaining -= net
                items.append({'item':i['itemId'],'description':i['description'],'quantity':str(i['quantity']),'unit':i['unit'],'price':str(i['rate']),'sourceNet':str(net),'tax':str(r['tax']) if index==0 else '0'})
            if led:
                items.append({'description':'LED levy','quantity':'1','price':'0','sourceNet':'0','tax':str(led),'taxAccount':Book.objects.first().led_account or '2200'})
            payload={'kind':'invoice','number':r['number'],'date':r['date'],'currency':r['currency'],'party':party.pk if party else None,'items':items}
            if not customer: payload['blocked']='Map this invoice to its canonical customer before posting.'
            if led and not Book.objects.first().led_account: payload['blocked']='LED account mapping must be confirmed before posting this invoice.'
            enqueue('invoice:'+r['id'],payload,user)
    if key == 'inventory-ledger':
        prior={r['id']:r for r in (previous or {}).get('dispatches',[])}
        for r in content.get('dispatches',[]):
            if r['status']=='Confirmed' and r['id'] not in prior:
                enqueue('dispatch:'+r['id'],{'kind':'stock-issue','number':'VAL-'+r['number'],'date':r['date'],'item':'finished:'+r['productId'],'unit':r['unit'],'quantity':str(r['quantity']),'contra':'5100'},user)
            elif r['status']=='Cancelled' and prior.get(r['id'],{}).get('status')=='Confirmed':
                enqueue('dispatch-return:'+r['id'],{'blocked':'A returned dispatch requires its original cost and approved return posting.','kind':'stock-receipt','number':'RETURN-'+r['number'],'date':r['date']},user)


def purchase_received(order, user, note=None):
    if not Book.objects.exists(): return
    rows=note.lines.select_related('purchase_line','purchase_line__stock_item') if note else order.lines.select_related('stock_item').all()
    for row in rows:
        line=row.purchase_line if note else row
        if not line.stock_item_id: continue
        qty=row.quantity_received-row.damaged_quantity if note else line.quantity
        if qty <= 0: continue
        source=f'grn:{note.pk}:{row.pk}' if note else f'purchase-receipt:{order.pk}:{line.pk}'
        payload={'kind':'stock-receipt','number':f'VAL-{source}','date':str(note.receipt_date if note else order.order_date),
            'item':'finished:'+str(line.stock_item_id),'unit':line.unit,'quantity':str(qty),
            'value':str(money(qty*line.unit_price)),'contra':'2300','currency':order.currency}
        enqueue(source,payload,user)
