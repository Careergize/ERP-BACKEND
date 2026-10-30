from django.db import transaction, IntegrityError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from .models import Book, Account, Party, Document, PostingEvent, AccountGroup, CostCategory, Currency, VoucherType
from .services import ACCOUNTS, require, text, day, post
from .reports import report, document_data


def access(user, write=False, admin=False):
    roles = {'admin'} if admin else {'admin','accounts'} if write else {'admin','accounts','project_manager','production_manager'}
    if not user.is_authenticated or not user.is_active or not user.effective_role or (not user.is_superuser and user.effective_role not in roles):
        raise PermissionDenied('Your account cannot perform this accounting action.')


class Bootstrap(APIView):
    def get(self, request):
        access(request.user)
        b = Book.objects.first()
        return Response({'book': {'company':b.company,'currency':b.currency,'startDate':str(b.start_date),'fiscalMonth':b.fiscal_month,'valuation':b.valuation,'ledAccount':b.led_account,'closedThrough':str(b.closed_through) if b.closed_through else ''} if b else None,
            'accounts':list(Account.objects.order_by('code').values('code','name','kind','cash','control','group_id')),
            'parties':list(Party.objects.order_by('name').values('id','name','kind','source','group_id')),
            'groups':list(AccountGroup.objects.order_by('name').values('id','name','kind','parent_id')),
            'costCategories':list(CostCategory.objects.order_by('name').values('id','name')),
            'currencies':list(Currency.objects.order_by('code').values('code','name','symbol')),
            'voucherTypes':list(VoucherType.objects.order_by('name').values('id','name','base_kind')),
            'canWrite':request.user.is_superuser or request.user.effective_role in {'admin','accounts'},
            'canConfigure':request.user.is_superuser or request.user.effective_role == 'admin'})

    @transaction.atomic
    def post(self, request):
        access(request.user, admin=True)
        import re
        require(not Book.objects.exists(), 'Accounting is already configured. Currency/cutover cannot change after activation.')
        p = request.data
        require(re.fullmatch('[A-Z]{3}', p.get('currency','')) is not None, 'Enter a three-letter reporting currency.')
        month = p.get('fiscalMonth',1)
        require(type(month) is int and 1 <= month <= 12, 'Fiscal start month must be 1–12.')
        require(p.get('valuation') == 'weighted-average', 'This release supports moving weighted-average valuation.')
        Book.objects.create(company=text(p.get('company'),200),currency=p['currency'],start_date=day(p.get('startDate')),fiscal_month=month,created_by=request.user)
        for code,name,kind,cash in ACCOUNTS:
            Account.objects.create(code=code,name=name,kind=kind,cash=cash,control=code in {'1300','1400','1500','2100','2200','2300','1600'})
        from .masters import seed_masters
        seed_masters(book=Book.objects.first(), account_model=Account)
        return self.get(request)


class Masters(APIView):
    @transaction.atomic
    def post(self, request, kind):
        access(request.user, write=True)
        p = request.data
        if kind == 'parties':
            require(p.get('kind') in {'customer','supplier'}, 'Select customer or supplier.')
            group = master_group(p, 'asset' if p['kind']=='customer' else 'liability')
            obj = Party.objects.create(name=text(p.get('name'),200),kind=p['kind'],group=group)
            return Response({'id':obj.pk})
        if kind == 'accounts':
            access(request.user, admin=True)
            require(p.get('kind') in {'asset','liability','equity','income','expense'}, 'Select an account type.')
            require(type(p.get('cash',False)) is bool and (not p.get('cash') or p['kind']=='asset'), 'Cash/bank accounts must be assets.')
            try:
                obj = Account.objects.create(code=text(p.get('code'),30),name=text(p.get('name'),150),kind=p['kind'],cash=p.get('cash',False),group=master_group(p,p['kind']))
            except IntegrityError:
                raise ValidationError('Account code already exists.')
            return Response({'id':obj.pk})
        access(request.user, admin=True)
        if kind == 'group-membership':
            model = Account if p.get('ledgerType')=='account' else Party if p.get('ledgerType')=='party' else None
            require(model is not None, 'Select a ledger type.')
            require((model is Account and isinstance(p.get('ledger'),str)) or
                    (model is Party and type(p.get('ledger')) is int), 'Select a valid ledger reference.')
            obj = model.objects.select_for_update().filter(pk=p.get('ledger')).first()
            require(obj is not None, 'Select an existing ledger.')
            expected = obj.kind if model is Account else 'asset' if obj.kind=='customer' else 'liability'
            obj.group = master_group(p, expected)
            obj.save(update_fields=['group'])
            return Response({'id':obj.pk, 'group':obj.group_id})
        try:
            if kind == 'groups':
                require(p.get('kind') in {'asset','liability','equity','income','expense'}, 'Select a group classification.')
                parent = master_group({'group':p.get('parent')}, p['kind'])
                obj = AccountGroup.objects.create(name=text(p.get('name'),150),kind=p['kind'],parent=parent)
            elif kind == 'cost-categories':
                obj = CostCategory.objects.create(name=text(p.get('name'),150))
            elif kind == 'currencies':
                import re
                code = p.get('code')
                require(isinstance(code,str) and re.fullmatch('[A-Z]{3}',code), 'Use a three-letter uppercase currency code.')
                symbol = p.get('symbol','')
                require(isinstance(symbol,str) and len(symbol)<=12, 'Currency symbol must be at most 12 characters.')
                obj = Currency.objects.create(code=code,name=text(p.get('name'),100),symbol=symbol)
            elif kind == 'voucher-types':
                from .masters import BASE_VOUCHERS
                require(p.get('baseKind') in BASE_VOUCHERS, 'Choose a supported voucher behaviour.')
                obj = VoucherType.objects.create(name=text(p.get('name'),100),base_kind=p['baseKind'])
            else:
                raise ValidationError('Unknown master type.')
        except IntegrityError:
            raise ValidationError('A master with this name or code already exists.')
        return Response({'id':obj.pk},status=201)


