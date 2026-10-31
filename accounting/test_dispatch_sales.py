from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
from django.utils import timezone
from rest_framework.test import APITestCase
from erp import test_operational_records as operational_tests
from erp.models import Customer, SharedEntry, EntryRevision
from .models import Document, JournalLine, PostingEvent, Party


class DispatchSaleTests(APITestCase):
    setUp = operational_tests.OperationalRecordTests.setUp
    put = operational_tests.OperationalRecordTests.put
    ledger = operational_tests.OperationalRecordTests.ledger

    def prepare(self, kind='credit', amount=2):
        self.client.force_authenticate(self.users['admin'])
        result=self.client.post('/api/accounting/setup/',{'company':'Test seller','currency':'USD','startDate':self.today,'fiscalMonth':1,'valuation':'weighted-average'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        customer=Customer.objects.create(name='Saved buyer')
        self.order.data['customerId']=f'db:{customer.pk}'
        self.order.data['commercialPricing']={'taxMode':'exclusive','discount':1}
        self.order.save()
        self.party=Party.objects.create(name=customer.name,kind='customer',source=f'customer:{customer.pk}')
        body=self.ledger(amount)
        self.identity=str(uuid4())
        body['dispatches'][0]['sale']={'id':self.identity,'payment':{'kind':kind,'fx':'1','cashAccount':'1100','seller':'Test seller','billingAddress':'Buyer billing'}}
        return body

    def balance(self):
        return sum((r.debit-r.credit for r in JournalLine.objects.filter(party=self.party,account_id='1300')),Decimal(0))

    def test_credit_dispatch_posts_invoice_with_approved_rates_and_retries_once(self):
        body=self.prepare();command=str(uuid4())
        duplicate_customer=Customer.objects.create(name=self.party.name)
        duplicate_party=Party.objects.create(name=self.party.name,kind='customer',source=f'customer:{duplicate_customer.pk}')
        # Display/browser totals never determine financial posting.
        body['dispatches'][0]['sale']['total']=1
        r=self.put('inventory-ledger',body,command,0);self.assertEqual(r.status_code,200,r.data)
        invoice=SharedEntry.objects.get(pk='invoices').content[0]
        self.assertEqual(invoice['total'],4.48)
        self.assertEqual(invoice['discount'],0.2)
        self.assertEqual(self.balance(),Decimal('4.48'))
        self.assertFalse(JournalLine.objects.filter(party=duplicate_party).exists())
        self.assertEqual(Document.objects.get(kind='invoice').party_id,self.party.pk)
        self.assertEqual(Document.objects.filter(kind='receipt').count(),0)
        self.assertEqual(self.put('inventory-ledger',body,command,0).status_code,200)
        self.assertEqual(Document.objects.filter(kind='invoice').count(),1)
        self.assertEqual(len(SharedEntry.objects.get(pk='inventory-ledger').content['movements']),1)
        self.assertEqual(EntryRevision.objects.filter(entry_id='invoices').count(),1)
        r=self.client.get('/api/accounting/dispatch-options/',{'orderId':self.order.data['id']})
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(Decimal(r.data['balance']),self.balance())

    def test_cash_dispatch_posts_full_allocated_receipt_without_changing_previous_balance(self):
        body=self.prepare('cash')
        from .services import post
        post({'commandId':str(uuid4()),'kind':'receipt','number':'PRIOR-ADVANCE','date':self.today,'party':self.party.pk,'currency':'USD','cashAccount':'1100','amount':'10'},self.users['admin'])
        before=self.balance()
        r=self.put('inventory-ledger',body);self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.balance(),before)
        invoice=Document.objects.get(kind='invoice');receipt=Document.objects.get(source='dispatch-receipt:'+self.identity)
        self.assertEqual(receipt.allocations.get().bill,invoice)
        self.assertEqual(receipt.total,invoice.total)
        self.assertEqual(SharedEntry.objects.get(pk='invoices').content[0]['dispatchPayment']['receiptId'],receipt.pk)
        self.assertTrue(JournalLine.objects.filter(document=receipt,account_id='1100',debit=invoice.total).exists())

    def test_bad_cash_account_rolls_back_stock_invoice_receipt_and_evidence(self):
        body=self.prepare('cash');body['dispatches'][0]['sale']['payment']['cashAccount']='4100'
        r=self.put('inventory-ledger',body);self.assertEqual(r.status_code,400,r.data)
        self.assertFalse(SharedEntry.objects.filter(pk__in=['invoices','inventory-ledger']).exists())
        self.assertEqual(Document.objects.count(),0);self.assertEqual(EntryRevision.objects.count(),0)
        self.assertEqual(PostingEvent.objects.count(),0)

    def test_invalid_choice_missing_address_and_approval_cannot_dispatch(self):
        body=self.prepare()
        for problem in ('kind','address','approval','customer'):
            changed=deepcopy(body)
            if problem=='kind':changed['dispatches'][0]['sale']['payment']['kind']='other'
            if problem=='address':changed['dispatches'][0]['sale']['payment']['billingAddress']=''
            original=deepcopy(self.order.data)
            if problem=='approval':self.order.data.pop('salesOrderApproval')
            if problem=='customer':self.order.data['customerId']='db:999999'
            self.order.save()
            r=self.put('inventory-ledger',changed);self.assertEqual(r.status_code,400,(problem,r.data))
            self.order.data=original;self.order.save()
        self.assertFalse(SharedEntry.objects.filter(pk='inventory-ledger').exists())

    def test_cash_credit_finance_is_restricted_to_existing_invoice_authority(self):
        body=self.prepare('cash');self.client.force_authenticate(self.users['store_keeper'])
        self.assertEqual(self.client.get('/api/accounting/dispatch-options/',{'orderId':self.order.data['id']}).status_code,403)
        self.assertEqual(self.put('inventory-ledger',body).status_code,403)
        self.assertEqual(Document.objects.count(),0)
        self.client.force_authenticate(self.users['receptionist'])
        r=self.put('inventory-ledger',body);self.assertEqual(r.status_code,200,r.data)

    def test_foreign_currency_requires_explicit_fx_and_balances_use_reporting_currency(self):
        body=self.prepare()
        self.order.data['currency']='EUR';self.order.save()
        body['dispatches'][0]['sale']['payment'].pop('fx')
        r=self.put('inventory-ledger',body)
        # No silent reporting-currency rate for a foreign sale.
        self.assertEqual(r.status_code,400,r.data)
        body['dispatches'][0]['sale']['payment']['fx']='2'
        r=self.put('inventory-ledger',body);self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.balance(),Decimal('8.96'))

    def test_partial_then_final_dispatch_reconciles_discount_and_prevents_second_invoicing(self):
        body=self.prepare(amount=2)
        self.assertEqual(self.put('inventory-ledger',body).status_code,200)
        first_invoice=deepcopy(SharedEntry.objects.get(pk='invoices').content[0])
        second=self.ledger(8)
        for key in ('movements','dispatches','allocations'):
            for row in second[key]:
                if 'id' in row:row['id']=row['id'].replace('dispatch1','dispatch2').replace('allocation1','allocation2')
                if 'dispatchId' in row:row['dispatchId']='dispatch2'
                if 'allocationId' in row:row['allocationId']='allocation2'
        second['dispatches'][0]['sale']={'id':str(uuid4()),'payment':deepcopy(body['dispatches'][0]['sale']['payment'])}
        for key in second:second[key]=body[key]+second[key]
        r=self.put('inventory-ledger',second);self.assertEqual(r.status_code,200,r.data)
        invoices=SharedEntry.objects.get(pk='invoices').content
        self.assertEqual(sum(Decimal(str(i['total'])) for i in invoices),Decimal('22.42'))
        self.assertEqual(sum(Decimal(str(i['discount'])) for i in invoices),Decimal('1'))
        self.assertEqual(invoices[0],first_invoice)
        # Operational invoice validation rejects billing the same dispatch again.
        duplicate={**first_invoice,'id':'duplicate','number':'DUPLICATE'}
        r=self.put('invoices',invoices+[duplicate]);self.assertEqual(r.status_code,400,r.data)
