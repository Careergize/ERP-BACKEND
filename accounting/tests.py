from copy import deepcopy
from uuid import uuid4
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import Account, Document, JournalLine, Allocation, StockValue, PostingEvent
from .integration import enqueue


class AccountingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin=get_user_model().objects.create_user('finance-admin',role='admin',assigned_role=Role.objects.get(code='admin'))
        self.accounts=get_user_model().objects.create_user('finance-clerk',role='accounts',assigned_role=Role.objects.get(code='accounts'))
        self.client.force_authenticate(self.admin)
        r=self.client.post('/api/accounting/setup/',{'company':'Synthetic company','currency':'UGX','startDate':'2026-01-01','fiscalMonth':1,'valuation':'weighted-average'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.customer=self.master('customer','Customer A');self.supplier=self.master('supplier','Supplier A')

    def master(self,kind,name):
        r=self.client.post('/api/accounting/masters/parties/',{'kind':kind,'name':name},format='json')
        self.assertEqual(r.status_code,200,r.data)
        return r.data['id']

    def command(self,kind,**values):
        p={'commandId':str(uuid4()),'kind':kind,'number':'SYN-'+uuid4().hex,'date':'2026-02-01','currency':'UGX',**values}
        return self.client.post('/api/accounting/commands/',p,format='json')

    def invoice(self,kind='invoice',**values):
        return self.command(kind,party=self.customer if kind=='invoice' else self.supplier,
            items=[{'description':'Test item','item':'test','quantity':'2','unit':'kg','price':'100','tax':'20','account':'6100'}],**values)

    def report(self,**params):
        r=self.client.get('/api/accounting/reports/',{'start':'2026-02-01','end':'2026-02-28',**params})
        self.assertEqual(r.status_code,200,r.data)
        return r.data

    def test_sales_receipt_partial_balance_ledgers_and_statements(self):
        inv=self.invoice();self.assertEqual(inv.status_code,201,inv.data)
        receipt=self.command('receipt',party=self.customer,amount='70',cashAccount='1100',allocations=[{'bill':inv.data['id'],'itemLine':inv.data['items'][0]['id'],'amount':'70'}])
        self.assertEqual(receipt.status_code,201,receipt.data)
        r=self.report(party=self.customer)
        self.assertEqual(r['profitLoss']['income'],'200.00')
        self.assertEqual(r['ledgerClosing'],'150.00')
        self.assertEqual(r['trialDifference'],'0.00')
        self.assertEqual(r['balanceSheet']['difference'],'0.00')
        self.assertEqual(r['cashFlow'],{'operating':'70.00'})
        row=next(d for d in r['documents'] if d['kind']=='invoice')
        self.assertEqual(row['outstanding'],'150.00')
        self.assertEqual(receipt.data['allocations'][0]['items'],['Test item'])

    def test_supplier_bill_payment_expense_cash_transfer(self):
        bill=self.invoice('bill');self.assertEqual(bill.status_code,201,bill.data)
        r=self.command('payment',party=self.supplier,amount='100',cashAccount='1200',allocations=[{'bill':bill.data['id'],'amount':'100'}])
        self.assertEqual(r.status_code,201,r.data)
        r=self.command('expense',cashAccount='1100',items=[{'description':'Transport','quantity':'1','price':'30','tax':'0','account':'6100'}])
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.command('transfer',amount='10',fromAccount='1100',toAccount='1200').status_code,201)
        data=self.report(party=self.supplier)
        self.assertEqual(data['ledgerClosing'],'-120.00')
        self.assertEqual(data['profitLoss']['expenses'],'230.00')
        self.assertEqual(data['cashFlow'],{'operating':'-130.00'})

    def test_allocation_bounds_and_wrong_party_roll_back(self):
        inv=self.invoice();pk=inv.data['id'];before=Document.objects.count()
        for allocations in [[{'bill':pk,'amount':'221'}],[{'bill':pk,'amount':'100'},{'bill':pk,'amount':'150'}],[{'bill':pk,'itemLine':99999,'amount':'10'}]]:
            r=self.command('receipt',party=self.customer,amount='300',cashAccount='1100',allocations=allocations)
            self.assertEqual(r.status_code,400,r.data)
        self.assertEqual(Document.objects.count(),before)
        self.assertEqual(Allocation.objects.count(),0)
        other=self.master('customer','Other')
        self.assertEqual(self.command('receipt',party=other,amount='20',cashAccount='1100',allocations=[{'bill':pk,'amount':'20'}]).status_code,400)

    def test_retry_idempotency_and_changed_request_rejected(self):
        payload={'commandId':str(uuid4()),'kind':'expense','number':'RETRY','date':'2026-02-01','cashAccount':'1100','items':[{'description':'Fee','quantity':'1','price':'10'}]}
        r=self.client.post('/api/accounting/commands/',payload,format='json');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.client.post('/api/accounting/commands/',payload,format='json').data['id'],r.data['id'])
        self.assertEqual(Document.objects.count(),1)
        self.assertEqual(self.client.post('/api/accounting/commands/',{**payload,'notes':'Changed'},format='json').status_code,400)

    def test_payment_reversal_restores_outstanding_and_historical_balances(self):
        inv=self.invoice()
        receipt=self.command('receipt',date='2026-02-10',party=self.customer,amount='100',cashAccount='1100',allocations=[{'bill':inv.data['id'],'amount':'100'}])
        self.assertEqual(self.command('reverse',document=inv.data['id'],notes='Wrong invoice',date='2026-02-11').status_code,400)
        r=self.command('reverse',document=receipt.data['id'],notes='Receipt reversed',date='2026-02-15');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.report(party=self.customer)['ledgerClosing'],'220.00')
        historical=self.report(end='2026-02-12',party=self.customer)
        self.assertEqual(historical['ledgerClosing'],'120.00')
        self.assertEqual(self.command('reverse',document=receipt.data['id'],notes='Again',date='2026-02-16').status_code,400)

    def test_fx_gain_and_missing_fx(self):
        inv=self.invoice(currency='USD',fx='2');self.assertEqual(inv.status_code,201,inv.data)
        self.assertEqual(self.invoice(currency='USD').status_code,400)
        r=self.command('receipt',currency='USD',fx='3',party=self.customer,amount='220',cashAccount='1100',allocations=[{'bill':inv.data['id'],'amount':'220'}])
        self.assertEqual(r.status_code,201,r.data)
        data=self.report(party=self.customer)
        self.assertEqual(data['ledgerClosing'],'0.00')
        self.assertEqual(data['profitLoss']['income'],'620.00')
        self.assertEqual(data['trialDifference'],'0.00')

    def test_valuation_weighted_average_and_insufficient_stock(self):
        for qty,value in [('10','100'),('10','200')]:
            r=self.command('stock-receipt',item='raw:test',unit='kg',quantity=qty,value=value,contra='3100');self.assertEqual(r.status_code,201,r.data)
        r=self.command('stock-issue',item='raw:test',unit='kg',quantity='5',date='2026-02-02',contra='5100');self.assertEqual(r.status_code,201,r.data)
        stock=StockValue.objects.get(pk='raw:test');self.assertEqual(stock.quantity,Decimal('15'));self.assertEqual(stock.value,Decimal('225'))
        self.assertEqual(self.report()['profitLoss']['expenses'],'75.00')
        self.assertEqual(self.command('stock-issue',item='raw:test',unit='kg',quantity='16',date='2026-02-03').status_code,400)
        self.assertEqual(self.command('stock-receipt',item='raw:test',unit='kg',quantity='1',value='10',date='2026-02-01').status_code,400)
        self.assertEqual(self.command('stock-issue',item='raw:test',unit='pcs',quantity='1',date='2026-02-03').status_code,400)

    def test_opening_and_period_lock(self):
        r=self.command('opening',date='2026-01-01',lines=[{'account':'1100','debit':'100','credit':'0'},{'account':'3100','debit':'0','credit':'100'}]);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.report(account='1100')['ledgerOpening'],'100.00')
        r=self.command('opening',date='2026-01-01',lines=[{'account':'1100','debit':'100','credit':'0'},{'account':'3100','debit':'0','credit':'90'}]);self.assertEqual(r.status_code,400,r.data)
        self.assertEqual(self.client.post('/api/accounting/close-period/',{'date':'2026-02-28'},format='json').status_code,200)
        self.assertEqual(self.invoice().status_code,400)
        self.assertEqual(self.invoice(date='2026-03-01').status_code,201)

    def test_queue_retains_missing_value_then_posts_exactly_once(self):
        enqueue('raw:syn',{'kind':'stock-receipt','number':'RAW-SYN','date':'2026-02-01','item':'raw:test','quantity':'2','unit':'kg','contra':'2300'},self.admin)
        self.assertEqual(Document.objects.count(),0)
        q=PostingEvent.objects.get(pk='raw:syn');self.assertTrue(q.error)
        r=self.client.post('/api/accounting/posting-queue/',{'source':q.pk,'value':'30'},format='json');self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(StockValue.objects.get(pk='raw:test').value,Decimal('30'))
        self.assertEqual(self.client.post('/api/accounting/posting-queue/',{'source':q.pk,'value':'30'},format='json').status_code,400)

    def test_permissions_inactive_roles_and_setup_immutability(self):
        self.client.force_authenticate(self.accounts)
        self.assertEqual(self.client.get('/api/accounting/setup/').status_code,200)
        self.assertEqual(self.client.post('/api/accounting/close-period/',{'date':'2026-02-28'},format='json').status_code,403)
        self.assertEqual(self.invoice().status_code,201)
        store=get_user_model().objects.create_user('finance-store',role='store_keeper',assigned_role=Role.objects.get(code='store_keeper'))
        self.client.force_authenticate(store)
        self.assertEqual(self.client.get('/api/accounting/setup/').status_code,403)
        self.assertEqual(self.invoice().status_code,403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/accounting/setup/').status_code,401)
