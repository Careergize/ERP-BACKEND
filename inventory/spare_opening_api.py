"""Spares-only capability diagnostics and opening request/review actions."""
from django.db import connection, DatabaseError
from django.db.models import Q
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, NotFound
from rest_framework.response import Response
from .catalog_models import SpareItem, SpareStockMovement, CatalogueSequence
from .spare_opening_models import SpareOpeningRequest, SpareOpeningDecision
from .spare_openings import can_request, can_review, can_read_all, submit_opening, review_opening


class SparesStorageUnavailable(APIException):
    status_code=503
    def __init__(self,code,detail):
        super().__init__({'code':code,'detail':detail})


def storage_status(include_openings=True):
    """Read-only readiness check; never migrates, creates tables or fabricates balances."""
    from erp.models import SharedEntry, EntryRevision
    try:
        with connection.cursor() as cursor:
            names=set(connection.introspection.table_names(cursor))
            models=(SpareItem,SpareStockMovement,CatalogueSequence,SharedEntry,EntryRevision)
            if include_openings:models+=(SpareOpeningRequest,SpareOpeningDecision)
            for model in models:
                table=model._meta.db_table
                if table not in names:
                    return False,'spares_migrations_required'
                columns={c.name for c in connection.introspection.get_table_description(cursor,table)}
                if not {f.column for f in model._meta.concrete_fields}.issubset(columns):
                    return False,'spares_migrations_required'
        if not CatalogueSequence.objects.filter(pk='catalogue-lock').exists():
            return False,'spares_migrations_required'
        return True,'ready'
    except DatabaseError:
        return False,'spares_api_unavailable'


class OpeningRequestSerializer(serializers.ModelSerializer):
    item_code=serializers.CharField(source='item.code',read_only=True)
    item_name=serializers.CharField(source='item.name',read_only=True)
    category=serializers.CharField(source='item.category',read_only=True)
    requested_by_name=serializers.SerializerMethodField()
    decision=serializers.SerializerMethodField()
    can_review=serializers.SerializerMethodField()
    class Meta:
        model=SpareOpeningRequest
        fields=['id','submission_id','item','item_code','item_name','category','quantity','unit','date','department','reference','remarks','requested_by','requested_by_name','requested_at','payload_digest','status','decision','can_review']
        read_only_fields=fields
    def get_requested_by_name(self,obj):
        return obj.requested_by.get_full_name() or obj.requested_by.username
    def get_decision(self,obj):
        decision=getattr(obj,'decision',None)
        if not decision:return None
        return {'id':str(decision.pk),'command_id':str(decision.command_id),'reviewer':decision.reviewer_id,'outcome':decision.outcome,'remarks':decision.remarks,'decided_at':decision.decided_at.isoformat(),'movement':str(decision.movement_id) if decision.movement_id else None,'transaction_reference':decision.movement.transaction_reference if decision.movement_id else None}
    def get_can_review(self,obj):
        user=self.context['request'].user
        return obj.status=='PENDING' and obj.requested_by_id!=user.pk and can_review(user)


class SpareOpeningViewMixin:
    def initial(self,request,*args,**kwargs):
        super().initial(request,*args,**kwargs)
        self._spares_storage=storage_status(include_openings=False)
        self._spares_opening_storage=storage_status()
        needed=self._spares_opening_storage if self.action in {'opening_requests','item_openings','review_opening'} else self._spares_storage
        if self.action!='capabilities' and not needed[0]:
            code=needed[1]
            raise SparesStorageUnavailable(code,'Required Spares catalogue/approval migrations are not installed on this API.' if code=='spares_migrations_required' else 'The Spares API cannot verify storage availability. Retry later.')

    @action(detail=False,methods=['get'])
    def capabilities(self,request):
        response=super().capabilities(request)
        ready,code=getattr(self,'_spares_storage',None) or storage_status()
        opening_ready,opening_code=getattr(self,'_spares_opening_storage',None) or storage_status()
        response.data.update({'spares_opening_version':1,'schema_ready':ready,'opening_schema_ready':opening_ready,'diagnostic_code':code if not ready else opening_code,'can_open':False,
                              'can_request_opening':opening_ready and can_request(request.user),'can_review_opening':opening_ready and can_review(request.user),
                              'can_view_openings':opening_ready and (can_request(request.user) or can_read_all(request.user)),'opening_policy':'independent_review_no_self_approval'})
        if not ready:
            response.data.update({'can_post':False,'can_create':False,'can_edit':False,'posting_notice':'Spares posting unavailable: required catalogue/approval migrations or storage are unavailable.'})
        return response

    def _opening_rows(self):
        user=self.request.user
        if not can_read_all(user) and not can_request(user):
            raise PermissionDenied('An explicitly assigned opening request, review or read permission is required.')
        rows=SpareOpeningRequest.objects.select_related('item','requested_by','decision','decision__movement')
        if not can_read_all(user):rows=rows.filter(requested_by=user)
        status=self.request.query_params.get('status')
        if status:
            if status not in {'PENDING','APPROVED','REJECTED'}:raise serializers.ValidationError({'status':'Use PENDING, APPROVED or REJECTED.'})
            rows=rows.filter(status=status)
        search=self.request.query_params.get('search')
        if search:rows=rows.filter(Q(item__code__icontains=search)|Q(item__name__icontains=search))
        return rows

    @action(detail=False,methods=['get'],url_path='opening-requests')
    def opening_requests(self,request):
        page=self.paginate_queryset(self._opening_rows())
        return self.get_paginated_response(OpeningRequestSerializer(page,many=True,context={'request':request}).data)

    @action(detail=True,methods=['get','post'],url_path='opening-requests')
    def item_openings(self,request,pk=None):
        item=self.get_object()
        if request.method=='GET':
            page=self.paginate_queryset(self._opening_rows().filter(item=item))
            return self.get_paginated_response(OpeningRequestSerializer(page,many=True,context={'request':request}).data)
        row,created=submit_opening(item.pk,request.data,request.user)
        return Response(OpeningRequestSerializer(row,context={'request':request}).data,status=201 if created else 200)

    @action(detail=False,methods=['post'],url_path=r'opening-requests/(?P<request_id>[^/.]+)/review')
    def review_opening(self,request,request_id=None):
        key=serializers.UUIDField().run_validation(request_id)
        if not SpareOpeningRequest.objects.filter(pk=key).exists():raise NotFound('Opening request not found.')
        row,_=review_opening(key,request.data,request.user)
        return Response(OpeningRequestSerializer(row,context={'request':request}).data)