def master_group(payload, expected):
    value = payload.get('group')
    if value in (None, ''): return None
    require(type(value) is int, 'Select a saved account group.')
    group = AccountGroup.objects.filter(pk=value).first()
    require(group is not None and group.kind==expected, 'Group classification must match the ledger or parent group.')
    return group


class Commands(APIView):
    def post(self, request):
        access(request.user, write=True)
        try:
            d = post(request.data,request.user)
        except IntegrityError:
            raise ValidationError('Duplicate document number or concurrent command. Reload before retrying.')
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValidationError('Complete all required fields with valid types and references.')
        return Response(document_data(d),status=201)


class Reports(APIView):
    def get(self,request):
        access(request.user)
        return Response(report(request.query_params.get('start'),request.query_params.get('end'),request.query_params.get('party'),request.query_params.get('account')))


class ClosePeriod(APIView):
    @transaction.atomic
    def post(self,request):
        access(request.user, admin=True)
        b = Book.objects.select_for_update().filter(pk=1).first()
        require(b is not None, 'Configure accounting first.')
        when = day(request.data.get('date'))
        require(when >= b.start_date and (not b.closed_through or when > b.closed_through), 'Close date must advance the existing lock.')
        b.closed_through = when; b.save(update_fields=['closed_through'])
        return Response({'closedThrough':str(when)})


class PostingQueue(APIView):
    def get(self,request):
        access(request.user)
        return Response([{'source':e.source,'payload':e.payload,'error':e.error,'createdAt':e.created_at.isoformat()} for e in PostingEvent.objects.filter(document__isnull=True,cancelled=False).order_by('created_at')])

    @transaction.atomic
    def post(self,request):
        access(request.user,write=True)
        e=PostingEvent.objects.select_for_update().filter(pk=request.data.get('source'),document__isnull=True,cancelled=False).first()
        require(e is not None, 'Select an unposted source event.')
        p=dict(e.payload)
        # Only genuinely absent inputs may be supplied; source quantities/prices
        # and identities cannot be overwritten from the accounting screen.
        if p.get('blocked'):
            if p['blocked'].startswith('LED account'):
                b=Book.objects.first()
                require(bool(b.led_account),'Configure the LED liability account in Setup first.')
                for row in p['items']:
                    if row.get('description')=='LED levy':row['taxAccount']=b.led_account
                p.pop('blocked')
            else:
                require(p.get('blocked','').startswith('Map this invoice'), p['blocked'])
                p['party']=request.data.get('party'); p.pop('blocked')
        if p.get('currency') and Book.objects.first().currency != p['currency']:
            p['fx']=request.data.get('fx')
            if p['kind']=='stock-receipt':
                from .services import dec, money
                p['value']=str(money(dec(p['value'])*dec(p['fx'],8,True))); p['currency']=Book.objects.first().currency; p['fx']='1'
        if p['kind']=='invoice' and not p.get('party'):
            p['party']=request.data.get('party')
        if p['kind']=='stock-receipt' and 'value' not in p:
            p['value']=request.data.get('value')
        d=post(p,request.user,source=e.source)
        e.document=d; e.error=''; e.payload=p; e.save(update_fields=['document','error','payload'])
        return Response(document_data(d))


class TaxMapping(APIView):
    @transaction.atomic
    def post(self,request):
        access(request.user,admin=True)
        from .services import account
        b=Book.objects.select_for_update().filter(pk=1).first()
        require(b is not None,'Configure accounting first.')
        code=request.data.get('ledAccount')
        require(account(code).kind=='liability','LED must map to a liability account.')
        b.led_account=code;b.save(update_fields=['led_account'])
        return Response({'ledAccount':code})


class ImportMasters(APIView):
    @transaction.atomic
    def post(self,request):
        access(request.user,write=True)
        from erp.models import Customer, Supplier
        for model,kind in [(Customer,'customer'),(Supplier,'supplier')]:
            for row in model.objects.filter(is_active=True):
                Party.objects.get_or_create(source=f'{kind}:{row.pk}',defaults={'name':row.name,'kind':kind})
        return Response({'detail':'Canonical customers and suppliers linked by stable IDs; names were not merged.'})


