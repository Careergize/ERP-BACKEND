from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from .models import Customer,WorkflowOrder,SharedEntry,EntryRevision,FinishedStockItem
from .sales_models import SalesReturn,SalesCreditNote,SalesReturnInspection
from inventory.models import RawMovement
from accounting.models import JournalLine,Document
from unittest.mock import patch
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.core.cache import cache


class SalesTests(TestCase):
    def setUp(self):
        # Isolate DRF's per-user throttle cache: test DB transactions reuse user IDs.
        cache.clear();self.addCleanup(cache.clear)
        self.writer=get_user_model().objects.create_user(username='sales-writer',role='receptionist')
        self.checker=get_user_model().objects.create_user(username='sales-finance',role='accounts')
        self.qc=get_user_model().objects.create_user(username='sales-qc',role='qc_inspector')
        self.other=get_user_model().objects.create_user(username='sales-other',role='receptionist')
        for u,verbs in [(self.writer,['view','edit']),(self.other,['view','edit']),(self.checker,['view','view_all','edit','review','issue']),(self.qc,['view','inspect'])]:
            u.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp',codename__in=[v+'_sales' for v in verbs]))
        self.writer.user_permissions.add(Permission.objects.get(content_type__app_label='erp',codename='view_sharedentry'));self.other.user_permissions.add(Permission.objects.get(content_type__app_label='erp',codename='view_sharedentry'))
        self.customer=Customer.objects.create(name='Same display name');self.second=Customer.objects.create(name='Same display name')
        self.order=WorkflowOrder.objects.create(request_id=uuid4(),created_by=self.writer,data={'id':'source-order','orderNumber':'SO-SALES','customerId':'db:'+str(self.customer.pk),'items':[{'id':'source-line','itemId':'product-1','unit':'kg'}]})
        self.invoice={'id':'source-invoice','number':'INV-SALES','date':timezone.localdate().isoformat(),'orderId':'wf-order-'+str(self.order.pk),'orderNumber':'SO-SALES','buyer':'Same display name','currency':'USD','taxMode':'exclusive','seller':'Real fixture seller','sellerAddress':'Fixture address','sellerTax':'FIXTURE-TAX','items':[{'itemId':'source-line','description':'Original product','quantity':'10','unit':'kg','rate':'10','amount':'100'}],'total':'100','tax':'0','led':'0'}
        self.source=SharedEntry.objects.create(key='invoices',content=[self.invoice],updated_by=self.writer)
    def client_for(self,user):
        c=APIClient();c.credentials(HTTP_AUTHORIZATION='Bearer '+str(AccessToken.for_user(user)));return c
    def request(self,user,method,path,data=None):return getattr(self.client_for(user),method)('/api/sales/'+path,data,format='json')
    def create(self,qty='4',user=None,**extra):
        data={'commandId':str(uuid4()),'invoiceId':self.invoice['id'],'lineKey':self.invoice['id']+':0','customerId':self.customer.pk,'date':self.invoice['date'],'quantity':qty,'reason':'Damaged Goods','condition':'Uninspected','remarks':'Return evidence',**extra}
        r=self.request(user or self.writer,'post','returns/',data);return r,data
    def change(self,row,status,user=None,**extra):
        kind='credit-notes' if 'returnId' in row else 'returns'
        data={'commandId':str(uuid4()),'expectedVersion':row['version'],'status':status,'remarks':'Reviewed evidence',**extra}
        return self.request(user or self.writer,'post',f"{kind}/{row['id']}/transition/",data),data
    def inspect(self,row,result='Pass',accepted='4'):
        return self.request(self.qc,'post',f"returns/{row['id']}/inspect/",{'commandId':str(uuid4()),'expectedVersion':row['version'],'result':result,'quantity':str(row['quantity']),'acceptedQuantity':accepted,'remarks':'QC findings'})
    def approved_return(self,qty='4'):
        r,_=self.create(qty);self.assertEqual(r.status_code,201,r.data)
        r,_=self.change(r.data,'Submitted');self.assertEqual(r.status_code,200,r.data)
        r=self.inspect(r.data,accepted=qty);self.assertEqual(r.status_code,200,r.data)
        r,_=self.change(r.data,'Pending Approval',self.checker);self.assertEqual(r.status_code,200,r.data)
        r,_=self.change(r.data,'Approved',self.checker);self.assertEqual(r.status_code,200,r.data);return r.data
    def credit(self,row,user=None):
        return self.request(user or self.writer,'post','credit-notes/',{'commandId':str(uuid4()),'returnId':row['id'],'date':row['date'],'reason':'Approved return','remarks':'Credit evidence'})
    def test_complete_workflow_with_jwt_issue_pdf_and_operational_ledger(self):
        before=(RawMovement.objects.count(),FinishedStockItem.objects.count(),JournalLine.objects.count(),Document.objects.count());source=deepcopy(self.source.content)
        row=self.approved_return();credit=self.credit(row);self.assertEqual(credit.status_code,201,credit.data)
        for status,user in [('Submitted',self.writer),('Pending Approval',self.checker),('Approved',self.checker),('Issued',self.checker)]:
            credit,_=self.change(credit.data,status,user);self.assertEqual(credit.status_code,200,credit.data)
        self.assertEqual(credit.data['glStatus'],'Not posted');self.assertTrue(credit.data['issuedAt'])
        pdf=self.request(self.checker,'get',f"credit-notes/{credit.data['id']}/pdf/");self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF-'))
        ledger=self.request(self.checker,'get','ledger/');self.assertEqual(ledger.status_code,200);self.assertEqual(ledger.data['count'],3)
        credits=[r for r in ledger.data['results'] if r['type']=='Credit Note'];self.assertEqual(Decimal(credits[0]['credit']),Decimal('40'))
        returned=[r for r in ledger.data['results'] if r['type']=='Return'];self.assertEqual(Decimal(returned[0]['credit']),0)
        self.assertEqual(returned[0]['linkedCreditNotes'][0]['number'],credit.data['number'])
        dashboard=self.request(self.checker,'get','dashboard/').data;self.assertEqual(Decimal(dashboard['currencies']['USD']['net']),Decimal('60'));self.assertIsNone(dashboard['receivables'])
        self.source.refresh_from_db();self.assertEqual(self.source.content,source);self.assertEqual(self.source.version,1);self.assertEqual(before,(RawMovement.objects.count(),FinishedStockItem.objects.count(),JournalLine.objects.count(),Document.objects.count()))
        self.assertEqual(EntryRevision.objects.filter(entry__key__startswith='sales-').count(),10)
    def test_validation_and_foreign_customer_references(self):
        for extra in [{'invoiceId':'missing'},{'lineKey':'missing'},{'customerId':self.second.pk},{'total':'0'},{'quantity':'11'},{'quantity':'0'},{'quantity':'-1'},{'quantity':'0.0000001'}]:
            r,_=self.create(**extra);self.assertEqual(r.status_code,400,(extra,r.data))
        self.assertEqual(SalesReturn.objects.count(),0)
        r=self.request(self.writer,'post','returns/',['bad']);self.assertEqual(r.status_code,400)
    def test_pending_reservations_prevent_overallocation_and_cancel_releases(self):
        first,_=self.create('6');second,_=self.create('6');self.assertEqual(second.status_code,201)
        first,_=self.change(first.data,'Submitted');self.assertEqual(first.status_code,200)
        failed,_=self.change(second.data,'Submitted');self.assertEqual(failed.status_code,400)
        cancelled,_=self.change(first.data,'Cancelled');self.assertEqual(cancelled.status_code,200)
        okay,_=self.change(second.data,'Submitted');self.assertEqual(okay.status_code,200)
    def test_independence_including_admin_and_contributing_editor(self):
        self.writer.is_superuser=True;self.writer.save()
        row,_=self.create();row,_=self.change(row.data,'Submitted')
        self_review,_=self.change(row.data,'Pending Approval');self.assertEqual(self_review.status_code,403)
        reject,_=self.change(row.data,'Rejected');self.assertEqual(reject.status_code,403)
        row,_=self.create();edited=self.request(self.checker,'patch',f"returns/{row.data['id']}/",{'commandId':str(uuid4()),'expectedVersion':row.data['version'],'date':row.data['date'],'quantity':'4','reason':'Updated','condition':'Damaged','remarks':'Editor evidence'})
        self.assertEqual(edited.status_code,200,edited.data);submitted,_=self.change(edited.data,'Submitted');blocked,_=self.change(submitted.data,'Pending Approval',self.checker);self.assertEqual(blocked.status_code,403)
    def test_qc_is_required_before_approval_and_cannot_post_stock(self):
        row,_=self.create();row,_=self.change(row.data,'Submitted');pending,_=self.change(row.data,'Pending Approval',self.checker)
        fail,_=self.change(pending.data,'Approved',self.checker);self.assertEqual(fail.status_code,400)
        inspected=self.inspect(pending.data,'Hold','0');self.assertEqual(inspected.status_code,200,inspected.data)
        self.assertEqual(inspected.data['qcStatus'],'Hold');self.assertEqual(inspected.data['inspections'][0]['acceptedQuantity'],'0.000000')
        denied,_=self.change(inspected.data,'Approved',self.qc);self.assertEqual(denied.status_code,403)
        approved,_=self.change(inspected.data,'Approved',self.checker);self.assertEqual(approved.status_code,200)
        inspection=SalesReturnInspection.objects.get()
        with self.assertRaises(Exception):inspection.delete()
    def test_credit_duplicates_taxed_issuance_and_draft_pdf(self):
        self.invoice.update(total='118',tax='18');self.source.content=[self.invoice];self.source.save()
        row=self.approved_return();credit=self.credit(row);duplicate=self.credit(row);self.assertEqual(duplicate.status_code,400)
        edit={'commandId':str(uuid4()),'expectedVersion':credit.data['version'],'returnId':str(uuid4()),'date':credit.data['date'],'reason':'Edited reason','remarks':'Draft edit'}
        wrong=self.request(self.writer,'patch',f"credit-notes/{credit.data['id']}/",edit);self.assertEqual(wrong.status_code,400)
        credit=self.request(self.writer,'patch',f"credit-notes/{credit.data['id']}/",{**edit,'commandId':str(uuid4()),'returnId':row['id']});self.assertEqual(credit.status_code,200,credit.data)
        preview=self.request(self.writer,'get',f"credit-notes/{credit.data['id']}/preview/");self.assertEqual(preview.status_code,200);self.assertTrue(preview.content.startswith(b'%PDF-'))
        official=self.request(self.writer,'get',f"credit-notes/{credit.data['id']}/pdf/");self.assertEqual(official.status_code,403)
        for status,user in [('Submitted',self.writer),('Pending Approval',self.checker),('Approved',self.checker)]:credit,_=self.change(credit.data,status,user)
        denied,_=self.change(credit.data,'Issued',self.checker);self.assertEqual(denied.status_code,403)
        self.assertEqual(SalesCreditNote.objects.get().vat,Decimal('7.20'))
        # A fractional credit must not escape the source invoice tax-policy gate
        # merely because its allocated VAT rounds to zero cents.
        tiny={**deepcopy(self.invoice),'id':'tiny-tax','number':'INV-TINY-TAX','tax':'0.01','total':'100.01'}
        self.source.content=[self.invoice,tiny];self.source.save()
        returned,_=self.create('1',invoiceId='tiny-tax',lineKey='tiny-tax:0')
        returned,_=self.change(returned.data,'Submitted');returned=self.inspect(returned.data,accepted='1')
        returned,_=self.change(returned.data,'Pending Approval',self.checker);returned,_=self.change(returned.data,'Approved',self.checker)
        small=self.credit(returned.data)
        for status,user in [('Submitted',self.writer),('Pending Approval',self.checker),('Approved',self.checker)]:small,_=self.change(small.data,status,user)
        self.assertEqual(Decimal(small.data['vat']),0);self.assertNotIn('Issued',small.data['allowedActions'])
        blocked,_=self.change(small.data,'Issued',self.checker);self.assertEqual(blocked.status_code,403)
    def test_idempotency_versioning_and_actor_binding(self):
        row,data=self.create();again=self.request(self.writer,'post','returns/',data);self.assertEqual(again.data['id'],row.data['id']);self.assertEqual(SalesReturn.objects.count(),1)
        changed=self.request(self.writer,'post','returns/',{**data,'quantity':'3'});self.assertEqual(changed.status_code,409)
        other=self.request(self.other,'post','returns/',data);self.assertEqual(other.status_code,409)
        submitted,command=self.change(row.data,'Submitted');replay=self.request(self.writer,'post',f"returns/{row.data['id']}/transition/",command);self.assertEqual(replay.status_code,200)
        stale,_=self.change(row.data,'Cancelled');self.assertEqual(stale.status_code,409)
    def test_authentication_scope_filters_and_bootstrap(self):
        anonymous=APIClient().get('/api/sales/bootstrap/');self.assertEqual(anonymous.status_code,401)
        ungranted=get_user_model().objects.create_user(username='sales-no-grants');self.assertEqual(self.request(ungranted,'get','bootstrap/').status_code,403)
        row,_=self.create();self.assertEqual(self.request(self.other,'get',f"returns/{row.data['id']}/").status_code,404)
        self.assertEqual(self.request(self.other,'get','returns/').data['count'],0)
        self.assertEqual(self.request(self.checker,'get',f'ledger/customers/{self.second.pk}/').data['count'],0)
        self.assertEqual(self.request(self.checker,'get',f'ledger/?customer_id={self.customer.pk}&product_id=product-1').data['count'],2)
        self.assertEqual(self.request(self.checker,'get','ledger/?ordering=bad').status_code,400)
        self.assertEqual(self.request(self.checker,'get','ledger/?page=2').status_code,404)
        bootstrap=self.request(self.writer,'get','bootstrap/');self.assertEqual(bootstrap.status_code,200);self.assertEqual(len(bootstrap.data['invoices']),1)
        reporting=self.request(self.checker,'get','bootstrap/');self.assertEqual(len(reporting.data['invoices']),1);self.assertTrue(reporting.data['capabilities']['view_all'])
        self.assertFalse(bootstrap.data['limitations']['restock']);self.assertFalse(bootstrap.data['limitations']['receivables'])
    def test_rounding_after_cancelled_pending_return_does_not_overcredit(self):
        self.invoice.update(total='1.00',tax='0');self.invoice['items'][0].update(quantity='3',amount='1.00',rate='0.333333');self.source.content=[self.invoice];self.source.save()
        a,_=self.create('1');a,_=self.change(a.data,'Submitted')
        b,_=self.create('1');b,_=self.change(b.data,'Submitted')
        self.change(a.data,'Cancelled')
        b=self.inspect(b.data,accepted='1');b,_=self.change(b.data,'Pending Approval',self.checker);b,_=self.change(b.data,'Approved',self.checker)
        c=self.approved_return('2');self.assertEqual(Decimal(b.data['total'])+Decimal(c['total']),Decimal('1.00'))
    def test_issued_immutability_and_audit_failure_rollback(self):
        with patch('erp.sales_service.audit',side_effect=RuntimeError('isolated audit failure')):
            with self.assertRaises(RuntimeError):self.create()
        self.assertEqual(SalesReturn.objects.count(),0)
        row=self.approved_return();credit=self.credit(row)
        for status,user in [('Submitted',self.writer),('Pending Approval',self.checker),('Approved',self.checker),('Issued',self.checker)]:credit,_=self.change(credit.data,status,user)
        instance=SalesCreditNote.objects.get();instance.version+=1
        with self.assertRaises(Exception):instance.save()
        with self.assertRaises(Exception):SalesCreditNote.objects.all().delete()
    def test_currency_status_and_filter_reporting(self):
        order=WorkflowOrder.objects.create(request_id=uuid4(),created_by=self.writer,data={'customerId':str(self.second.pk),'items':[{'id':'other-line','itemId':'product-2','unit':'kg'}]})
        second={**deepcopy(self.invoice),'id':'foreign','number':'INV-FOREIGN','orderId':str(order.pk),'currency':'UGX'};second['items'][0]['itemId']='other-line'
        draft={**deepcopy(self.invoice),'id':'draft-invoice','number':'INV-DRAFT','status':'Draft'}
        self.source.content=[self.invoice,second,draft];self.source.save()
        dashboard=self.request(self.checker,'get','dashboard/').data['currencies'];self.assertEqual(Decimal(dashboard['USD']['gross']),Decimal('100'));self.assertEqual(Decimal(dashboard['UGX']['gross']),Decimal('100'))
        filtered=self.request(self.checker,'get',f'ledger/?customer_id={self.second.pk}&currency=UGX').data;self.assertEqual(filtered['count'],1)
        self.assertEqual(self.request(self.checker,'get','ledger/?from=2026-12-01&to=2026-01-01').status_code,400)
        self.assertEqual(self.create(invoiceId='draft-invoice',lineKey='draft-invoice:0')[0].status_code,400)


class SalesMigrationTests(TransactionTestCase):
    def test_existing_source_and_users_survive_additive_migration(self):
        executor=MigrationExecutor(connection);leaves=executor.loader.graph.leaf_nodes();before=[('erp','0020_recycling_entry_permission')];after=[('erp','0021_sales_operations')]
        try:
            executor.migrate(before);apps=executor.loader.project_state(before).apps
            user=apps.get_model('accounts','User').objects.create(username='sales-migration-owner')
            source=apps.get_model('erp','SharedEntry').objects.create(key='invoices',content=[{'id':'keep-original'}],version=17,updated_by_id=user.pk)
            executor=MigrationExecutor(connection);executor.migrate(after);apps=executor.loader.project_state(after).apps
            preserved=apps.get_model('erp','SharedEntry').objects.get(pk=source.pk);self.assertEqual(preserved.version,17);self.assertEqual(preserved.content,[{'id':'keep-original'}]);self.assertEqual(apps.get_model('erp','SalesReturn').objects.count(),0)
            self.assertFalse(user.user_permissions.filter(codename__endswith='_sales').exists())
        finally:MigrationExecutor(connection).migrate(leaves)

