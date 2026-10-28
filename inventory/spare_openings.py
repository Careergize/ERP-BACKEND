"""Spares-only independent opening approval. No roles or grants are inferred."""
from dataclasses import dataclass
from collections.abc import Mapping
from uuid import uuid5, NAMESPACE_URL
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, APIException
from .catalog_models import SpareItem
from .catalogue_service import serialized, audit, digest, post_movement, DEPARTMENTS
from .spare_opening_models import SpareOpeningRequest, SpareOpeningDecision


class OpeningConflict(APIException):
    status_code = 409
    default_detail = 'This opening request was already decided or the request ID was reused with different details.'


def explicit_opening_permission(user, verb):
    """Use existing grant sources, but never infer opening authority from superuser/role labels."""
    if not user.is_authenticated or not user.is_active:
        return False
    codename = f'{verb}_spareopening'
    condition = {'content_type__app_label':'inventory', 'content_type__model':'spareopeningrequest', 'codename':codename}
    if user.user_permissions.filter(**condition).exists():
        return True
    if user.groups.filter(permissions__content_type__app_label='inventory', permissions__content_type__model='spareopeningrequest', permissions__codename=codename).exists():
        return True
    role = user.assigned_role if user.assigned_role_id else None
    return bool(role and role.is_active and role.permissions.filter(**condition).exists())


def can_request(user):
    return user.has_perm('inventory.view_spareitem') and explicit_opening_permission(user,'request')


def can_review(user):
    return user.has_perm('inventory.view_spareitem') and explicit_opening_permission(user,'review')


def can_read_all(user):
    return user.has_perm('inventory.view_spareitem') and (explicit_opening_permission(user,'view') or can_review(user))


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data,Mapping):return super().to_internal_value(data)
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key:'This field cannot be supplied.' for key in sorted(unknown)})
        return super().to_internal_value(data)


class OpeningInput(StrictInput):
    submission_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=20,decimal_places=6,min_value=0)
    unit = serializers.CharField(max_length=30)
    date = serializers.DateField()
    department = serializers.ChoiceField(choices=['']+DEPARTMENTS,default='')
    reference = serializers.CharField(max_length=250,allow_blank=True,default='')
    remarks = serializers.CharField(max_length=5000,allow_blank=False)


class ReviewInput(StrictInput):
    command_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(choices=['APPROVED','REJECTED'])
    expected_digest = serializers.RegexField(regex=r'^[0-9a-f]{64}$')
    remarks = serializers.CharField(max_length=5000,allow_blank=False)


@serialized
def submit_opening(item_id, data, actor):
    if not can_request(actor):
        raise PermissionDenied('An explicitly assigned spare-opening request permission is required.')
    s=OpeningInput(data=data);s.is_valid(raise_exception=True);v=s.validated_data
    original=SpareOpeningRequest.objects.filter(submission_id=v['submission_id']).first()
    payload_digest=digest({'item':str(item_id),**v})
    if original:
        if original.requested_by_id!=actor.pk or str(original.item_id)!=str(item_id) or original.payload_digest!=payload_digest:
            raise OpeningConflict()
        return original,False
    item=SpareItem.objects.get(pk=item_id)
    if not item.is_active or item.unit!=v['unit']:
        raise serializers.ValidationError('Use an active spare and its exact unit.')
    if v['date']>timezone.localdate():
        raise serializers.ValidationError({'date':'Future opening dates are not allowed.'})
    if item.movements.exists():
        raise serializers.ValidationError('Opening is already initialized or historical movements exist; an opening cannot be overwritten.')
    if item.opening_requests.filter(status='PENDING').exists():
        raise OpeningConflict('A pending opening request already exists for this spare.')
    proposal=SpareOpeningRequest.objects.create(item=item,requested_by=actor,payload_digest=payload_digest,**v)
    audit(item,actor,'Spare Opening Requested',None,{'request_id':str(proposal.pk),'status':'PENDING','quantity':str(proposal.quantity),'unit':proposal.unit,'date':proposal.date.isoformat(),'snapshot_hash':proposal.payload_digest},str(proposal.pk),proposal.department,proposal.remarks)
    return proposal,True


