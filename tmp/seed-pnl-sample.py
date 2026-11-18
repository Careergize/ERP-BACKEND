from uuid import uuid5, NAMESPACE_URL
from accounting.models import *
from accounting.services import post
from accounting.detailed_profit_loss import detailed_profit_loss
from django.db import transaction
book=Book.objects.get(pk=1)
with transaction.atomic():
    supplier,_=Party.objects.get_or_create(name='SAMPLE Supplier',kind='supplier')
    customer,_=Party.objects.get_or_create(name='SAMPLE Customer',kind='customer')
    group=AccountGroup.objects.get(name__iexact='Admin & Operation Expenses')
    Account.objects.get_or_create(code='SAMPLE-FUEL',defaults=dict(name='Fuel Expense',kind='expense',group=group,pnl_section='indirect_expense'))
    accounts=list(Account.objects.filter(kind__in=['expense','income'],control=False))
    for a in accounts:
        for i in range(5):
            number=f'SAMPLE-PNL-{a.pk}-{i+1}'
            if Document.objects.filter(number=number).exists(): continue
            amount=str(100*(i+1))
            payload=dict(number=number,commandId=str(uuid5(NAMESPACE_URL,number)),date=f'2026-11-{9+i:02}',notes='Fictional P&L test voucher')
            items=[dict(account=a.pk,description=a.name,quantity='1',price=amount)]
            if a.pk=='4100':
                payload.update(kind='invoice',salePayment='credit' if i%2 else 'cash',cashAccount='1100',party=customer.pk,items=items)
            elif a.kind=='income':
                payload.update(kind='journal',lines=[dict(account=a.pk,credit=amount),dict(account='1100',debit=amount)])
            elif i%2:
                payload.update(kind='bill',purchasePayment='credit',party=supplier.pk,items=items)
            else:
                payload.update(kind='expense',cashAccount='1100',items=items)
            post(payload,book.created_by)
    report=detailed_profit_loss('2026-11-01','2026-11-13')
    fuel=next(a for s in report['sections'] for a in s['accounts'] if a['code']=='SAMPLE-FUEL')
    assert len(fuel['entries'])==5
print(len(accounts),Document.objects.filter(number__startswith='SAMPLE-PNL-').count())
