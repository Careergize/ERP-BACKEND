from decimal import Decimal
from uuid import uuid4
from django.db import connection
from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from .models import EntryRevision
from .sales_models import SalesReturn, SalesCreditNote, SalesReturnInspection
from . import sales_service as service


def scope(model,user):
    rows=model.objects.select_related('customer','created_by','reviewed_by')
    return rows if any(service.can(user,v) for v in ['view_all','review','inspect']) else rows.filter(created_by=user)


def dto(row,user):
    key=f'sales-{row._meta.model_name}-{row.pk}'
    history=[{'action':r.content['action'],'userId':str(r.actor_id),'user':r.actor.get_full_name() or r.actor.username,'at':r.recorded_at.isoformat(),'remarks':r.content['next'].get('remarks','')} for r in EntryRevision.objects.filter(entry_id=key).select_related('actor').order_by('version')]
    data={'id':str(row.pk),'number':row.number,'date':row.date.isoformat(),'invoice':row.snapshot['invoice'],'line':row.snapshot['line'],'quantity':str(row.quantity),'net':str(row.net),'vat':str(row.vat),'led':str(row.led),'total':str(row.total),'reason':row.reason,'remarks':row.remarks,'status':row.status,'history':history,'version':row.version,'allowedActions':service.allowed_actions(row,user),'canEdit':service.can(user,'edit') and row.status=='Draft','createdBy':row.created_by_id,'reviewedBy':row.reviewed_by_id,'createdAt':row.created_at.isoformat(),'updatedAt':row.updated_at.isoformat(),'approvedAt':row.approved_at.isoformat() if row.approved_at else None,'glStatus':'Not posted'}
    if isinstance(row,SalesReturn):
        data.update(condition=row.condition,qcStatus=row.qc_status,inspections=[{'id':str(i.pk),'result':i.result,'inspectedQuantity':str(i.inspected_quantity),'acceptedQuantity':str(i.accepted_quantity),'remarks':i.remarks,'inspectorId':str(i.inspector_id),'inspector':i.inspector.get_full_name() or i.inspector.username,'at':i.recorded_at.isoformat(),'qcReference':i.qc_reference} for i in row.inspections.select_related('inspector').order_by('recorded_at')])
        data['linkedCreditNotes']=[{'id':str(c.pk),'number':c.number,'status':c.status} for c in row.credits.order_by('created_at')]
    else:data.update(returnId=str(row.sales_return_id),returnNumber=row.sales_return.number,issuedAt=row.issued_at.isoformat() if row.issued_at else None)
    return data


def ledger(user,reporting=True):
    service.access(user,'view_all' if reporting else 'view')
    invoices,issues=service.invoices() if reporting else ([],[]);rows=[]
    def base(invoice,line):return {'invoiceId':invoice['id'],'invoiceNumber':invoice['number'],'customerKey':invoice['customerKey'],'customerId':invoice['customerId'],'customer':invoice['customer'],'currency':invoice['currency'],'productId':line['productId'],'product':line['product'],'unit':line['unit'],'invoiceStatus':invoice['status'],'balance':Decimal(0)}
    for invoice in invoices:
        for line in invoice['lines']:rows.append({**base(invoice,line),'key':'sale:'+line['key'],'date':invoice['date'],'reference':invoice['number'],'type':'Sale','quantity':Decimal(line['quantity']),'debit':Decimal(line['total']) if invoice['status'] not in ['Draft','Cancelled'] else Decimal(0),'credit':Decimal(0),'netDebit':Decimal(line['net']) if invoice['status'] not in ['Draft','Cancelled'] else Decimal(0),'netCredit':Decimal(0),'status':invoice['status'],'reason':'','note':'Recorded immutable invoice snapshot; receivables are separate.','documentId':invoice['id']})
    for model,kind in [(SalesReturn,'Return'),(SalesCreditNote,'Credit Note')]:
        for row in scope(model,user):
            invoice=row.snapshot['invoice'];line=row.snapshot['line'];posted=kind=='Credit Note' and row.status=='Issued'
            rows.append({**base(invoice,line),'key':kind+':'+str(row.pk),'date':row.date.isoformat(),'reference':row.number,'type':kind,'quantity':row.quantity,'debit':Decimal(0),'credit':row.total if posted else Decimal(0),'netDebit':Decimal(0),'netCredit':row.net if posted else Decimal(0),'status':row.status,'reason':row.reason,'documentValue':row.total,'note':'Not posted to general ledger. '+('Issued credit reduces operational sales once.' if posted else 'Informational document; only issued credit notes reduce sales.'),'documentId':str(row.pk),'returnId':str(row.pk) if kind=='Return' else str(row.sales_return_id)})
    rank={'Sale':0,'Return':1,'Credit Note':2};balances={}
    for r in sorted(rows,key=lambda r:(r['date'],rank[r['type']],r['reference'],r['key'])):
        k=(r['customerId'],r['currency']);balances[k]=balances.get(k,Decimal(0))+r['debit']-r['credit'];r['balance']=balances[k]
    references={str(r.pk):[{'id':str(c.pk),'number':c.number,'status':c.status} for c in r.credits.all()] for r in scope(SalesReturn,user).prefetch_related('credits')}
    for row in rows:
        if row['type']=='Return':row['linkedCreditNotes']=references.get(row['documentId'],[])
    return sorted(rows,key=lambda r:(r['date'],rank[r['type']],r['reference'],r['key'])),issues


