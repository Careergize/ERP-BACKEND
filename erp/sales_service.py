"""Source-backed Sales commands. Never modifies stock or accounting balances."""
import json
import time
from copy import deepcopy
from functools import wraps
from decimal import Decimal, ROUND_FLOOR
from uuid import UUID, uuid4
from django.db import transaction, connection, OperationalError
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, APIException, NotFound
from .models import SharedEntry, EntryRevision, WorkflowOrder, Customer
from .sales_models import SalesReturn, SalesCreditNote, SalesReturnInspection
from .shared_entries import canonical
from .operational_records import quantity, money, dated, require


class Conflict(APIException):
    status_code=409
    default_detail='Concurrent or repeated Sales command conflicts with saved data. Reload before editing.'


def can(user,verb):return bool(user.is_authenticated and user.is_active and user.has_perm('erp.'+verb+'_sales'))
def access(user,verb):
    if not can(user,verb):raise PermissionDenied('Your account lacks the required Sales permission.')
def visible(user,row):return can(user,'view') and (row.created_by_id==user.pk or any(can(user,v) for v in ['view_all','review','inspect']))
def object_access(user,row):
    if not visible(user,row):raise NotFound('Sales document not found in your authorized scope.')


def serialized(fn):
    @wraps(fn)
    def run(*args,**kwargs):
        for attempt in range(5):
            try:
                with transaction.atomic():
                    # Existing invoice source is the common allocation mutex. First SQL is
                    # a no-op write for SQLite; PostgreSQL locks the same source row.
                    if SharedEntry.objects.filter(pk='invoices').update(version=F('version'))!=1:
                        raise serializers.ValidationError('The invoice source register is not initialized.')
                    return fn(*args,**kwargs)
            except OperationalError as e:
                if connection.vendor!='sqlite' or not any(s in str(e).lower() for s in ['locked','busy']):raise
                if attempt==4 or connection.in_atomic_block:
                    error=APIException('Sales is busy. Retry with the same command ID.');error.status_code=503;raise error
                time.sleep(.02*(attempt+1))
    return run


def strict(data,allowed,required=()):
    require(isinstance(data,dict),'Supply a JSON object.')
    require(not set(data)-set(allowed),'Unexpected or read-only fields were supplied.')
    require(all(k in data for k in required),'Required command fields are missing.')
def date_check(value,earliest):
    d=dated(value);require(earliest<=d<=timezone.localdate(),'Date must follow the source and cannot be future.');return d
def decimal_quantity(value):
    n=quantity(value,True);require(n==n.quantize(Decimal('.000001')),'Use at most six quantity decimal places.');return n
def clean(value,limit=500):
    require(isinstance(value,str) and value.strip() and len(value)<=limit,'Enter nonempty text within the field length limit.');return value.strip()


def allocate(value,weights):
    total=sum(weights,Decimal(0));count=int(money(value)*100)
    require(total>0 or count==0,'Zero-price invoice cannot carry allocated charges.')
    if not total:return [Decimal(0)]*len(weights)
    raw=[Decimal(count)*w/total for w in weights];parts=[int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in raw]
    order=sorted(range(len(raw)),key=lambda i:(-(raw[i]-parts[i]),i))
    for i in range(count-sum(parts)):parts[order[i%len(order)]]+=1
    return [Decimal(v)/100 for v in parts]