class PurchaseBills(APIView):
    def get(self,request):
        access(request.user)
        from erp.models import PurchaseOrder
        from .models import DocumentLine
        from .services import ZERO
        rows=[]
        for order in PurchaseOrder.objects.prefetch_related('lines').order_by('-order_date','-pk')[:500]:
            lines=[]
            for line in order.lines.all():
                billed=sum((i.quantity for i in DocumentLine.objects.filter(item=f'purchase-line:{line.pk}',document__kind='bill',document__reversal__isnull=True)),ZERO)
                lines.append({'id':line.pk,'description':line.description,'unit':line.unit,'quantity':str(line.quantity),'price':str(line.unit_price),'remaining':str(line.quantity-billed)})
            rows.append({'id':order.pk,'number':order.number,'supplier':order.supplier_name,'currency':order.currency,'date':str(order.order_date),'lines':lines})
        return Response(rows)

    @transaction.atomic
    def post(self,request):
        access(request.user,write=True)
        from erp.models import PurchaseOrder
        from .models import DocumentLine
        from .services import ZERO, dec
        b=Book.objects.select_for_update().filter(pk=1).first()
        require(b is not None,'Configure accounting first.')
        order=PurchaseOrder.objects.select_for_update().filter(pk=request.data.get('purchaseOrder')).first()
        require(order is not None,'Select a saved purchase order.')
        p=request.data
        # A replay is handled by the command before recalculating remaining scope.
        previous=Document.objects.filter(command=p.get('commandId')).first()
        if previous:
            require(previous.actor_id==request.user.pk and previous.source==f'purchase-bill:{order.pk}:{p.get("number")}', 'Command ID belongs to a different bill.')
            expected={i.item.removeprefix('purchase-line:'):i.quantity for i in previous.items.all()}
            supplied={str(k):dec(v,6) for k,v in p.get('quantities',{}).items() if dec(v,6)>0}
            require(expected==supplied and previous.date==day(p.get('date')) and previous.party_id==p.get('party') and previous.fx==dec(p.get('fx','1'),8,True), 'This command was already accepted with different bill details.')
            require(all(i.tax==dec(p.get('taxes',{}).get(i.item.removeprefix('purchase-line:'),'0')) for i in previous.items.all()),'This bill was already accepted with different tax amounts.')
            return Response(document_data(previous))
        requested=p.get('quantities',{})
        require(isinstance(requested,dict),'Supply quantities by purchase-line ID.')
        require(isinstance(p.get('taxes',{}),dict),'Supply tax amounts by purchase-line ID.')
        items=[]
        for line in order.lines.all():
            qty=dec(requested.get(str(line.pk),'0'),6)
            if not qty:continue
            billed=sum((i.quantity for i in DocumentLine.objects.filter(item=f'purchase-line:{line.pk}',document__kind='bill',document__reversal__isnull=True)),ZERO)
            require(qty <= line.quantity-billed,'Billed quantity exceeds this purchase line’s remaining scope.')
            items.append({'item':f'purchase-line:{line.pk}','description':line.description,'quantity':str(qty),'unit':line.unit,'price':str(line.unit_price),'tax':str(dec(p.get('taxes',{}).get(str(line.pk),'0'))),'account':'2300' if line.stock_item_id else '6100'})
        payload={k:p[k] for k in ['commandId','number','date','party','fx'] if k in p}
        payload.update(kind='bill',currency=order.currency,items=items,notes=f'Supplier bill matched to {order.number}; explicit tax amounts are recoverable input tax.')
        doc=post(payload,request.user,source=f'purchase-bill:{order.pk}:{p.get("number")}')
        return Response(document_data(doc),status=201)


class CaptureSources(APIView):
    @transaction.atomic
    def post(self,request):
        access(request.user,write=True)
        from erp.models import SharedEntry, PurchaseOrder, GoodsReceivedNote
        from inventory.models import RawMovement
        from .integration import shared_saved, purchase_received
        from .signals import raw_movement
        b=Book.objects.select_for_update().first()
        require(b is not None,'Configure accounting first.')
        invoices=SharedEntry.objects.filter(pk='invoices').first()
        shared_saved('invoices',[i for i in (invoices.content if invoices else []) if i['date']>=str(b.start_date)],None,request.user)
        ledger=SharedEntry.objects.filter(pk='inventory-ledger').first()
        shared_saved('inventory-ledger',{'dispatches':[d for d in (ledger.content if ledger else {}).get('dispatches',[]) if d['date']>=str(b.start_date)],'movements':[m for m in (ledger.content if ledger else {}).get('movements',[]) if m['date']>=str(b.start_date)]},None,request.user)
        for order in PurchaseOrder.objects.filter(order_date__gte=b.start_date,receipt_method='immediate'):
            purchase_received(order,request.user)
        for note in GoodsReceivedNote.objects.filter(receipt_date__gte=b.start_date).select_related('purchase_order'):
            purchase_received(note.purchase_order,request.user,note)
        for movement in RawMovement.objects.filter(date__gte=b.start_date).select_related('material','created_by'):
            raw_movement(RawMovement,movement,True)
        return Response({'detail':'Eligible saved source events captured. Missing accounting inputs remain in Posting Queue.'})