def filter_rows(rows,params):
    start=params.get('from','');end=params.get('to','')
    if start:service.dated(start)
    if end:service.dated(end)
    if start and end and start>end:raise serializers.ValidationError('Invalid date range.')
    customer=params.get('customer_id')
    if customer:
        try:customer=int(customer)
        except ValueError:raise serializers.ValidationError('customer_id must be an integer.')
    result=[r for r in rows if (not start or r['date']>=start) and (not end or r['date']<=end) and (not customer or r['customerId']==customer) and (not params.get('product_id') or r['productId']==params['product_id']) and (not params.get('transaction_type') or r['type']==params['transaction_type']) and (not params.get('invoice_status') or r['invoiceStatus']==params['invoice_status']) and (not params.get('currency') or r['currency']==params['currency']) and (not params.get('search') or params['search'].lower() in ' '.join(str(r.get(k,'')) for k in ['reference','customer','product','reason']).lower())]
    ordering=params.get('ordering','date');field=ordering.lstrip('-')
    if field not in ['date','reference','customerId','debit','credit']:raise serializers.ValidationError('Unsupported ordering.')
    return sorted(result,key=lambda r:(r[field],r['key']),reverse=ordering.startswith('-'))


class SalesAPI(APIView):
    permission_classes=[permissions.IsAuthenticated]
    def finalize_response(self,request,response,*args,**kwargs):
        def wire(value):
            if isinstance(value,Decimal):return format(value,'f')
            if isinstance(value,dict):return {k:wire(v) for k,v in value.items()}
            if isinstance(value,list):return [wire(v) for v in value]
            return value
        if isinstance(response,Response):response.data=wire(response.data)
        return super().finalize_response(request,response,*args,**kwargs)
    def initial(self,request,*args,**kwargs):
        super().initial(request,*args,**kwargs);service.access(request.user,'view')
        if not {m._meta.db_table for m in [SalesReturn,SalesCreditNote,SalesReturnInspection]}.issubset(set(connection.introspection.table_names())):
            error=APIException({'code':'sales_migrations_required','detail':'Sales persistence migrations are not installed on this API.'});error.status_code=503;raise error
    def get(self,request,resource,pk=None,action=None,customer_id=None):
        user=request.user
        if resource=='bootstrap':
            from .shared_entries import permitted,SCOPES
            source_invoices,issues=service.invoices() if service.can(user,'view_all') or permitted(user,SCOPES['invoices'][0]) else ([],[])
            returns=[dto(r,user) for r in scope(SalesReturn,user)];credits=[dto(c,user) for c in scope(SalesCreditNote,user)]
            if not source_invoices:source_invoices=list({r['invoice']['id']:r['invoice'] for r in returns}.values())
            rows=ledger(user,reporting=service.can(user,'view_all'))[0]
            return Response({'version':1,'invoices':source_invoices,'state':{'returns':returns,'credits':credits,'returnSequence':0,'creditSequence':0},'ledger':rows,'issues':issues,'capabilities':{v:service.can(user,v) for v in ['edit','review','inspect','issue','view_all']},'limitations':{'restock':False,'credit_gl_posting':False,'taxed_credit_issuance':False,'partial_credits':False,'receivables':False}})
        if resource in ['ledger','dashboard']:
            rows,issues=ledger(user);params=request.query_params.copy()
            if customer_id is not None:params['customer_id']=str(customer_id)
            selected=filter_rows(rows,params)
            if resource=='ledger':return self.paginate(request,selected)
            summaries={};monthly={};customers={}
            for r in selected:
                currency=r['currency'];s=summaries.setdefault(currency,{'gross':Decimal(0),'issued_credit':Decimal(0),'approved_return':Decimal(0),'net':Decimal(0),'invoice_ids':set(),'customer_ids':set(),'return_count':0})
                s['gross']+=r['debit'];s['issued_credit']+=r['credit'];s['net']+=r['debit']-r['credit']
                if r['type']=='Sale' and r['invoiceStatus'] not in ['Draft','Cancelled']:s['invoice_ids'].add(r['documentId']);s['customer_ids'].add(r['customerId'])
                if r['type']=='Return':
                    s['return_count']+=1
                    if r['status']=='Approved':s['approved_return']+=SalesReturn.objects.get(pk=r['documentId']).total
                for bucket,key in [(monthly,(currency,r['date'][:7])),(customers,(currency,r['customerId']))]:
                    value=bucket.setdefault(key,{'sales':Decimal(0),'credits':Decimal(0),'returns':Decimal(0)});value['sales']+=r['debit'];value['credits']+=r['credit']
                    if r['type']=='Return' and r['status']=='Approved':value['returns']+=SalesReturn.objects.get(pk=r['documentId']).total
            for s in summaries.values():s['invoice_count']=len(s.pop('invoice_ids'));s['customer_count']=len(s.pop('customer_ids'))
            return Response({'currencies':summaries,'monthly':[{'currency':k[0],'month':k[1],**v} for k,v in monthly.items()],'customers':[{'currency':k[0],'customer_id':k[1],**v} for k,v in customers.items()],'issues':issues,'receivables':None,'gl_posted':False})
        model=self.model(resource)
        if pk:
            row=self.row(model,pk,user)
            if action=='pdf':
                if model!=SalesCreditNote:raise NotFound()
                if row.status!='Issued':raise PermissionDenied('Only issued credit notes have an official PDF. Use the explicitly identified preview endpoint.')
                return self.pdf(row,False)
            if action=='preview':
                if model!=SalesCreditNote:raise NotFound()
                return self.pdf(row,True)
            if action is not None:raise NotFound()
            return Response(dto(row,user))
        rows=[dto(r,user) for r in scope(model,user)]
        for key in ['status','customer_id','invoice_id']:
            if request.query_params.get(key):rows=[r for r in rows if str(r['invoice']['customerId'] if key=='customer_id' else r['invoice']['id'] if key=='invoice_id' else r['status'])==request.query_params[key]]
        return self.paginate(request,rows)
    def post(self,request,resource,pk=None,action=None):
        service.require(isinstance(request.data,dict),'Supply a JSON command object.')
        model=self.model(resource)
        if not pk:
            fn=service.save_return if model==SalesReturn else service.save_credit
            row=fn(request.data,request.user);return Response(dto(row,request.user),status=201)
        self.row(model,pk,request.user)
        if action=='inspect' and model==SalesReturn:row=service.inspect(pk,request.data,request.user)
        elif action=='transition':row=service.transition(model,pk,request.data,request.user)
        elif action in ['submit','approve','reject','issue','cancel','pending']:
            target={'submit':'Submitted','approve':'Approved','reject':'Rejected','issue':'Issued','cancel':'Cancelled','pending':'Pending Approval'}[action]
            row=service.transition(model,pk,{**request.data,'status':target},request.user)
        else:raise NotFound()
        return Response(dto(row,request.user))
    def patch(self,request,resource,pk=None,action=None):
        model=self.model(resource)
        if not pk or action:raise NotFound()
        self.row(model,pk,request.user);fn=service.save_return if model==SalesReturn else service.save_credit
        return Response(dto(fn(request.data,request.user,pk),request.user))
    @staticmethod
    def model(resource):
        if resource not in ['returns','credit-notes']:raise NotFound()
        return SalesReturn if resource=='returns' else SalesCreditNote
    @staticmethod
    def row(model,pk,user):
        row=scope(model,user).filter(pk=pk).first()
        if not row:raise NotFound()
        service.object_access(user,row);return row
    @staticmethod
    def paginate(request,rows):
        paginator=PageNumberPagination();paginator.page_size=50
        return paginator.get_paginated_response(paginator.paginate_queryset(rows,request))
    @staticmethod
    def pdf(row,preview):
        from .sales_pdf import credit_pdf
        response=HttpResponse(credit_pdf(row,preview),content_type='application/pdf');response['Content-Disposition']=f'attachment; filename="{row.number}{"-DRAFT" if preview else ""}.pdf"';return response