def invoice_projection(raw):
    """Project the existing immutable invoice register; no alternate invoice storage."""
    try:
        require(isinstance(raw,dict) and isinstance(raw.get('id'),str) and isinstance(raw.get('number'),str),'Invoice requires stable source identifiers.')
        require(isinstance(raw.get('currency'),str) and len(raw['currency'])==3 and raw['currency'].isalpha() and raw['currency'].isupper(),'Invoice currency is invalid.')
        order=WorkflowOrder.objects.get(pk=int(str(raw['orderId']).removeprefix('wf-order-')))
        customer_id=str(order.data.get('customerId','')).removeprefix('db:')
        customer=Customer.objects.get(pk=int(customer_id))
        lines=raw['items'];require(isinstance(lines,list) and lines,'Invoice has no lines.')
        weights=[quantity(l['amount']) for l in lines]
        net=money(quantity(raw['total'])-quantity(raw['tax'])-quantity(raw.get('led') or 0));require(net>=0,'Invalid invoice charge snapshots.')
        nets=allocate(net,weights);vats=allocate(quantity(raw['tax']),weights);leds=allocate(quantity(raw.get('led') or 0),weights)
        projected=[];source_led=False
        for i,l in enumerate(lines):
            source=next((r for r in order.data['items'] if r['id']==l['itemId']),None)
            require(source is not None and source['unit']==l['unit'],'Invoice/order line identity mismatch.')
            source_led|=quantity((source.get('ledSnapshot') or {}).get('selectedAmount',0))>0
            projected.append({'key':f"{raw['id']}:{i}",'productId':str(source.get('itemId') or l['itemId']),'product':l['description'],'quantity':str(decimal_quantity(l['quantity'])),'unit':l['unit'],'unitPrice':str(quantity(l['rate'])),'net':str(nets[i]),'vat':str(vats[i]),'led':str(leds[i]),'total':str(money(nets[i]+vats[i]+leds[i])),'reservedQuantity':str(reserved(raw['id'],f"{raw['id']}:{i}"))})
        return {'id':raw['id'],'number':raw['number'],'date':dated(raw['date']).isoformat(),'customerId':customer.pk,'customerKey':f'customer:{customer.pk}','customer':customer.name,'linkedCustomer':True,'orderId':str(order.pk),'orderNumber':raw.get('orderNumber',order.data.get('orderNumber','')),'currency':raw['currency'],'status':raw.get('status') if raw.get('status') in ['Draft','Cancelled','Issued'] else 'Recorded','taxMode':raw.get('taxMode','exclusive'),'taxAdjustmentRequired':bool(source_led or quantity(order.data.get('taxRate',0)) or quantity(raw['tax']) or quantity(raw.get('led') or 0)),'company':{'name':raw.get('seller',''),'address':raw.get('sellerAddress',''),'taxId':raw.get('sellerTax',''),'email':raw.get('email',''),'contact':raw.get('contact','')},'net':str(net),'vat':str(money(quantity(raw['tax']))),'led':str(money(quantity(raw.get('led') or 0))),'total':str(money(quantity(raw['total']))),'lines':projected}
    except (KeyError,ValueError,TypeError,AttributeError,StopIteration,Customer.DoesNotExist,WorkflowOrder.DoesNotExist):
        raise serializers.ValidationError('Invoice is missing a valid stable customer/order/line reference; review legacy source data.')


def invoices():
    entry=SharedEntry.objects.filter(pk='invoices').first();result=[];issues=[]
    for raw in entry.content if entry and isinstance(entry.content,list) else []:
        try:result.append(invoice_projection(raw))
        except serializers.ValidationError:issues.append(f"Invoice {raw.get('id','unknown') if isinstance(raw,dict) else 'unknown'} requires source/customer review.")
    return result,issues
def source(invoice_id,line_key):
    entry=SharedEntry.objects.get(pk='invoices')
    raw=next((r for r in entry.content if r.get('id')==invoice_id),None);require(raw is not None,'Original invoice does not exist.')
    invoice=invoice_projection(raw);require(invoice['status'] not in ['Draft','Cancelled'],'Draft/cancelled invoices cannot be returned.');line=next((l for l in invoice['lines'] if l['key']==line_key),None);require(line is not None,'Product line is not on this invoice.');return invoice,line
def reserved(invoice_id,line_key,exclude=None):
    return SalesReturn.objects.filter(invoice_id=invoice_id,line_key=line_key,status__in=['Submitted','Pending Approval','Approved']).exclude(pk=exclude).aggregate(n=Sum('quantity'))['n'] or Decimal(0)
def amounts(line,prior,qty):
    sold=quantity(line['quantity'],True);require(prior+qty<=sold,'Return exceeds remaining invoiced quantity, including pending reservations.')
    values={key:money(quantity(line[key])*(prior+qty)/sold)-money(quantity(line[key])*prior/sold) for key in ['net','vat','led']}
    return {**values,'total':sum(values.values(),Decimal(0))}


def command_start(data,user,action):
    try:command=UUID(str(data['commandId']))
    except (KeyError,TypeError,ValueError):raise serializers.ValidationError('Supply a commandId UUID.')
    fingerprint=canonical({'action':action,'payload':data});receipt=EntryRevision.objects.filter(command_id=command).first()
    if receipt:
        if receipt.actor_id!=user.pk or receipt.content.get('request_hash')!=fingerprint:raise Conflict()
        model=SalesReturn if receipt.content['kind']=='Return' else SalesCreditNote
        row=model.objects.get(pk=receipt.content['document_id']);object_access(user,row);return command,fingerprint,row
    return command,fingerprint,None
def audit(row,user,action,command,fingerprint,previous):
    kind='Return' if isinstance(row,SalesReturn) else 'Credit Note';key=f'sales-{row._meta.model_name}-{row.pk}'
    entry,_=SharedEntry.objects.get_or_create(pk=key,defaults={'content':{},'version':0,'updated_by':user})
    entry.version+=1;entry.updated_by=user
    content={'kind':kind,'document_id':str(row.pk),'action':action,'request_hash':fingerprint,'previous':previous,'next':document_snapshot(row),'gl_status':'Not posted'}
    entry.content=content;entry.save()
    EntryRevision.objects.create(entry=entry,version=entry.version,command_id=command,content=content,content_hash=canonical(content),actor=user)
