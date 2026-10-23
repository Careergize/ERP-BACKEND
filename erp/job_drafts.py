"""Controlled preparation only; no approval, production or inventory authorization."""
from copy import deepcopy
from uuid import uuid4
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from .models import WorkflowOrder, WorkflowJob, WorkflowJobRevision, ContributorEvidence, CommandReceipt
from .order_control_foundation import capture_snapshot, verify_snapshot, require_version, require_order_revision
from .order_drafts import DraftAPI, DraftError, strict_keys
from .order_confirmation_evidence import inspect_confirmation_evidence
from .production_workflow import allocate


def access(actor, action=None):
    if (not actor.is_authenticated or not actor.is_active or
            not actor.has_perm('erp.view_workfloworder') or
            not actor.has_perm('erp.view_workflowjob') or
            action and not actor.has_perm('erp.' + action + '_workflowjob')):
        raise DraftError('PERMISSION_DENIED', 403)


def source(order, revision_id, version):
    return inspect_confirmation_evidence(order, revision_id, version)


def checked_job(job):
    row = job.control_revisions.filter(job_version=job.version).first()
    if row is None or job.source_confirmed_revision_id is None:
        raise DraftError('LEGACY_REVIEW_REQUIRED')
    require_order_revision(job.order_id, row.source_order_revision.order_id)
    if row.source_order_revision_id != job.source_confirmed_revision_id:
        raise DraftError('SOURCE_REVISION_CHANGED')
    verify_snapshot(row.content, row.content_hash)
    if capture_snapshot(job.data).digest != row.content_hash:
        raise DraftError('REVISION_CONFLICT')
    verify_snapshot(row.source_order_revision.content, row.source_order_revision.content_hash)
    bound_source = verify_snapshot(job.data.get('sourceOrderSnapshot'), job.data.get('sourceOrderContentHash'))
    if bound_source.digest != row.source_order_revision.content_hash:
        raise DraftError('SOURCE_REVISION_CHANGED')
    makers = set(row.contributors.values_list('actor_id', flat=True))
    source_makers = set(row.source_order_revision.contributors.values_list('actor_id', flat=True))
    if not source_makers or not (source_makers | {row.captured_by_id}) <= makers:
        raise DraftError('MAKER_EVIDENCE_REQUIRED')
    return row


def projection(job, actor):
    controlled = job.source_confirmed_revision_id is not None and job.control_revisions.exists()
    return {**deepcopy(job.data), 'workflowKey': job.pk, 'workflowVersion': job.version if controlled else None,
            'sourceConfirmedRevision': job.source_confirmed_revision_id,
            'reviewStatus': 'Draft' if controlled else 'LegacyUnknown', 'policyStatus': 'Blocked',
            'operationalBlockCodes': ['AUTHORITY_NOT_CONFIGURED', 'POLICY_NOT_APPROVED'],
            'capabilities': {'edit': False,
                             'approve': False, 'reject': False, 'issue': False}}


def validate_review_preparation(actor, job):
    """Evidence check only; deliberately cannot grant or record an approval."""
    access(actor)
    revision = checked_job(job)
    if revision.contributors.filter(actor=actor).exists():
        raise DraftError('SELF_APPROVAL_FORBIDDEN', 403)
    # D01/D02 authority publication and an independent decision service are absent.
    raise DraftError('AUTHORITY_NOT_CONFIGURED')


def validate_command(payload, pk):
    keys = {'commandId', 'expectedVersion', 'reason', 'evidenceIds', 'orderId',
            'expectedOrderVersion', 'sourceConfirmedRevision', 'corrections'}
    strict_keys(payload, keys)
    if set(payload) != keys:
        raise DraftError('VALIDATION_ERROR', 400)
    command_id = serializers.UUIDField().run_validation(payload['commandId'])
    if (type(payload['reason']) is not str or not payload['reason'].strip() or len(payload['reason']) > 2000
            or type(payload['evidenceIds']) is not list or payload['evidenceIds'] != []
            or type(payload['orderId']) is not int or not 1 <= payload['orderId'] <= 2**63 - 1):
        raise DraftError('VALIDATION_ERROR', 400)
    require_version(payload['expectedVersion'], 0 if pk is None else payload['expectedVersion'], creating=pk is None)
    return command_id


