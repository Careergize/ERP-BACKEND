from django.db.models import Q, Case, When, IntegerField
from rest_framework import serializers, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from .spare_opening_api import SpareOpeningViewMixin
from .catalog_models import SpareItem, Consumable, SpareStockMovement, ConsumableStockMovement
from .catalogue_service import balance, CATEGORIES, LEGACY_OFFICE, DEPARTMENTS, save_item, post_movement, kind

COMMON_FIELDS = ['id','code','name','description','unit','is_active','supplier','department','storage_location','expense_classification','unit_cost','created_by','updated_by','created_at','updated_at','stock','last_purchase_date','creation_request_id','opening_stock']
READ_ONLY = ['id','created_by','updated_by','created_at','updated_at','stock','last_purchase_date']

class ItemSerializer(serializers.ModelSerializer):
    creation_request_id = serializers.UUIDField(required=False)
    stock = serializers.SerializerMethodField()
    last_purchase_date = serializers.SerializerMethodField()
    opening_stock = serializers.DecimalField(max_digits=20,decimal_places=6,min_value=0,required=False,write_only=True)
    def get_stock(self,obj):
        totals=balance(obj)
        current=totals['current_stock']
        low=current is not None and (current <= obj.minimum_stock if isinstance(obj,Consumable) else current < obj.minimum_stock)
        return {**{k:None if v is None else str(v) for k,v in totals.items()},'below_minimum':low}
    def get_last_purchase_date(self,obj):
        dates=[m.date for m in obj.movements.all() if m.movement_type=='IN' and not m.is_opening and m.date is not None]
        return max(dates).isoformat() if dates else None
    def validate(self,attrs):
        if self.instance and attrs.get('code',self.instance.code)!=self.instance.code:
            raise serializers.ValidationError({'code':'Item codes are immutable.'})
        if attrs.get('department') and attrs['department'] not in DEPARTMENTS:
            raise serializers.ValidationError({'department':'Select a supported department.'})
        if self.instance and 'creation_request_id' in attrs:
            raise serializers.ValidationError({'creation_request_id':'Creation identity cannot be edited.'})
        return attrs

class SpareSerializer(ItemSerializer):
    opening_state=serializers.SerializerMethodField()
    def get_opening_state(self,obj):
        if balance(obj)['opening_stock'] is not None:return {'status':'INITIALIZED','request_id':None}
        view=self.context.get('view')
        if view and not getattr(view,'_spares_opening_storage',(True,'ready'))[0]:return {'status':'UNAVAILABLE','request_id':None}
        pending=obj.opening_requests.filter(status='PENDING').first()
        return {'status':'PENDING' if pending else 'UNKNOWN','request_id':str(pending.pk) if pending else None}
    class Meta:
        model=SpareItem
        fields=COMMON_FIELDS+['category','minimum_stock','opening_state']
        read_only_fields=READ_ONLY
        extra_kwargs={'code':{'validators':[]},'minimum_stock':{'min_value':0},'unit_cost':{'min_value':0},'creation_request_id':{'validators':[]}}

class ConsumableSerializer(ItemSerializer):
    class Meta:
        model=Consumable
        fields=COMMON_FIELDS+['category','subcategory','minimum_stock','reorder_quantity','machine']
        read_only_fields=READ_ONLY
        extra_kwargs={'code':{'required':False,'validators':[]},'minimum_stock':{'min_value':0},'reorder_quantity':{'min_value':0},'unit_cost':{'min_value':0},'creation_request_id':{'validators':[]}}
    def validate(self,attrs):
        attrs=super().validate(attrs)
        category=attrs.get('category',self.instance.category if self.instance else '')
        subcategory=attrs.get('subcategory',self.instance.subcategory if self.instance else '')
        allowed=CATEGORIES.get(category,{})
        if self.instance and category=='Office & Administration Consumables': allowed=LEGACY_OFFICE
        if category or subcategory:
            if subcategory not in allowed:
                raise serializers.ValidationError({'subcategory':'Select a Consumables category and its subcategory. Spares must use their dedicated module.'})
        elif not self.instance and not attrs.get('code'):
            raise serializers.ValidationError({'category':'Category and subcategory are required.'})
        return attrs