def document_snapshot(row):return {'number':row.number,'status':row.status,'version':row.version,'quantity':str(row.quantity),'net':str(row.net),'vat':str(row.vat),'led':str(row.led),'total':str(row.total),'reason':row.reason,'remarks':row.remarks,'qc_status':getattr(row,'qc_status',None),'customer_id':row.customer_id}
def check_version(row,data):
    if type(data.get('expectedVersion')) is not int or data['expectedVersion']!=row.version:raise Conflict('Sales document version changed. Reload before editing/review.')


@serialized
def save_return(data,user,pk=None):
    access(user,'view');access(user,'edit')
    if pk is None:
        from .shared_entries import permitted,SCOPES
        if not permitted(user,SCOPES['invoices'][0]):raise PermissionDenied('Existing invoice read authority is required to create a return.')
    strict(data,['commandId','expectedVersion','invoiceId','lineKey','customerId','date','quantity','reason','condition','remarks'],['commandId','date','quantity','reason','condition','remarks'])
    cmd,fp,replay=command_start(data,user,'return-save:'+str(pk));
    if replay:return replay
    row=SalesReturn.objects.get(pk=pk) if pk else None
    if row:object_access(user,row);check_version(row,data);require(row.status=='Draft','Only drafts can be edited.')
    invoice,line=source(row.invoice_id if row else data.get('invoiceId'),row.line_key if row else data.get('lineKey'))
    require(data.get('customerId',invoice['customerId'])==invoice['customerId'],'Customer must match the original invoice.')
    if row:require(data.get('invoiceId',row.invoice_id)==row.invoice_id and data.get('lineKey',row.line_key)==row.line_key,'Original invoice and line cannot change.')
    qty=decimal_quantity(data['quantity']);values=amounts(line,reserved(invoice['id'],line['key'],pk),qty)
    previous=document_snapshot(row) if row else None
    if not row:
        identity=uuid4();row=SalesReturn(id=identity,number='SR-'+identity.hex.upper(),source_id='invoices',customer_id=invoice['customerId'],invoice_id=invoice['id'],line_key=line['key'],snapshot={'invoice':invoice,'line':line},created_by=user)
    else:row.version+=1
    row.date=date_check(data['date'],dated(invoice['date']));row.quantity=qty;row.reason=clean(data['reason']);row.condition=clean(data['condition'],100);row.remarks=clean(data['remarks'],5000)
    for k,v in values.items():setattr(row,k,v)
    row.contributors=sorted(set(row.contributors+[user.pk]));row.save();audit(row,user,'Return draft saved',cmd,fp,previous);return row


@serialized
def save_credit(data,user,pk=None):
    access(user,'view');access(user,'edit');strict(data,['commandId','expectedVersion','returnId','date','reason','remarks'],['commandId','date','reason','remarks'])
    cmd,fp,replay=command_start(data,user,'credit-save:'+str(pk))
    if replay:return replay
    row=SalesCreditNote.objects.get(pk=pk) if pk else None
    if row:object_access(user,row);check_version(row,data);require(row.status=='Draft','Only drafts can be edited.')
    if row:require(str(data.get('returnId',row.sales_return_id))==str(row.sales_return_id),'A saved credit note cannot change its originating return.')
    returned=SalesReturn.objects.filter(pk=row.sales_return_id if row else serializers.UUIDField().run_validation(data.get('returnId'))).first()
    require(returned is not None and returned.status=='Approved','An approved return is required.');object_access(user,returned)
    require(not SalesCreditNote.objects.filter(sales_return=returned).exclude(pk=pk).exclude(status__in=['Rejected','Cancelled']).exists(),'A current credit note already covers this return; partial credits are not authorized.')
    previous=document_snapshot(row) if row else None
    if not row:
        identity=uuid4();row=SalesCreditNote(id=identity,number='CN-'+identity.hex.upper(),sales_return=returned,source_id=returned.source_id,customer_id=returned.customer_id,invoice_id=returned.invoice_id,line_key=returned.line_key,snapshot=deepcopy(returned.snapshot),created_by=user,quantity=returned.quantity,net=returned.net,vat=returned.vat,led=returned.led,total=returned.total)
    else:row.version+=1
    row.date=date_check(data['date'],returned.date);row.reason=clean(data['reason']);row.remarks=clean(data['remarks'],5000);row.contributors=sorted(set(row.contributors+[user.pk]));row.save();audit(row,user,'Credit draft saved',cmd,fp,previous);return row


