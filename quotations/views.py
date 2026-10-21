import hashlib
import json
import re
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from .delivery import configuration_error, send_email, send_whatsapp, DeliveryError, DeliveryUnknown
from .models import Quotation, Delivery
from .pdf import build_pdf
from .serializers import DocumentSerializer, SendSerializer


def normalized_document(data):
    # Keep decimals as strings; a JSON float would lose entered price precision.
    return json.loads(json.dumps(data, default=lambda value: value.isoformat() if hasattr(value, 'isoformat') else str(value)))


def delivery_data(delivery):
    return {key: str(getattr(delivery, key)) for key in ['id', 'channel', 'recipient', 'status', 'detail', 'provider_id', 'created_at']}


def manager_approved(quote):
    return bool(not quote.rejected_at and quote.submitted_at and quote.approved_at and quote.approved_by_id and quote.approved_by.effective_role in ('project_manager', 'production_manager') and quote.approved_at >= quote.submitted_at)


def quote_data(quote):
    return {'id': str(quote.pk), 'document': quote.document, 'approved': manager_approved(quote), 'submitted_at': quote.submitted_at,
        'approved_at': quote.approved_at, 'created_at': quote.created_at,
        'rejected_at': quote.rejected_at, 'rejection_reason': quote.rejection_reason,
        'reviewed_by': (quote.rejected_by or quote.approved_by).get_username() if (quote.rejected_by_id or quote.approved_by_id) else None,
        'deliveries': [delivery_data(d) for d in quote.deliveries.order_by('id')]}