MOVEMENT_FIELDS=['id','item','request_id','movement_type','is_opening','quantity','unit','reference','transaction_reference','recorded_by','created_at','date','unit_cost','supplier','department','storage_location','received_issued_by','issued_to','purpose','machine','job_reference','purchase_order_reference','invoice_reference','remarks','item_code','item_name']
class MovementSerializer(serializers.ModelSerializer):
    item_code=serializers.CharField(source='item.code',read_only=True)
    item_name=serializers.CharField(source='item.name',read_only=True)
    class Meta:
        model=SpareStockMovement
        fields=MOVEMENT_FIELDS
        read_only_fields=fields
class ConsumableMovementSerializer(MovementSerializer):
    class Meta(MovementSerializer.Meta):
        model=ConsumableStockMovement

class CatalogPermission(permissions.BasePermission):
    def has_permission(self,request,view):
        if not request.user.is_authenticated or not request.user.is_active:return False
        model=view.queryset.model._meta.model_name
        verb={'create':'add','update':'change','partial_update':'change'}.get(view.action,'view')
        return request.user.has_perm(f'inventory.{verb}_{model}')

class RegisterViewSet(viewsets.ModelViewSet):
    permission_classes=[permissions.IsAuthenticated,CatalogPermission]
    http_method_names=['get','post','put','patch','head','options']
    def movement_model(self):
        return ConsumableStockMovement if self.queryset.model==Consumable else SpareStockMovement
    def movement_serializer(self):
        return ConsumableMovementSerializer if self.queryset.model==Consumable else MovementSerializer
    def movement_permission(self,verb):
        return self.request.user.has_perm(f'inventory.{verb}_{self.movement_model()._meta.model_name}')
    def require_history(self):
        if not self.movement_permission('view'):raise PermissionDenied('Movement history permission required.')
    def get_queryset(self):
        qs=super().get_queryset().prefetch_related('movements')
        p=self.request.query_params
        if p.get('search'):qs=qs.filter(Q(name__icontains=p['search'])|Q(code__icontains=p['search']))
        keys=('unit','is_active') if self.action in {'history','activity'} else ('unit','is_active','department')
        for key in keys:
            if p.get(key):
                value=p[key]
                if key=='is_active':
                    if value not in ('true','false'):raise serializers.ValidationError({'is_active':'Use true or false.'})
                    value=value=='true'
                qs=qs.filter(**{key:value})
        if p.get('category'):
            category=p['category']
            if qs.model==Consumable:
                legacy='Office Maintenance' if category=='Office Maintenance Supplies' else category
                qs=qs.filter(Q(category=category)|Q(category='Office & Administration Consumables',subcategory=legacy))
            else:qs=qs.filter(category=category)
        if qs.model==Consumable and p.get('office')=='true':qs=qs.exclude(category__in=['','Factory & Production Consumables'])
        if p.get('low_stock')=='true':
            ids=[i.pk for i in qs if (b:=balance(i))['current_stock'] is not None and (b['current_stock']<=i.minimum_stock if qs.model==Consumable else b['current_stock']<i.minimum_stock)]
            qs=qs.filter(pk__in=ids)
        ordering=p.get('ordering','name')
        if ordering in ['stock','-stock']:
            rows=[(item,balance(item)['current_stock']) for item in qs]
            known=sorted([(item,stock) for item,stock in rows if stock is not None],key=lambda pair:(pair[1],pair[0].code),reverse=ordering=='-stock')
            unknown=[(item,stock) for item,stock in rows if stock is None]
            ids=[item.pk for item,_ in known+unknown]
            if not ids:return qs.order_by('id')
            return qs.order_by(Case(*[When(pk=pk,then=index) for index,pk in enumerate(ids)],output_field=IntegerField()))
        if ordering not in ['name','-name','code','-code']:raise serializers.ValidationError({'ordering':'Use name, code or stock ordering.'})
        return qs.order_by(ordering,'id')
    @action(detail=False,methods=['get'])
    def capabilities(self,request):
        model=self.queryset.model._meta.model_name
        can_post=self.movement_permission('add') and request.user.has_perm(f'inventory.view_{model}')
        return Response({'can_create':request.user.has_perm(f'inventory.add_{model}'),'can_edit':request.user.has_perm(f'inventory.change_{model}'),
                         'can_post':can_post,'can_open':can_post,'can_view_movements':self.movement_permission('view'),'catalogue_version':1,
                         'posting_notice':'' if can_post else 'Stock posting unavailable: item-view and movement-add permissions are required.'})
    def perform_create(self,serializer):
        serializer.instance=save_item(serializer,self.request.user)
    def perform_update(self,serializer):
        serializer.instance=save_item(serializer,self.request.user)
    @action(detail=True,methods=['get'])
    def stock(self,request,pk=None):
        return Response(self.get_serializer(self.get_object()).data['stock'])
    @action(detail=True,methods=['get','post'])
    def movements(self,request,pk=None):
        item=self.get_object()
        serializer=self.movement_serializer()
        if request.method=='GET':
            self.require_history()
            page=self.paginate_queryset(item.movements.select_related('item').all())
            return self.get_paginated_response(serializer(page,many=True).data)
        if not self.movement_permission('add'):raise PermissionDenied('Stock posting permission required.')
        row,created=post_movement(item.pk,request.data,request.user,self.queryset.model)
        return Response(serializer(row).data,status=201 if created else 200)
    @action(detail=False,methods=['get'])
    def history(self,request):
        self.require_history()
        rows=self.movement_model().objects.filter(item__in=self.get_queryset()).select_related('item')
        if request.query_params.get('department'):rows=rows.filter(department=request.query_params['department'])
        page=self.paginate_queryset(rows)
        return self.get_paginated_response(self.movement_serializer()(page,many=True).data)
    @action(detail=False,methods=['get'])
    def activity(self,request):
        self.require_history()
        from erp.models import EntryRevision
        module='consumables' if self.queryset.model==Consumable else 'spares'
        keys=[f'catalogue-{module}-{i.pk}' for i in self.get_queryset()]
        rows=EntryRevision.objects.filter(entry_id__in=keys).select_related('actor').order_by('-recorded_at','-id')
        if request.query_params.get('department'):rows=rows.filter(content__department=request.query_params['department'])
        page=self.paginate_queryset(rows)
        return self.get_paginated_response([{'id':r.pk,'version':r.version,'actor':r.actor.get_full_name() or r.actor.username,'actor_id':r.actor_id,'at':r.recorded_at.isoformat(),'content':r.content} for r in page])
    @action(detail=False,methods=['get'])
    def summary(self,request):
        from decimal import Decimal
        result={'total_items':0,'stock_value':Decimal('0'),'unknown_stock':0,'unvalued_items':0,'low_stock':0,'out_of_stock':0,'office_items':0,'factory_items':0}
        for item in self.get_queryset():
            current=balance(item)['current_stock'];result['total_items']+=1
            if current is None:result['unknown_stock']+=1
            if current is None or item.unit_cost is None:result['unvalued_items']+=1
            else:result['stock_value']+=current*item.unit_cost
            if current==0:result['out_of_stock']+=1
            if current is not None and current>0 and (current<=item.minimum_stock if isinstance(item,Consumable) else current<item.minimum_stock):result['low_stock']+=1
            if isinstance(item,Consumable):
                if item.category=='Factory & Production Consumables':result['factory_items']+=1
                elif item.category:result['office_items']+=1
        result['stock_value']=str(result['stock_value'])
        return Response(result)

class ConsumableViewSet(RegisterViewSet):
    queryset=Consumable.objects.all()
    serializer_class=ConsumableSerializer

class SpareViewSet(SpareOpeningViewMixin,RegisterViewSet):
    queryset=SpareItem.objects.all()
    serializer_class=SpareSerializer
