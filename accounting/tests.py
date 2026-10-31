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

    def create_finance_master(self,master_kind,**payload):
        response=self.client.post(f'/api/accounting/masters/{master_kind}/',payload,format='json')
        self.assertIn(response.status_code,[200,201],response.data)
        return response.data['id']

    def test_unapplied_accounting_schema_returns_actionable_503(self):
        from django.db.migrations.recorder import MigrationRecorder
        from .api import required_accounting_migrations
        MigrationRecorder.Migration.objects.filter(app='accounting',name__in=required_accounting_migrations()).delete()
        for url in ['/api/accounting/setup/', '/api/accounting/reports/?start=2026-02-01&end=2026-02-28', '/api/accounting/posting-queue/']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,503,response.data)
            self.assertIn('python manage.py migrate accounting',str(response.data['detail']))
        response=self.command('receipt',party=self.customer,amount='10',cashAccount='1100')
        self.assertEqual(response.status_code,503,response.data)
        self.assertEqual(Document.objects.count(),0)
        self.admin.is_active=False;self.admin.save()
        self.assertEqual(self.client.get('/api/accounting/setup/').status_code,403)

    def test_finance_masters_nested_groups_ledgers_and_group_reports(self):
        from .models import AccountGroup
        root=self.create_finance_master('groups',name='Operating costs',kind='expense')
        child=self.create_finance_master('groups',name='Office costs',kind='expense',parent=root)
        self.create_finance_master('accounts',code='6110',name='Office rent',kind='expense',cash=False,group=child)
        expense=self.command('expense',cashAccount='1100',items=[{'description':'Rent','quantity':'1','price':'50','account':'6110'}])
        self.assertEqual(expense.status_code,201,expense.data)
        rows={r['name']:r for r in self.report()['groupSummary']}
        self.assertEqual(Decimal(rows['Office costs']['balance']),Decimal('50'))
        self.assertEqual(Decimal(rows['Operating costs']['balance']),Decimal('50'))
        self.assertEqual(rows['Operating costs']['ledgerCount'],1)
        self.assertEqual(AccountGroup.objects.get(pk=child).parent_id,root)
        boot=self.client.get('/api/accounting/setup/').data
        self.assertEqual(next(a for a in boot['accounts'] if a['code']=='6110')['group_id'],child)
        self.assertTrue(any(c['code']=='UGX' for c in boot['currencies']))
        self.assertTrue(any(v['base_kind']=='journal' for v in boot['voucherTypes']))

    def test_group_validation_case_duplicates_and_existing_membership(self):
        root=self.create_finance_master('groups',name='Finance assets',kind='asset')
        for payload in [{'name':'Finance assets','kind':'asset'},{'name':'finance ASSETS','kind':'asset'},
                        {'name':'Bad child','kind':'expense','parent':root},{'name':'Bad parent','kind':'asset','parent':999999}]:
            r=self.client.post('/api/accounting/masters/groups/',payload,format='json');self.assertEqual(r.status_code,400,r.data)
        self.assertEqual(self.client.post('/api/accounting/masters/accounts/',{'code':'BAD','name':'Wrong classification','kind':'expense','group':root},format='json').status_code,400)
        party=self.create_finance_master('parties',name='Grouped customer',kind='customer',group=root)
        self.assertEqual(next(p for p in self.client.get('/api/accounting/setup/').data['parties'] if p['id']==party)['group_id'],root)
        self.command('receipt',party=party,amount='15',cashAccount='1100')
        grouped=next(g for g in self.report()['partyGroupSummary'] if g['id']==root)
        self.assertEqual(Decimal(grouped['balance']),Decimal('-15'))
        self.assertEqual(self.client.post('/api/accounting/masters/parties/',{'name':'Wrong supplier','kind':'supplier','group':root},format='json').status_code,400)
        self.command('receipt',party=self.customer,amount='20',cashAccount='1100')
        before=list(JournalLine.objects.values('id','debit','credit','account_id','party_id'))
        r=self.client.post('/api/accounting/masters/group-membership/',{'ledgerType':'account','ledger':'1100','group':root},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(Account.objects.get(pk='1100').group_id,root)
        self.assertEqual(before,list(JournalLine.objects.values('id','debit','credit','account_id','party_id')))
        self.assertEqual(self.client.post('/api/accounting/masters/group-membership/',{'ledgerType':'account','ledger':'6100','group':root},format='json').status_code,400)
        r=self.client.post('/api/accounting/masters/group-membership/',{'ledgerType':'account','ledger':'1100','group':None},format='json')
        self.assertEqual(r.status_code,200,r.data);self.assertIsNone(Account.objects.get(pk='1100').group_id)

    def test_custom_voucher_cost_category_and_currency_posting(self):
        category=self.create_finance_master('cost-categories',name='Administration')
        voucher=self.create_finance_master('voucher-types',name='Office expense',baseKind='expense')
        currency=self.create_finance_master('currencies',code='USD',name='US Dollar',symbol='$')
        self.assertEqual(currency,'USD')
        r=self.command('expense',currency='USD',fx='2',voucherType=voucher,costCategory=category,cashAccount='1200',items=[{'description':'Office fee','quantity':'1','price':'20'}])
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['voucherTypeName'],'Office expense');self.assertEqual(r.data['costCategoryName'],'Administration')
        self.assertEqual(r.data['currency'],'USD')
        data=self.report();self.assertEqual(Decimal(next(c for c in data['costCategorySummary'] if c['id']==category)['expenses']),Decimal('40'))
        self.assertEqual(data['transactions'][0]['voucherType'],'Office expense')
        self.assertEqual(self.command('receipt',party=self.customer,amount='10',cashAccount='1100',voucherType=voucher).status_code,400)
        self.assertEqual(self.command('expense',cashAccount='1100',costCategory=999999,items=[{'description':'Bad','quantity':'1','price':'1'}]).status_code,400)
        reversed_doc=self.command('reverse',document=r.data['id'],date='2026-03-01',notes='Correct the office fee')
        self.assertEqual(reversed_doc.status_code,201,reversed_doc.data)
        self.assertEqual(reversed_doc.data['costCategoryName'],'Administration')
        self.assertEqual(Decimal(next(c for c in self.report(end='2026-03-31')['costCategorySummary'] if c['id']==category)['expenses']),Decimal('0'))
        self.assertEqual(Decimal(next(c for c in self.report()['costCategorySummary'] if c['id']==category)['expenses']),Decimal('40'))

    def test_master_duplicates_unsupported_types_and_role_authority(self):
        for kind,payload in [('cost-categories',{'name':'Factory overhead'}),('voucher-types',{'name':'Customer collection','baseKind':'receipt'}),('currencies',{'code':'EUR','name':'Euro','symbol':'EUR'})]:
            self.create_finance_master(kind,**payload)
            duplicate={**payload,**({'name':payload['name'].upper()} if kind!='currencies' else {})}
            self.assertEqual(self.client.post(f'/api/accounting/masters/{kind}/',duplicate,format='json').status_code,400)
        for kind,payload in [('voucher-types',{'name':'Unsafe','baseKind':'erase'}),('currencies',{'code':'usd','name':'Lowercase'}),('currencies',{'code':'USDD','name':'Long code'})]:
            self.assertEqual(self.client.post(f'/api/accounting/masters/{kind}/',payload,format='json').status_code,400)
        self.client.force_authenticate(self.accounts)
        for kind,payload in [('groups',{'name':'Clerk group','kind':'expense'}),('cost-categories',{'name':'Clerk category'}),('currencies',{'code':'GBP','name':'Pound'}),('voucher-types',{'name':'Clerk type','baseKind':'payment'}),('accounts',{'code':'9999','name':'Clerk general ledger','kind':'expense'})]:
            self.assertEqual(self.client.post(f'/api/accounting/masters/{kind}/',payload,format='json').status_code,403)
        self.assertEqual(self.client.post('/api/accounting/masters/parties/',{'name':'Clerk customer','kind':'customer'},format='json').status_code,200)

    def test_custom_journal_type_posts_balanced_ledgers_and_preserves_rules(self):
        group=self.create_finance_master('groups',name='Other revenue',kind='income')
        self.create_finance_master('accounts',code='4300',name='Service income',kind='income',group=group)
        voucher=self.create_finance_master('voucher-types',name='Service adjustment',baseKind='journal')
        lines=[{'account':'1300','debit':'50','party':self.customer},{'account':'4300','credit':'50','party':self.customer}]
        r=self.command('journal',voucherType=voucher,lines=lines)
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['voucherTypeName'],'Service adjustment')
        self.assertEqual(Decimal(self.report()['profitLoss']['income']),Decimal('50'))
        self.assertEqual(Decimal(self.report(party=self.customer)['ledgerClosing']),Decimal('50'))
        self.assertEqual(self.report()['trialDifference'],'0.00')
        customer=next(c for c in self.report()['customerProfit']['rows'] if c['customerId']==self.customer)
        self.assertEqual(Decimal(customer['income']),Decimal('50'))
        for bad_lines in [[{'account':'1300','debit':'50'},{'account':'4300','credit':'50'}],
                          [{'account':'1100','debit':'51'},{'account':'4300','credit':'50'}],
                          [{'account':'1400','debit':'50'},{'account':'4300','credit':'50'}]]:
            r=self.command('journal',lines=bad_lines);self.assertEqual(r.status_code,400,r.data)
        self.assertEqual(self.command('journal',currency='USD',fx='2',lines=lines).status_code,400)
        self.assertEqual(self.command('journal',lines=[{'account':'1300','debit':'50','party':self.supplier},{'account':'4300','credit':'50'}]).status_code,400)

    def test_finance_customer_can_create_erp_customer_for_sales_orders(self):
        self.enable_customer_master_permissions()
        from erp.models import Customer
        key=str(uuid4())
        payload={'kind':'customer','name':'Finance-created customer','createErpCustomer':True,'creationKey':key,'customerType':'B2C'}
        result=self.client.post('/api/accounting/masters/parties/',payload,format='json')
        self.assertEqual(result.status_code,200,result.data)
        canonical=Customer.objects.get(creation_key=key)
        self.assertEqual(canonical.customer_type,'B2C')
        self.assertEqual(result.data['source'],f'customer:{canonical.pk}')
        options=self.client.get('/api/production-workflow/order-options/')
        self.assertEqual(options.status_code,200,options.data)
        self.assertIn({'id':f'db:{canonical.pk}','name':canonical.name},options.data['customers'])
        replay=self.client.post('/api/accounting/masters/parties/',payload,format='json')
        self.assertEqual(replay.status_code,200,replay.data);self.assertEqual(replay.data['id'],result.data['id'])
        self.assertEqual(Customer.objects.filter(creation_key=key).count(),1)

    def test_existing_finance_customer_links_by_id_without_changing_journals(self):
        self.enable_customer_master_permissions()
        from erp.models import Customer
        from .models import Party
        self.invoice()
        before=list(JournalLine.objects.values('id','party_id','account_id','debit','credit'))
        canonical=Customer.objects.create(name='Same displayed name')
        result=self.client.post('/api/accounting/masters/customer-link/',{'party':self.customer,'erpCustomer':canonical.pk},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(Party.objects.get(pk=self.customer).source,f'customer:{canonical.pk}')
        self.assertEqual(before,list(JournalLine.objects.values('id','party_id','account_id','debit','credit')))
        self.assertEqual(self.client.post('/api/accounting/masters/customer-link/',{'party':self.supplier,'erpCustomer':canonical.pk},format='json').status_code,400)
        other=self.master('customer','Another customer')
        self.assertEqual(self.client.post('/api/accounting/masters/customer-link/',{'party':other,'erpCustomer':canonical.pk},format='json').status_code,400)
        different=Customer.objects.create(name='Different customer')
        self.assertEqual(self.client.post('/api/accounting/masters/customer-link/',{'party':self.customer,'erpCustomer':different.pk},format='json').status_code,400)

    def test_existing_finance_customer_can_publish_new_customer_with_permissions(self):
        self.enable_customer_master_permissions()
        from erp.models import Customer
        from .models import Party
        key=str(uuid4())
        payload={'party':self.customer,'creationKey':key,'customerType':'B2B'}
        result=self.client.post('/api/accounting/masters/customer-link/',payload,format='json')
        self.assertEqual(result.status_code,200,result.data)
        canonical=Customer.objects.get(creation_key=key)
        self.assertEqual(Party.objects.get(pk=self.customer).source,f'customer:{canonical.pk}')
        self.client.force_authenticate(self.accounts)
        from unittest.mock import patch
        with patch.object(type(self.accounts),'has_perm',return_value=False):
            result=self.client.post('/api/accounting/masters/parties/',{'name':'Not permitted','kind':'customer','createErpCustomer':True,'creationKey':str(uuid4())},format='json')
        self.assertEqual(result.status_code,403,result.data)
        self.assertFalse(Party.objects.filter(name='Not permitted').exists())
        self.assertFalse(Customer.objects.filter(name='Not permitted').exists())

    def enable_customer_master_permissions(self):
        from django.contrib.auth.models import Permission
        self.admin.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp',codename__in=['add_customer','view_customer','view_workfloworder']))
        self.admin=get_user_model().objects.get(pk=self.admin.pk)
        self.client.force_authenticate(self.admin)

    def test_ledger_edit_preserves_transactions_and_rejects_stale_or_type_changes(self):
        from .models import Party, AccountGroup
        self.invoice()
        before = list(JournalLine.objects.values())
        group = AccountGroup.objects.create(name='Customer group', kind='asset')
        payload = {'ledgerType':'party','ledger':self.customer,'expectedName':'Customer A','expectedGroup':None,'name':'Corrected customer','group':group.pk}
        response = self.client.post('/api/accounting/masters/ledger-update/',payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(Party.objects.get(pk=self.customer).name,'Corrected customer')
        self.assertEqual(list(JournalLine.objects.values()),before)
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-update/',payload,format='json').status_code,400)
        payload.update(expectedName='Corrected customer',expectedGroup=group.pk,kind='supplier')
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-update/',payload,format='json').status_code,400)
        payload.pop('kind');payload['group']=AccountGroup.objects.create(name='Expense group',kind='expense').pk
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-update/',payload,format='json').status_code,400)
        self.client.force_authenticate(self.accounts)
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-update/',payload,format='json').status_code,403)

    def test_ledger_delete_protects_history_links_builtin_accounts_and_pending_queue(self):
        from .models import Party
        self.invoice()
        def delete(ledger, name, ledger_type='party'):
            return self.client.post('/api/accounting/masters/ledger-delete/',{'ledgerType':ledger_type,'ledger':ledger,'expectedName':name,'expectedGroup':None},format='json')
        self.assertEqual(delete(self.customer,'Customer A').status_code,400)
        linked=Party.objects.create(name='Linked',kind='customer',source='customer:999')
        self.assertEqual(delete(linked.pk,linked.name).status_code,400)
        built_in=Account.objects.get(code='1100')
        payload={'ledgerType':'account','ledger':built_in.code,'expectedName':built_in.name,'expectedGroup':built_in.group_id}
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-delete/',payload,format='json').status_code,400)
        pending=PostingEvent.objects.create(source='synthetic-pending-delete',payload={'party':self.supplier},actor=self.admin)
        self.assertEqual(delete(self.supplier,'Supplier A').status_code,400)
        pending.cancelled=True;pending.save()
        self.assertEqual(delete(self.supplier,'Supplier A').status_code,200)
        self.assertFalse(Party.objects.filter(pk=self.supplier).exists())
        self.assertEqual(delete(self.supplier,'Supplier A').status_code,400)

    def test_unused_custom_account_can_be_edited_and_deleted_only_by_admin(self):
        account=Account.objects.create(code='CUSTOM-X',name='Custom expense',kind='expense')
        payload={'ledgerType':'account','ledger':account.code,'expectedName':account.name,'expectedGroup':None,'name':'Renamed expense','group':None}
        response=self.client.post('/api/accounting/masters/ledger-update/',payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        account.refresh_from_db();self.assertEqual(account.name,'Renamed expense');self.assertEqual(account.kind,'expense')
        payload={'ledgerType':'account','ledger':account.code,'expectedName':account.name,'expectedGroup':None}
        self.client.force_authenticate(self.accounts)
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-delete/',payload,format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post('/api/accounting/masters/ledger-delete/',payload,format='json').status_code,200)
        self.assertFalse(Account.objects.filter(pk=account.pk).exists())

    def test_quotation_choices_expose_finance_only_customer_identity_and_link_exact_ledger(self):
        from django.contrib.auth.models import Permission
        from erp.models import Customer
        from .models import Party
        self.admin.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp',codename__in=['view_customer','add_customer']))
        for attr in ('_perm_cache','_user_perm_cache','_group_perm_cache'):
            if hasattr(self.admin,attr):delattr(self.admin,attr)
        existing=Customer.objects.create(name='Customer A')
        linked=Party.objects.create(name='Already linked',kind='customer',source=f'customer:{existing.pk}')
        count=Customer.objects.count()
        r=self.client.get('/api/customers/ledger-options/')
        self.assertEqual(r.status_code,200,r.data);self.assertTrue(r.data['canLink'])
        self.assertIn({'id':self.customer,'name':'Customer A'},r.data['ledgers'])
        self.assertNotIn(self.supplier,[row['id'] for row in r.data['ledgers']])
        self.assertNotIn(linked.pk,[row['id'] for row in r.data['ledgers']])
        self.assertEqual(Customer.objects.count(),count)
        key=str(uuid4())
        r=self.client.post('/api/accounting/masters/customer-link/',{'party':self.customer,'creationKey':key,'customerType':'B2B'},format='json')
        self.assertEqual(r.status_code,200,r.data)
        created=Customer.objects.get(creation_key=key)
        self.assertNotEqual(created.pk,existing.pk)  # Same names do not merge identities.
        self.assertEqual(Party.objects.get(pk=self.customer).source,f'customer:{created.pk}')
        self.assertNotIn(self.customer,[row['id'] for row in self.client.get('/api/customers/ledger-options/').data['ledgers']])
        detail=self.client.get(f'/api/customers/{created.pk}/')
        self.assertEqual(detail.status_code,200,detail.data);self.assertEqual(detail.data['name'],'Customer A')
        self.assertEqual(JournalLine.objects.count(),0)

    def test_quotation_ledger_choices_require_customer_view_permission_without_granting_link_rights(self):
        from django.contrib.auth.models import Permission
        self.accounts.user_permissions.clear()
        self.client.force_authenticate(self.accounts)
        from unittest.mock import patch
        with patch.object(type(self.accounts),'has_perms',return_value=False):
            self.assertEqual(self.client.get('/api/customers/ledger-options/').status_code,403)
        self.accounts.user_permissions.add(Permission.objects.get(content_type__app_label='erp',codename='view_customer'))
        with patch.object(type(self.accounts),'has_perm',side_effect=lambda perm,obj=None:perm=='erp.view_customer'):
            r=self.client.get('/api/customers/ledger-options/')
        self.assertEqual(r.status_code,200,r.data);self.assertFalse(r.data['canLink'])

    def test_valuation_catalog_carries_exact_item_unit_and_rejects_typed_wrong_unit(self):
        from datetime import date
        from erp.models import FinishedStockItem
        from inventory.models import RawMaterial
        from .models import StockValue
        product=FinishedStockItem.objects.create(id='valuation-cartons',name='Valuation cartons',category='packing',unit='ctn',opening_stock=100,opening_date=date(2026,1,1))
        RawMaterial.objects.create(id='valuation-raw',name='Valuation raw',family='Test',category='virgin',unit='kg',opening_stock=10,opening_date=date(2026,1,1))
        r=self.client.get('/api/accounting/valuation-items/')
        self.assertEqual(r.status_code,200,r.data)
        choices={i['id']:i for i in r.data}
        self.assertEqual(choices['finished:'+product.pk]['unit'],'ctn')
        self.assertEqual(choices['raw:valuation-raw']['unit'],'kg')
        r=self.command('stock-receipt',item='finished:'+product.pk,unit='kg',quantity='10',value='100',contra='3100')
        self.assertEqual(r.status_code,400,r.data);self.assertIn('item unit is ctn',str(r.data))
        self.assertFalse(StockValue.objects.filter(pk='finished:'+product.pk).exists())
        r=self.command('stock-receipt',item='finished:'+product.pk,unit='ctn',quantity='10',value='100',contra='3100')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(StockValue.objects.get(pk='finished:'+product.pk).unit,'ctn')
        product.refresh_from_db();self.assertEqual(product.opening_stock,100)

    def test_valuation_date_error_is_separate_from_unit_error(self):
        r=self.command('stock-receipt',item='valuation-date-test',unit='ctn',quantity='10',value='100',contra='3100',date='2026-02-02')
        self.assertEqual(r.status_code,201,r.data)
        r=self.command('stock-issue',item='valuation-date-test',unit='ctn',quantity='1',contra='5100',date='2026-02-01')
        self.assertEqual(r.status_code,400,r.data)
        self.assertIn('valuation date 2026-02-01',str(r.data));self.assertIn('2026-02-02',str(r.data))
        self.assertNotIn('unit must match',str(r.data))

    def test_wrong_historical_valuation_unit_is_not_silently_relabelled(self):
        from datetime import date
        from erp.models import FinishedStockItem
        from .models import StockValue
        FinishedStockItem.objects.create(id='historic-cartons',name='Historic cartons',category='packing',unit='ctn',opening_stock=100,opening_date=date(2026,1,1))
        stock=StockValue.objects.create(item='finished:historic-cartons',unit='kg',quantity=100,value=1000,last_date=date(2026,1,1))
        r=self.command('stock-issue',item=stock.item,unit='ctn',quantity='10',contra='5100')
        self.assertEqual(r.status_code,400,r.data);self.assertIn('existing accounting valuation uses kg',str(r.data))
        stock.refresh_from_db();self.assertEqual(stock.unit,'kg');self.assertEqual(stock.quantity,100)
        choices={i['id']:i for i in self.client.get('/api/accounting/valuation-items/').data}
        self.assertEqual(choices[stock.item]['valuedUnit'],'kg');self.assertEqual(choices[stock.item]['unit'],'ctn')