def command_identity(actor, payload, pk):
    action = 'job.draft.create' if pk is None else 'job.draft.edit'
    digest = capture_snapshot({'schema': 'job-draft-v1', 'actor': actor.pk,
                               'action': action, 'job': pk, 'payload': payload}).digest
    return action, digest


def replay_result(receipt, actor, action, digest, order, pk):
    if (receipt.actor_id != actor.pk or receipt.action != action or receipt.payload_digest != digest
            or receipt.completed_at is None or receipt.workflow_job_id is None
            or receipt.workflow_job.order_id != order.pk or pk and receipt.workflow_job_id != pk):
        raise DraftError('COMMAND_PAYLOAD_CONFLICT')
    return deepcopy(receipt.response_body), receipt.response_status, True


@transaction.atomic
def save_job(actor, payload, pk=None):
    """Operational entry: historical replay only until trusted C06 exists.

    Structural evidence never authorizes a new command. No configurable/test
    switch can enable creation, including for staff or superusers.
    """
    access(actor, 'add' if pk is None else 'change')
    command_id = validate_command(payload, pk)
    order = WorkflowOrder.objects.select_for_update().filter(pk=payload['orderId']).first()
    if order is None:
        raise DraftError('NOT_FOUND', 404)
    if pk and not WorkflowJob.objects.filter(pk=pk, order=order).exists():
        raise DraftError('NOT_FOUND', 404)
    receipt = CommandReceipt.objects.select_for_update().filter(pk=command_id).first()
    if receipt is not None:
        action, digest = command_identity(actor, payload, pk)
        return replay_result(receipt, actor, action, digest, order, pk)
    source(order, payload['sourceConfirmedRevision'], payload['expectedOrderVersion'])
    # No verifier for policy custody, adopted scope/authority, submitted/customer
    # evidence or suspension exists. Even perfect-looking persisted rows stop.
    raise DraftError('AUTHORITY_NOT_CONFIGURED')