_AUTHORITY_SEAL=object()
@dataclass(frozen=True)
class _OpeningAuthority:
    request_id: object
    reviewer_id: int
    seal: object


def movement_request_id(proposal):
    return uuid5(NAMESPACE_URL,f'navapack-spare-opening:{proposal.pk}')


def validate_opening_authority(item,values,actor,authority):
    if not isinstance(authority,_OpeningAuthority) or authority.seal is not _AUTHORITY_SEAL or authority.reviewer_id!=actor.pk:
        raise PermissionDenied({'code':'spare_opening_approval_required','detail':'Direct spare openings are prohibited. Submit a request for independent approval.'})
    proposal=SpareOpeningRequest.objects.get(pk=authority.request_id)
    if not can_review(actor) or proposal.requested_by_id==actor.pk:
        raise PermissionDenied('The opening requester cannot approve their own request, including superusers.')
    expected={'request_id':movement_request_id(proposal),'movement_type':'IN','is_opening':True,'quantity':proposal.quantity,'unit':proposal.unit,'date':proposal.date,'department':proposal.department,'reference':proposal.reference or f'Opening request {proposal.pk}','remarks':proposal.remarks}
    if proposal.status!='PENDING' or proposal.item_id!=item.pk or any(values.get(k)!=v for k,v in expected.items()):
        raise PermissionDenied('The opening does not match the independently reviewed proposal.')


@serialized
def review_opening(request_id,data,actor):
    if not can_review(actor):
        raise PermissionDenied('An explicitly assigned spare-opening review permission is required.')
    s=ReviewInput(data=data);s.is_valid(raise_exception=True);v=s.validated_data
    proposal=SpareOpeningRequest.objects.select_related('item').get(pk=request_id)
    if proposal.requested_by_id==actor.pk:
        raise PermissionDenied('The requester cannot review their own opening, including superusers.')
    fingerprint=digest({'request':str(proposal.pk),**v})
    replay=SpareOpeningDecision.objects.filter(command_id=v['command_id']).first()
    if replay:
        if replay.reviewer_id!=actor.pk or replay.request_id!=proposal.pk or replay.payload_digest!=fingerprint:
            raise OpeningConflict()
        return proposal,False
    if proposal.payload_digest!=v['expected_digest']:
        raise OpeningConflict('Opening content changed. Reload and review the current immutable snapshot.')
    if proposal.status!='PENDING' or SpareOpeningDecision.objects.filter(request=proposal).exists():
        raise OpeningConflict()
    movement=None
    if v['outcome']=='APPROVED':
        movement,_=post_movement(proposal.item_id,{'request_id':str(movement_request_id(proposal)),'movement_type':'IN','is_opening':True,'quantity':str(proposal.quantity),'unit':proposal.unit,'date':proposal.date.isoformat(),'department':proposal.department,'reference':proposal.reference or f'Opening request {proposal.pk}','remarks':proposal.remarks},actor,_opening_authority=_OpeningAuthority(proposal.pk,actor.pk,_AUTHORITY_SEAL))
    decision=SpareOpeningDecision.objects.create(command_id=v['command_id'],request=proposal,requester_id=proposal.requested_by_id,reviewer=actor,outcome=v['outcome'],movement=movement,remarks=v['remarks'],payload_digest=fingerprint)
    proposal.status=decision.outcome
    proposal.save(update_fields=['status'])
    audit(proposal.item,actor,'Spare Opening Approved' if movement else 'Spare Opening Rejected',{'request_id':str(proposal.pk),'status':'PENDING'}, {'request_id':str(proposal.pk),'status':proposal.status,'requester_id':proposal.requested_by_id,'reviewer_id':actor.pk,'decision_id':str(decision.pk),'movement_id':str(movement.pk) if movement else None,'snapshot_hash':proposal.payload_digest},movement.transaction_reference if movement else str(proposal.pk),proposal.department,decision.remarks)
    return proposal,True