TRANSITIONS={'Draft':['Submitted','Cancelled'],'Submitted':['Pending Approval','Rejected','Cancelled'],'Pending Approval':['Approved','Rejected','Cancelled'],'Approved':['Issued'],'Issued':[],'Rejected':[],'Cancelled':[]}
def allowed_actions(row,user):
    if not visible(user,row):return []
    result=[]
    for action in TRANSITIONS[row.status]:
        if action=='Issued':
            if isinstance(row,SalesCreditNote) and can(user,'issue') and user.pk not in row.contributors and row.total>0 and not row.vat and not row.led and row.snapshot['invoice'].get('taxAdjustmentRequired',True) is False:result.append(action)
        elif action in ['Pending Approval','Approved','Rejected']:
            if can(user,'review') and user.pk not in row.contributors:result.append(action)
        elif can(user,'edit'):result.append(action)
    return result


@serialized
def transition(model,pk,data,user):
    access(user,'view');strict(data,['commandId','expectedVersion','status','remarks'],['commandId','expectedVersion','status','remarks'])
    cmd,fp,replay=command_start(data,user,f'{model._meta.model_name}:{pk}:transition')
    if replay:return replay
    row=model.objects.get(pk=pk);object_access(user,row);check_version(row,data);target=data['status']
    if target not in allowed_actions(row,user):raise PermissionDenied('This action requires an authorized independent user and an eligible document state. Taxed/LED issuance awaits confirmed adjustment rules.')
    clean(data['remarks'],5000);previous=document_snapshot(row)
    if isinstance(row,SalesReturn) and target=='Submitted':
        values=amounts(row.snapshot['line'],reserved(row.invoice_id,row.line_key,row.pk),row.quantity)
        for key,v in values.items():setattr(row,key,v)
        row.contributors=sorted(set(row.contributors+[user.pk]))
    if target=='Approved':
        if isinstance(row,SalesReturn):
            require(row.inspections.exists(),'QC inspection must precede return approval.')
            require(reserved(row.invoice_id,row.line_key,row.pk)+row.quantity<=quantity(row.snapshot['line']['quantity']),'Return reservations exceed sold quantity.')
            approved=SalesReturn.objects.filter(invoice_id=row.invoice_id,line_key=row.line_key,status='Approved').aggregate(n=Sum('quantity'))['n'] or Decimal(0)
            for key,value in amounts(row.snapshot['line'],approved,row.quantity).items():setattr(row,key,value)
        else:require(row.sales_return.status=='Approved','Return must remain approved.')
        row.reviewed_by=user;row.approved_at=timezone.now()
    if target=='Issued':
        company=row.snapshot['invoice'].get('company',{})
        require(company.get('name') and company.get('address'),'Company name and address are required for issuance.')
        row.issued_at=timezone.now()
    row.status=target;row.version+=1;row.save();audit(row,user,target,cmd,fp,previous);return row


@serialized
def inspect(pk,data,user):
    access(user,'view');access(user,'inspect');strict(data,['commandId','expectedVersion','result','quantity','acceptedQuantity','remarks','qcReference'],['commandId','expectedVersion','result','quantity','acceptedQuantity','remarks'])
    from .operational_policy import authorize
    authorize(user,'qc-record')
    cmd,fp,replay=command_start(data,user,f'return:{pk}:inspect')
    if replay:return replay
    row=SalesReturn.objects.get(pk=pk);object_access(user,row);check_version(row,data);require(row.status in ['Submitted','Pending Approval'],'Inspect submitted/pending returns before approval.')
    qty=decimal_quantity(data['quantity']);accepted=quantity(data['acceptedQuantity']);require(accepted==accepted.quantize(Decimal('.000001')),'Use at most six decimals.')
    require(qty<=row.quantity and accepted<=qty and data['result'] in ['Pass','Fail','Hold'],'Inspection quantities or result are invalid.')
    require(data['result']=='Pass' or accepted==0,'Failed/held goods cannot be accepted.')
    reference=data.get('qcReference','')
    if reference:
        from .operational_records import qc_result,shared
        record=next((i for i in shared('quality-state',{}).get('inspections',[]) if i.get('id')==reference),None)
        require(record and record.get('subject',{}).get('salesReturnId')==str(row.pk),'QC reference must identify this exact return.')
        require(qc_result(record)==data['result'],'Existing QC checklist result must match.')
    previous=document_snapshot(row)
    SalesReturnInspection.objects.create(sales_return=row,result=data['result'],inspected_quantity=qty,accepted_quantity=accepted,remarks=clean(data['remarks'],5000),inspector=user,qc_reference=reference)
    row.qc_status=data['result'];row.version+=1;row.save();audit(row,user,'QC '+data['result'],cmd,fp,previous);return row