@transaction.atomic
def _save_preparation(actor, payload, pk=None):
    """Private non-authorizing Draft persistence engine.

    New-write behavior is exercised directly by synthetic unit fixtures. The
    operational entry never calls this engine until trusted C06 is implemented.
    This engine revalidates persisted evidence; it accepts no trust/skip flag.
    """
    access(actor, 'add' if pk is None else 'change')
    command_id = validate_command(payload, pk)
    order = WorkflowOrder.objects.select_for_update().filter(pk=payload['orderId']).first()
    if order is None:
        raise DraftError('NOT_FOUND', 404)
    job = WorkflowJob.objects.select_for_update().filter(pk=pk).first() if pk else None
    if pk and (job is None or job.order_id != order.pk):
        raise DraftError('NOT_FOUND', 404)
    action, digest = command_identity(actor, payload, pk)
    receipt, created = CommandReceipt.objects.get_or_create(command_id=command_id, defaults={
        'actor': actor, 'action': action, 'schema_version': 1, 'payload_digest': digest})
    receipt = CommandReceipt.objects.select_for_update().get(pk=receipt.pk)
    if not created:
        return replay_result(receipt, actor, action, digest, order, pk)
    evidence = source(order, payload['sourceConfirmedRevision'], payload['expectedOrderVersion'])
    revision = order.confirmed_revision
    corrections = payload['corrections']
    if type(corrections) is not dict:
        raise DraftError('VALIDATION_ERROR', 400)
    previous = None
    if job is None:
        if corrections:
            raise DraftError('VALIDATION_ERROR', 400)
        if WorkflowJob.objects.filter(order=order).exists():
            raise DraftError('JOB_ALREADY_EXISTS')
        job = allocate(order, actor)  # Reuse existing shared parent, routing and recipe math.
        job.source_confirmed_revision = revision
        job.data['status'] = 'Draft'
        job.data['sourceOrderSnapshot'] = deepcopy(revision.content)
        job.data['sourceOrderContentHash'] = revision.content_hash
        job.data['sourceConfirmationDecision'] = evidence.decision_id
        job.data['sourceConfirmationCommand'] = evidence.command_id
        for child in job.data['manufacturingItems']:
            child['status'] = 'Draft'
    else:
        require_version(payload['expectedVersion'], job.version)
        previous = checked_job(job)
        if job.data.get('status') != 'Draft' or job.source_confirmed_revision_id != revision.pk:
            raise DraftError('REQUIREMENTS_LOCKED')
        if (job.data.get('sourceConfirmationDecision') != evidence.decision_id or
                job.data.get('sourceConfirmationCommand') != evidence.command_id):
            raise DraftError('CONFIRMATION_EVIDENCE_REQUIRED')
        if job.version >= 2**31 - 1:
            raise DraftError('VERSION_CONFLICT')
        children = {child['salesOrderItemId']: child for child in job.data['manufacturingItems']}
        for item_id, change in corrections.items():
            if item_id not in children:
                raise DraftError('VALIDATION_ERROR', 400)
            # Narrative corrections only. Source quantities, units, recipes and routes remain bound.
            strict_keys(change, {'specifications', 'productionRequirements'})
            for key, value in change.items():
                if type(value) is not str or len(value) > 2000:
                    raise DraftError('VALIDATION_ERROR', 400)
                children[item_id][key] = value
        job.version += 1
    job.data['workflowVersion'] = job.version
    snapshot = capture_snapshot(job.data)
    saved = WorkflowJobRevision.objects.create(job=job, source_order_revision=revision,
        job_version=job.version, content=snapshot.decode(), content_hash=snapshot.digest,
        captured_by=actor, reason=payload['reason'], provenance='Contemporaneous')
    contributors = set(revision.contributors.values_list('actor_id', flat=True)) | {actor.pk}
    if previous:
        contributors |= set(previous.contributors.values_list('actor_id', flat=True))
    for contributor in sorted(contributors):
        ContributorEvidence.objects.create(event_id=uuid4(), job_revision=saved, actor_id=contributor,
            action='Draft saved' if contributor == actor.pk else 'Inherited contributor')
    if pk:
        changed = WorkflowJob.objects.filter(pk=pk, version=payload['expectedVersion']).update(
            data=job.data, version=job.version, updated_at=timezone.now())
        if changed != 1:
            raise DraftError('VERSION_CONFLICT')
    else:
        job.save(update_fields=['data', 'source_confirmed_revision'])
    result = projection(job, actor)
    receipt.workflow_job = job
    receipt.response_status = 201 if pk is None else 200
    receipt.response_body = result
    receipt.completed_at = timezone.now()
    receipt.save()
    return result, receipt.response_status, False


class JobDraftAPI(DraftAPI):
    def get(self, request):
        access(request.user)
        return Response({'capabilities': {'create': False},
                         'operationalBlockCodes': ['AUTHORITY_NOT_CONFIGURED']})

    def post(self, request, pk=None):
        try:
            body, status, replay = save_job(request.user, request.data, pk)
        except IntegrityError:
            raise DraftError('COMMAND_PAYLOAD_CONFLICT')
        return Response(body, status=status, headers={'Idempotency-Replayed': str(replay).lower()})


class JobDraftDetail(JobDraftAPI):
    def get(self, request, pk):
        access(request.user)
        job = WorkflowJob.objects.filter(pk=pk).first()
        if job is None:
            raise DraftError('NOT_FOUND', 404)
        if job.source_confirmed_revision_id is not None:
            checked_job(job)
        return Response(projection(job, request.user))


class JobDraftHistory(JobDraftAPI):
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        access(request.user)
        job = WorkflowJob.objects.filter(pk=pk).first()
        if job is None:
            raise DraftError('NOT_FOUND', 404)
        results = []
        for row in job.control_revisions.select_related('source_order_revision').order_by('job_version'):
            require_order_revision(job.order_id, row.source_order_revision.order_id)
            verify_snapshot(row.content, row.content_hash)
            results.append({'id': row.pk, 'version': row.job_version, 'content': row.content,
                'contentHash': row.content_hash, 'sourceConfirmedRevision': row.source_order_revision_id,
                'reason': row.reason, 'capturedAt': row.captured_at.isoformat(),
                'contributors': list(row.contributors.values_list('actor_id', flat=True))})
        return Response({'jobId': pk, 'revisions': results})
