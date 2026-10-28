"""Isolated-test fixtures that initialize spares only through independent approval."""
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from .spare_openings import submit_opening, review_opening


def approved_opening(item,requester,quantity='10.000001',reference='Synthetic evidence'):
    proposal,_=submit_opening(item.pk,{'submission_id':str(uuid4()),'quantity':str(quantity),'unit':item.unit,'date':timezone.localdate().isoformat(),'reference':reference,'remarks':'Isolated opening fixture evidence'},requester)
    reviewer=get_user_model().objects.create_user(username=f'spare-reviewer-{uuid4().hex}')
    reviewer.user_permissions.set(Permission.objects.filter(codename__in=['view_spareitem','review_spareopening'],content_type__app_label='inventory'))
    result,_=review_opening(proposal.pk,{'command_id':str(uuid4()),'outcome':'APPROVED','expected_digest':proposal.payload_digest,'remarks':'Independently reviewed isolated fixture'},reviewer)
    return result.decision.movement,True