class QuotationViewSet(viewsets.GenericViewSet):
    queryset = Quotation.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def require(self, action_name):
        if not self.request.user.has_perm(f'quotations.{action_name}_quotation'):
            raise PermissionDenied('Your account needs the corresponding quotation permission.')

    def get_queryset(self):
        queryset = super().get_queryset()
        # Approvers can review all snapshots; ordinary staff see their own only.
        if not (self.request.user.effective_role in ('project_manager', 'production_manager') and self.request.user.has_perm('quotations.approve_quotation')):
            queryset = queryset.filter(created_by=self.request.user)
        if self.request.query_params.get('pending') == 'true':
            queryset = queryset.filter(submitted_at__isnull=False, approved_at__isnull=True, rejected_at__isnull=True)
        elif self.request.query_params.get('submitted') == 'true':
            queryset = queryset.filter(submitted_at__isnull=False)
        return queryset

    def list(self, request):
        self.require('view')
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response([quote_data(q) for q in page])

    def retrieve(self, request, pk=None):
        self.require('view')
        return Response(quote_data(self.get_object()))

    @action(detail=False, methods=['post'])
    def extract(self, request):
        """Extract persisted sales-order/customer/line details into template fields."""
        self.require('add')
        if not request.user.has_perm('erp.view_salesorder'):
            raise PermissionDenied('Sales order view permission is required for extraction.')
        from django.shortcuts import get_object_or_404
        from rest_framework import serializers
        from erp.models import SalesOrder
        from .serializers import CompanySerializer, TermsSerializer

        class SourceSerializer(serializers.Serializer):
            sales_order_id = serializers.IntegerField(min_value=1)
            company = CompanySerializer()
            terms = TermsSerializer()
            currency = serializers.RegexField(r'^[A-Z]{3}$')
            taxRate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
            taxMode = serializers.ChoiceField(choices=['inclusive', 'exclusive'])
            validityDays = serializers.IntegerField(default=14, min_value=0, max_value=3650)

        source = SourceSerializer(data=request.data)
        source.is_valid(raise_exception=True)
        values = source.validated_data
        order = get_object_or_404(SalesOrder.objects.select_related('customer').prefetch_related('lines__product'), pk=values['sales_order_id'])
        customer = order.customer
        payload = {
            'source_id': f'sales-order-{order.pk}', 'number': f'QT-{order.number}', 'revision': 1,
            'documentType': 'Sales Quotation', 'date': timezone.localdate(), 'validityDays': values['validityDays'],
            'currency': values['currency'], 'taxRate': values['taxRate'], 'taxMode': values['taxMode'], 'discount': '0',
            'company': values['company'], 'terms': values['terms'],
            'customer_id': customer.pk,
            'customer': {'name': customer.name, 'billingAddress': customer.address, 'deliveryAddress': customer.delivery_address or customer.address,
                'contactPerson': customer.contact_person, 'telephone': customer.phone, 'email': customer.email, 'tin': customer.tin},
            'lines': [{'description': line.product.name, 'group': '', 'specifications': '', 'quantity': line.quantity,
                'unit': line.product.unit, 'unitPrice': line.unit_price} for line in order.lines.all()],
        }
        serializer = DocumentSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(normalized_document(serializer.validated_data))

    def create(self, request):
        self.require('add')
        serializer = DocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = normalized_document(serializer.validated_data)
        fingerprint = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        quote, created = Quotation.objects.get_or_create(created_by=request.user, source_id=document['source_id'],
            fingerprint=fingerprint, defaults={'document': document, 'customer_master_id': document.get('customer_id')})
        return Response(quote_data(quote), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='pdf')
    def pdf(self, request, pk=None):
        self.require('view')
        quote = self.get_object()
        response = HttpResponse(build_pdf(quote.document, approved=manager_approved(quote)), content_type='application/pdf')
        filename = re.sub(r'[^A-Za-z0-9_.-]', '-', quote.document['number']) + '.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'private, no-store'
        return response

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        self.require('add')
        with transaction.atomic():
            quote = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if quote.rejected_at:
                return Response({'detail': 'This snapshot was rejected. Revise the quotation and submit a new snapshot.'}, status=409)
            if quote.document['validUntil'] < timezone.localdate().isoformat():
                return Response({'detail': 'Quotation has expired. Create a current revision.'}, status=400)
            if quote.submitted_at is None:
                quote.submitted_at = timezone.now()
                # Older generic PDF approvals do not satisfy manager approval.
                quote.approved_at, quote.approved_by = None, None
                quote.save(update_fields=['submitted_at', 'approved_at', 'approved_by'])
        return Response(quote_data(quote))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        self.require('approve')
        if request.user.effective_role not in ('project_manager', 'production_manager'):
            raise PermissionDenied('Only a Project Manager or Production Manager can review quotations.')
        with transaction.atomic():
            quote = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if quote.rejected_at:
                return Response({'detail': 'Rejected snapshots cannot be approved. Submit a revised quotation.'}, status=409)
            if quote.submitted_at is None:
                return Response({'detail': 'Submit this quotation for manager approval first.'}, status=400)
            if quote.document['validUntil'] < timezone.localdate().isoformat():
                return Response({'detail': 'Quotation has expired. Create a current revision.'}, status=400)
            if quote.approved_at is None:
                quote.approved_at, quote.approved_by = timezone.now(), request.user
                quote.save(update_fields=['approved_at', 'approved_by'])
        return Response(quote_data(quote))

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        self.require('approve')
        if request.user.effective_role not in ('project_manager', 'production_manager'):
            raise PermissionDenied('Only a Project Manager or Production Manager can review quotations.')
        from rest_framework import serializers

        class RejectionSerializer(serializers.Serializer):
            reason = serializers.CharField(max_length=2000, allow_blank=False)

        serializer = RejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            quote = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if quote.submitted_at is None or quote.approved_at:
                return Response({'detail': 'Only a submitted, unapproved quotation may be rejected.'}, status=409)
            if quote.rejected_at:
                if quote.rejection_reason == serializer.validated_data['reason']:
                    return Response(quote_data(quote))
                return Response({'detail': 'Rejection is already recorded and cannot be overwritten.'}, status=409)
            quote.rejected_at, quote.rejected_by = timezone.now(), request.user
            quote.rejection_reason = serializer.validated_data['reason']
            quote.save(update_fields=['rejected_at', 'rejected_by', 'rejection_reason'])
        return Response(quote_data(quote))

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        self.require('send')
        quote = self.get_object()
        if not manager_approved(quote) or quote.document['validUntil'] < timezone.localdate().isoformat():
            return Response({'detail': 'Manager approval of this exact current quotation is required before email or WhatsApp sending. Rejected quotations cannot be sent.'}, status=400)
        serializer = SendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Validate every chosen channel before creating attempts or contacting providers.
        issues = {ch: configuration_error(ch) for ch in data['channels'] if configuration_error(ch)}
        if issues:
            return Response({'detail': 'Delivery is not configured.', 'channels': issues}, status=503)
        with transaction.atomic():
            old = list(Delivery.objects.filter(quotation=quote, request_id=data['request_id']))
            if old:
                expected = {(ch, data['email' if ch == 'email' else 'phone']) for ch in data['channels']}
                if {(d.channel, d.recipient) for d in old} != expected:
                    return Response({'detail': 'Request ID already used with different channels or recipients.'}, status=409)
                return Response({'results': [delivery_data(d) for d in old], 'replayed': True})
            # Unique constraint prevents a simultaneous duplicate from posting twice.
            from django.db import IntegrityError
            try:
                with transaction.atomic():
                    deliveries = [Delivery.objects.create(quotation=quote, request_id=data['request_id'], channel=ch,
                        recipient=data['email' if ch == 'email' else 'phone'], created_by=request.user) for ch in data['channels']]
            except IntegrityError:
                return Response({'detail': 'This send request is already being processed. Reload delivery history.'}, status=409)
        try:
            pdf = build_pdf(quote.document, approved=True)
        except Exception:
            for d in deliveries:
                d.status, d.detail = 'failed', 'PDF generation failed; no message was sent.'
                d.save(update_fields=['status', 'detail'])
            return Response({'results': [delivery_data(d) for d in deliveries]}, status=500)
        filename = re.sub(r'[^A-Za-z0-9_.-]', '-', quote.document['number']) + '.pdf'
        for d in deliveries:
            try:
                sender = send_email if d.channel == 'email' else send_whatsapp
                d.provider_id = sender(d.recipient, quote.document, pdf, filename)
                d.status, d.detail = 'accepted', 'Provider accepted the PDF. Recipient delivery is not confirmed.'
            except DeliveryError as exc:
                d.status, d.detail = 'failed', str(exc)
            except DeliveryUnknown as exc:
                d.status, d.detail = 'unknown', str(exc)
            except Exception:
                d.status, d.detail = 'unknown', 'Submission outcome is unknown. Check provider logs before retrying.'
            d.save(update_fields=['status', 'detail', 'provider_id'])
        return Response({'results': [delivery_data(d) for d in deliveries], 'replayed': False})
