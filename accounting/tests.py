from copy import deepcopy
from uuid import uuid4
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import Account, Document, JournalLine, Allocation, StockValue, PostingEvent
from .integration import enqueue, shared_saved


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

    def test_saved_invoice_auto_posting_and_foreign_queue(self):
        from erp.models import WorkflowOrder
        order=WorkflowOrder.objects.create(request_id=uuid4(),created_by=self.admin,data={'customerId':'customer-123'})
        row={'id':'source-invoice','orderId':order.pk,'buyer':'Source customer','number':'SOURCE-INV','date':'2026-02-01',
             'currency':'UGX','total':108,'tax':8,'items':[{'itemId':'p1','description':'Source item','quantity':1,'unit':'kg','rate':120,'amount':120}]}
        shared_saved('invoices',[row],None,self.admin)
        doc=Document.objects.get(source='invoice:source-invoice')
        self.assertEqual(doc.total,Decimal('108'))
        self.assertEqual(doc.items.get().net,Decimal('100'))
        shared_saved('invoices',[row],None,self.admin)
        self.assertEqual(Document.objects.filter(source=doc.source).count(),1)
        other={**row,'id':'foreign-source','number':'SOURCE-FOREIGN','currency':'USD'}
        shared_saved('invoices',[other],None,self.admin)
        self.assertTrue(PostingEvent.objects.get(pk='invoice:foreign-source').error)

    def test_supplier_bill_matches_po_partial_and_rejects_overbilling(self):
        from erp.models import PurchaseOrder, PurchaseOrderLine
        order=PurchaseOrder.objects.create(request_id=uuid4(),supplier_name='Supplier A',order_date='2026-02-01',currency='UGX',created_by=self.admin,receipt_method='grn')
        line=PurchaseOrderLine.objects.create(order=order,description='Purchase item',unit='kg',quantity='10',unit_price='5')
        p={'commandId':str(uuid4()),'purchaseOrder':order.pk,'number':'SUPPLIER-001','date':'2026-02-02','party':self.supplier,'fx':'1','quantities':{str(line.pk):'4'}}
        r=self.client.post('/api/accounting/purchase-bills/',p,format='json');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['total'],'20.00')
        self.assertEqual(self.client.post('/api/accounting/purchase-bills/',p,format='json').status_code,200)
        changed=deepcopy(p);changed['quantities'][str(line.pk)]='5'
        self.assertEqual(self.client.post('/api/accounting/purchase-bills/',changed,format='json').status_code,400)
        changed.update(commandId=str(uuid4()),number='SUPPLIER-002');changed['quantities'][str(line.pk)]='7'
        self.assertEqual(self.client.post('/api/accounting/purchase-bills/',changed,format='json').status_code,400)
        result=self.client.get('/api/accounting/purchase-bills/').data
        self.assertEqual(Decimal(result[0]['lines'][0]['remaining']),Decimal('6'))

    def test_raw_movement_signal_captures_missing_cost(self):
        from inventory.models import RawMaterial, RawMovement
        material=RawMaterial.objects.create(id='finance-raw',name='Synthetic material',family='Test',category='virgin',unit='kg',opening_stock=0,opening_date='2026-01-01')
        RawMovement.objects.create(id='finance-move',material=material,date='2026-02-01',movement_type='IN',quantity='5',created_by=self.admin)
        e=PostingEvent.objects.get(pk='raw-movement:finance-move')
        self.assertIsNone(e.document_id);self.assertTrue(e.error)

    def test_historical_outstanding_and_valuation_are_as_of_end(self):
        inv=self.invoice()
        self.command('receipt',date='2026-03-01',party=self.customer,amount='220',cashAccount='1100',allocations=[{'bill':inv.data['id'],'amount':'220'}])
        self.command('stock-receipt',item='raw:test',unit='kg',quantity='10',value='100',contra='3100')
        self.command('stock-issue',item='raw:test',unit='kg',quantity='5',date='2026-03-01')
        data=self.report()
        self.assertEqual(next(d for d in data['documents'] if d['kind']=='invoice')['outstanding'],'220.00')
        self.assertEqual(Decimal(data['inventory'][0]['quantity']),Decimal('10'))

    def test_advance_can_be_allocated_later_and_reversed_with_fx(self):
        receipt=self.command('receipt',date='2026-02-01',currency='USD',fx='3',party=self.customer,amount='220',cashAccount='1100')
        inv=self.invoice(currency='USD',fx='2',date='2026-02-05')
        r=self.command('allocate',date='2026-02-06',currency='USD',fx='3',party=self.customer,paymentDocument=receipt.data['id'],allocations=[{'bill':inv.data['id'],'amount':'220'}])
        self.assertEqual(r.status_code,201,r.data)
        historical=next(d for d in self.report(end='2026-02-03')['documents'] if d['id']==receipt.data['id'])
        self.assertEqual(Decimal(historical['unallocated']),Decimal('220'))
        self.assertEqual(historical['allocations'],[])
        self.assertEqual(self.report(party=self.customer)['ledgerClosing'],'0.00')
        self.assertEqual(self.command('allocate',date='2026-02-07',currency='USD',fx='3',party=self.customer,paymentDocument=receipt.data['id'],allocations=[{'bill':inv.data['id'],'amount':'1'}]).status_code,400)
        reverse=self.command('reverse',date='2026-02-10',document=receipt.data['id'],notes='Advance refunded')
        self.assertEqual(reverse.status_code,201,reverse.data)
        self.assertEqual(self.report(party=self.customer)['ledgerClosing'],'440.00')
        self.assertEqual(self.report()['profitLoss']['income'],'400.00')

    def test_journal_evidence_cannot_be_edited_and_led_requires_mapping(self):
        doc=self.invoice();line=JournalLine.objects.filter(document_id=doc.data['id']).first()
        with self.assertRaises(ValueError):JournalLine.objects.filter(pk=line.pk).update(debit=999)
        line.debit=999
        with self.assertRaises(ValueError):line.save()
        original=Document.objects.get(pk=doc.data['id']);original.total=99
        with self.assertRaises(ValueError):original.save()
        r=self.client.post('/api/accounting/tax-mapping/',{'ledAccount':'2200'},format='json')
        self.assertEqual(r.status_code,200,r.data)

    def test_cogs_reversal_returns_original_cost(self):
        self.command('stock-receipt',item='finished:test',unit='pcs',quantity='10',value='100',contra='3100')
        issue=self.command('stock-issue',item='finished:test',unit='pcs',quantity='5',date='2026-02-02')
        r=self.command('reverse',document=issue.data['id'],date='2026-02-03',notes='Customer returned goods')
        self.assertEqual(r.status_code,201,r.data)
        s=StockValue.objects.get(pk='finished:test');self.assertEqual(s.quantity,Decimal('10'));self.assertEqual(s.value,Decimal('100'))
        self.assertEqual(self.report()['profitLoss']['expenses'],'0.00')

    def test_dispatch_integration_posts_and_cancels_original_cost_once(self):
        self.command('stock-receipt',item='finished:test',unit='pcs',quantity='10',value='100',contra='3100')
        d={'id':'dispatch-syn','number':'DSP-SYN','date':'2026-02-02','productId':'test','unit':'pcs','quantity':5,'status':'Confirmed'}
        shared_saved('inventory-ledger',{'dispatches':[d],'movements':[]},None,self.admin)
        self.assertIsNotNone(PostingEvent.objects.get(pk='dispatch:dispatch-syn').document_id)
        cancelled={**d,'status':'Cancelled'}
        state={'dispatches':[cancelled],'movements':[{'id':'reverse-dispatch-syn','dispatchId':d['id'],'reversalOf':'out-dispatch-syn','date':'2026-02-03','remarks':'Returned'}]}
        shared_saved('inventory-ledger',state,{'dispatches':[d],'movements':[]},self.admin)
        shared_saved('inventory-ledger',state,{'dispatches':[d],'movements':[]},self.admin)
        self.assertEqual(StockValue.objects.get(pk='finished:test').quantity,Decimal('10'))
        self.assertEqual(Document.objects.filter(source='dispatch-return:dispatch-syn').count(),1)

    def test_cancel_before_costing_removes_pending_dispatch_without_posting(self):
        d={'id':'dispatch-missing','number':'DSP-MISSING','date':'2026-02-02','productId':'unknown','unit':'pcs','quantity':5,'status':'Confirmed'}
        shared_saved('inventory-ledger',{'dispatches':[d]},None,self.admin)
        shared_saved('inventory-ledger',{'dispatches':[{**d,'status':'Cancelled'}]},{'dispatches':[d]},self.admin)
        self.assertEqual(Document.objects.count(),0)
        self.assertEqual(self.client.get('/api/accounting/posting-queue/').data,[])

    def test_customer_profit_uses_actual_costs_fx_and_reconciles_company(self):
        self.invoice(currency='USD',fx='2')
        self.command('receipt',party=self.customer,amount='50',cashAccount='1100')
        self.command('stock-receipt',item='finished:profit',unit='pcs',quantity='10',value='100',contra='3100')
        issue=self.command('stock-issue',item='finished:profit',unit='pcs',quantity='5',customer=self.customer)
        self.assertEqual(issue.status_code,201,issue.data)
        expense=self.command('expense',customer=self.customer,cashAccount='1200',items=[{'description':'Customer transport','quantity':'1','price':'20','tax':'0'}])
        self.assertEqual(expense.status_code,201,expense.data)
        self.invoice('bill')
        data=self.report()['customerProfit']
        customer=next(r for r in data['rows'] if r['customerId']==self.customer)
        self.assertEqual(Decimal(customer['income']),Decimal('400'))
        self.assertEqual(Decimal(customer['costOfSales']),Decimal('50'))
        self.assertEqual(Decimal(customer['otherCosts']),Decimal('20'))
        self.assertEqual(Decimal(customer['netProfit']),Decimal('330'))
        self.assertEqual(Decimal(data['unassignedCosts']),Decimal('200'))
        self.assertEqual(sum((Decimal(r['netProfit']) for r in data['rows']),Decimal(0))+Decimal(data['unassignedNet']),Decimal(data['companyNetProfit']))
        reverse=self.command('reverse',document=issue.data['id'],notes='Return at original cost',date='2026-03-01')
        self.assertEqual(reverse.status_code,201,reverse.data)
        self.assertEqual(Decimal(next(r for r in self.report()['customerProfit']['rows'] if r['customerId']==self.customer)['costOfSales']),Decimal('50'))
        march=self.report(start='2026-03-01',end='2026-03-31')['customerProfit']
        self.assertEqual(Decimal(next(r for r in march['rows'] if r['customerId']==self.customer)['costOfSales']),Decimal('-50'))

    def test_cost_assignment_splits_caps_replays_and_preserves_cash(self):
        expense=self.command('expense',cashAccount='1100',items=[{'description':'Shared delivery','quantity':'1','price':'100'}])
        line=JournalLine.objects.get(document_id=expense.data['id'],account_id='6100')
        other=self.master('customer','Customer B')
        payload={'commandId':str(uuid4()),'kind':'assign-cost','number':'ASSIGN-1','date':'2026-02-04','sourceLine':line.pk,'customer':self.customer,'amount':'60','notes':'Delivery allocation'}
        first=self.client.post('/api/accounting/commands/',payload,format='json')
        self.assertEqual(first.status_code,201,first.data)
        self.assertEqual(self.client.post('/api/accounting/commands/',payload,format='json').data['id'],first.data['id'])
        self.assertEqual(self.command('assign-cost',sourceLine=line.pk,customer=other,amount='41',notes='Too much',date='2026-02-05').status_code,400)
        self.assertEqual(self.command('assign-cost',sourceLine=line.pk,customer=other,amount='40',notes='Remaining',date='2026-02-05').status_code,201)
        data=self.report()
        self.assertEqual(Decimal(data['profitLoss']['expenses']),Decimal('100'))
        self.assertEqual(Decimal(data['customerProfit']['unassignedCosts']),Decimal('0'))
        self.assertEqual(data['cashFlow'],{'operating':'-100.00'})
        self.assertFalse(any(r['line']==line.pk for r in data['assignableCosts']))
        self.assertEqual(self.command('reverse',document=expense.data['id'],notes='Source reversal',date='2026-02-06').status_code,400)
        self.assertEqual(self.command('reverse',document=first.data['id'],notes='Allocation corrected',date='2026-03-01').status_code,201)
        self.assertEqual(Decimal(self.report()['customerProfit']['unassignedCosts']),Decimal('0'))
        self.assertEqual(Decimal(self.report(start='2026-01-01',end='2026-03-31')['customerProfit']['unassignedCosts']),Decimal('60'))
        self.assertEqual(self.command('assign-cost',sourceLine=line.pk,customer=other,amount='60',notes='Cannot backdate after March reversal',date='2026-02-20').status_code,400)

    def test_cost_assignment_validates_customer_date_source_currency_and_permissions(self):
        expense=self.command('expense',cashAccount='1100',items=[{'description':'Test','quantity':'1','price':'20'}])
        line=JournalLine.objects.get(document_id=expense.data['id'],account_id='6100')
        base={'sourceLine':line.pk,'customer':self.customer,'amount':'10','notes':'Reviewed split','date':'2026-02-02'}
        for change in [{'customer':self.supplier},{'date':'2026-01-30'},{'currency':'USD','fx':'2'},{'notes':''},{'amount':'0'},{'sourceLine':999999}]:
            r=self.command('assign-cost',**{**base,**change});self.assertEqual(r.status_code,400,r.data)
        self.client.force_authenticate(self.accounts)
        self.assertEqual(self.command('assign-cost',**base).status_code,201)
        self.client.force_authenticate(self.admin)
        self.client.post('/api/accounting/close-period/',{'date':'2026-02-02'},format='json')
        self.assertEqual(self.command('assign-cost',**base).status_code,400)
        self.admin.is_active=False;self.admin.save()
        self.assertEqual(self.command('assign-cost',**{**base,'date':'2026-02-03'}).status_code,403)

    def test_payment_drilldown_exposes_real_accounts_and_transfer_sides(self):
        Account.objects.create(code='1210',name='MTN Mobile Money',kind='asset',cash=True)
        receipt=self.command('receipt',party=self.customer,amount='40',cashAccount='1210')
        self.assertEqual(receipt.status_code,201,receipt.data)
        cash=[r for r in receipt.data['journal'] if r['cash']]
        self.assertEqual(cash[0]['accountName'],'MTN Mobile Money')
        self.assertEqual(Decimal(cash[0]['debit']),Decimal('40'))
        transfer=self.command('transfer',amount='10',fromAccount='1210',toAccount='1200')
        self.assertEqual({r['account'] for r in transfer.data['journal'] if r['cash']},{'1210','1200'})
        reverse=self.command('reverse',document=receipt.data['id'],date='2026-02-02',notes='Refund')
        self.assertEqual(Decimal(next(r for r in reverse.data['journal'] if r['cash'])['credit']),Decimal('40'))

    def test_uninvoiced_sales_partial_future_currency_and_units(self):
        from erp.models import WorkflowOrder, SharedEntry
        order=WorkflowOrder.objects.create(request_id=uuid4(),created_by=self.admin,data={
            'orderNumber':'SO-COMMIT','customerName':'Customer A','customerId':'canonical-a','date':'2026-01-15','status':'Approved','currency':'USD',
            'items':[{'id':'line-a','itemName':'Bags','unit':'pcs','quantity':'10','unitPrice':'5'},
                     {'id':'line-b','itemName':'Material','unit':'kg','quantity':'2','unitPrice':'8'}]})
        rows=[{'id':'i1','number':'INV-COMMIT','date':'2026-02-05','orderId':f'wf-order-{order.pk}','items':[{'itemId':'line-a','unit':'pcs','quantity':'4'}]},
              {'id':'future','date':'2026-03-01','orderId':str(order.pk),'items':[{'itemId':'line-a','unit':'pcs','quantity':'6'}]},
              {'id':'draft','date':'2026-02-01','status':'Draft','orderId':str(order.pk),'items':[{'itemId':'line-b','unit':'kg','quantity':'2'}]}]
        SharedEntry.objects.create(key='invoices',content=rows,updated_by=self.admin)
        data=self.report()['orderCommitments']
        self.assertEqual(len(data['sales']),2)
        a=next(r for r in data['sales'] if r['line']=='line-a')
        self.assertEqual(Decimal(a['remaining']),Decimal('6'))
        self.assertEqual(Decimal(a['remainingValue']),Decimal('30'))
        self.assertEqual(data['totals'],[{'kind':'sales','currency':'USD','remainingValue':'46.00'}])
        self.assertEqual(Document.objects.count(),0)
        later=self.report(end='2026-03-31')['orderCommitments']
        self.assertEqual(len(later['sales']),1)

    def test_purchase_commitments_follow_partial_bills_and_asof_reversal(self):
        from erp.models import PurchaseOrder, PurchaseOrderLine
        order=PurchaseOrder.objects.create(request_id=uuid4(),supplier_name='Supplier A',order_date='2026-01-15',currency='USD',created_by=self.admin,receipt_method='grn')
        line=PurchaseOrderLine.objects.create(order=order,description='Goods',unit='kg',quantity='10',unit_price='5')
        bill=self.client.post('/api/accounting/purchase-bills/',{'commandId':str(uuid4()),'purchaseOrder':order.pk,'number':'COMMIT-BILL','date':'2026-02-01','party':self.supplier,'fx':'2','quantities':{str(line.pk):'4'}},format='json')
        self.assertEqual(bill.status_code,201,bill.data)
        row=self.report()['orderCommitments']['purchases'][0]
        self.assertEqual(Decimal(row['remaining']),Decimal('6'))
        self.assertEqual(row['currency'],'USD')
        self.assertEqual(Decimal(row['remainingValue']),Decimal('30'))
        self.command('reverse',document=bill.data['id'],date='2026-03-01',notes='Bill correction')
        self.assertEqual(Decimal(self.report()['orderCommitments']['purchases'][0]['remaining']),Decimal('6'))
        self.assertEqual(Decimal(self.report(end='2026-03-31')['orderCommitments']['purchases'][0]['remaining']),Decimal('10'))

    def test_dispatch_cost_automatically_uses_canonical_order_customer(self):
        from erp.models import WorkflowOrder, Customer
        canonical=Customer.objects.create(name='Dispatch customer')
        order=WorkflowOrder.objects.create(request_id=uuid4(),created_by=self.admin,data={'customerId':str(canonical.pk)})
        self.command('stock-receipt',item='finished:auto',unit='pcs',quantity='10',value='100',contra='3100')
        d={'id':'dispatch-linked','number':'DSP-LINKED','date':'2026-02-02','orderId':f'wf-order-{order.pk}','productId':'auto','unit':'pcs','quantity':5,'status':'Confirmed'}
        shared_saved('inventory-ledger',{'dispatches':[d],'movements':[]},None,self.admin)
        event=PostingEvent.objects.get(pk='dispatch:dispatch-linked')
        self.assertIsNotNone(event.document_id,event.error)
        cost=event.document.journal.get(account_id='5100')
        self.assertEqual(cost.party.source,f'customer:{canonical.pk}')
        self.assertEqual(Decimal(next(r for r in self.report()['customerProfit']['rows'] if r['customerId']==cost.party_id)['costOfSales']),Decimal('50'))
