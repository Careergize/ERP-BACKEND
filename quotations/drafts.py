from django.db import transaction
from rest_framework import permissions, serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from .models import QuotationDraft, Quotation
from .serializers import DocumentSerializer, CustomerSerializer, LineSerializer, CompanySerializer
from .views import normalized_document, quote_data


class DraftCustomerSerializer(CustomerSerializer):
    name = serializers.CharField(max_length=200, allow_blank=True)
    customerId = serializers.CharField(max_length=100, required=False)
    prospectId = serializers.CharField(max_length=100, required=False)
    customerType = serializers.ChoiceField(choices=['B2B', 'B2C'], default='B2B')


class DraftCompanySerializer(CompanySerializer):
    logo = serializers.CharField(max_length=500, required=False, default='/assets/Nava-logo.png')


class DraftLineSerializer(LineSerializer):
    led = serializers.JSONField(required=False)
    ledSnapshot = serializers.JSONField(required=False)
    id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=['stock', 'custom'])
    productId = serializers.CharField(max_length=100, required=False)
    modelId = serializers.CharField(max_length=100, required=False)
    modelRevision = serializers.IntegerField(min_value=1, required=False)
    estimateId = serializers.CharField(max_length=100, required=False)
    estimateRevision = serializers.IntegerField(min_value=1, required=False)
    manualPriceOverride = serializers.BooleanField(required=False)
    unitId = serializers.IntegerField(min_value=1, required=False)

    def validate(self, data):
        if 'unitId' in data:
            data['unit_id'] = data['unitId']
        data = super().validate(data)
        data['unitId'] = data.pop('unit_id')
        return data


class DraftContentSerializer(DocumentSerializer):
    salesOrderId = serializers.CharField(max_length=100, required=False)
    source_id = None
    customer_id = None
    id = serializers.UUIDField()
    rootId = serializers.UUIDField()
    previousId = serializers.UUIDField(required=False)
    salesRepresentative = serializers.CharField(max_length=200, allow_blank=True, default='')
    customer = DraftCustomerSerializer()
    company = DraftCompanySerializer()
    lines = DraftLineSerializer(many=True, allow_empty=True, max_length=100)


def draft_data(draft):
    review = Quotation.objects.filter(created_by=draft.created_by, source_id=str(draft.id)).order_by('-created_at').first()
    return {'id': str(draft.id), 'editor_data': draft.editor_data, 'version': draft.version,
        'created_by': str(draft.created_by_id), 'created_by_name': draft.created_by.get_full_name() or draft.created_by.username,
        'created_at': draft.created_at, 'updated_at': draft.updated_at, 'review': quote_data(review) if review else None}


class QuotationDraftViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = QuotationDraft.objects.select_related('created_by').all()

    def get_queryset(self):
        return super().get_queryset().filter(created_by=self.request.user)

    def require(self, action):
        if not self.request.user.has_perm(f'quotations.{action}_quotation'):
            raise PermissionDenied('Your account needs quotation permissions to save or load drafts.')

    def list(self, request):
        self.require('view')
        return self.get_paginated_response([draft_data(q) for q in self.paginate_queryset(self.get_queryset())])

    def retrieve(self, request, pk=None):
        self.require('view')
        return Response(draft_data(self.get_object()))

    def create(self, request):
        self.require('add')
        class SaveSerializer(serializers.Serializer):
            editor_data = DraftContentSerializer()
            expected_version = serializers.IntegerField(min_value=0, default=0)

        serializer = SaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = normalized_document(serializer.validated_data['editor_data'])
        with transaction.atomic():
            draft, created = QuotationDraft.objects.get_or_create(pk=content['id'], defaults={'editor_data': content, 'created_by': request.user})
            draft = QuotationDraft.objects.select_for_update().get(pk=draft.pk)
            if draft.created_by_id != request.user.pk:
                raise PermissionDenied('This draft belongs to another user.')
            if not created and draft.editor_data != content:
                if Quotation.objects.filter(created_by=request.user, source_id=str(draft.id), submitted_at__isnull=False).exists():
                    return Response({'detail': 'Submitted quotations are frozen. Create a new revision before editing.'}, status=409)
                if serializer.validated_data['expected_version'] != draft.version:
                    return Response({'detail': 'Another session changed this draft. Refresh before editing again.'}, status=409)
                draft.editor_data = content
                draft.version += 1
                draft.save(update_fields=['editor_data', 'version', 'updated_at'])
        return Response(draft_data(draft), status=201 if created else 200)
