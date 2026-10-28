from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from inventory.catalog_models import SpareItem, SpareStockMovement, ConsumableStockMovement
from inventory.catalogue_service import balance, post_movement
from inventory.spare_opening_models import SpareOpeningRequest, SpareOpeningDecision
from inventory.spare_openings import can_request, can_review
from erp.models import EntryRevision


class SpareOpeningTests(TestCase):
    def grant(self,user,*codenames):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='inventory',codename__in=codenames))
    def auth_client(self,user):
        client=APIClient();client.force_authenticate(user);return client
    def setUp(self):
        self.requester=get_user_model().objects.create_user(username='opening-requester',role='store_keeper')
        self.reviewer=get_user_model().objects.create_user(username='opening-reviewer',role='production_manager')
        self.other=get_user_model().objects.create_user(username='opening-other-reviewer')
        self.grant(self.requester,'view_spareitem','view_sparestockmovement','add_sparestockmovement','add_spareitem','change_spareitem','request_spareopening')
        self.grant(self.reviewer,'view_spareitem','review_spareopening')
        self.grant(self.other,'view_spareitem','review_spareopening')
        self.item=SpareItem.objects.create(code='KEEP-SP-001',name='Imported bearing',category='IMPORTED',unit='Each',created_by=self.requester)
    def propose(self,actor=None,item=None,**changes):
        body={'submission_id':str(uuid4()),'quantity':'10','unit':(item or self.item).unit,'date':timezone.localdate().isoformat(),'department':'Stores','remarks':'Physical count evidence reviewed separately',**changes}
        result=self.auth_client(actor or self.requester).post(f'/api/spares/{(item or self.item).pk}/opening-requests/',body,format='json')
        return result,body
    def decide(self,proposal,actor=None,**changes):
        body={'command_id':str(uuid4()),'expected_digest':proposal['payload_digest'],'outcome':'APPROVED','remarks':'Independent count verification',**changes}
        result=self.auth_client(actor or self.reviewer).post(f"/api/spares/opening-requests/{proposal['id']}/review/",body,format='json')
        return result,body
    def ordinary(self,actor=None,**changes):
        body={'request_id':str(uuid4()),'movement_type':'IN','quantity':'2','unit':'Each','department':'Stores',**changes}
        return self.auth_client(actor or self.requester).post(f'/api/spares/{self.item.pk}/movements/',body,format='json'),body
    def test_pending_request_leaves_null_balance_and_immutable_snapshot(self):
        response,_=self.propose();self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['status'],'PENDING');self.assertFalse(response.data['can_review'])
        self.assertIsNone(balance(self.item)['opening_stock']);self.assertIsNone(balance(self.item)['current_stock']);self.assertEqual(SpareStockMovement.objects.count(),0)
        item=self.auth_client(self.requester).get(f'/api/spares/{self.item.pk}/').data
        self.assertEqual(item['opening_state']['status'],'PENDING');self.assertIsNone(item['stock']['current_stock'])
        pending=SpareOpeningRequest.objects.get(pk=response.data['id']);pending.quantity=99
        with self.assertRaises(ModelValidationError):pending.save()
        with self.assertRaises(ModelValidationError):SpareOpeningRequest.objects.filter(pk=pending.pk).update(status='APPROVED')
    def test_independent_approval_posts_once_with_both_actor_evidence(self):
        response,_=self.propose();approved,_=self.decide(response.data);self.assertEqual(approved.status_code,200,approved.data)
        self.assertEqual(approved.data['status'],'APPROVED');self.assertEqual(approved.data['decision']['reviewer'],self.reviewer.pk)
        move=SpareStockMovement.objects.get();self.assertEqual(move.recorded_by_id,self.reviewer.pk);self.assertEqual(move.quantity,10)
        self.assertEqual(balance(self.item)['current_stock'],Decimal('10'));self.assertEqual(self.item.code,'KEEP-SP-001')
        actions=list(EntryRevision.objects.filter(entry__key__startswith='catalogue-spares-').values_list('content',flat=True))
        self.assertEqual({a['action'] for a in actions},{'Spare Opening Requested','Stock IN','Spare Opening Approved'})
        evidence=next(a for a in actions if a['action']=='Spare Opening Approved')['next']
        self.assertEqual(evidence['requester_id'],self.requester.pk);self.assertEqual(evidence['reviewer_id'],self.reviewer.pk)
        decision=SpareOpeningDecision.objects.get()
        with self.assertRaises(ModelValidationError):decision.delete()
        with self.assertRaises(ModelValidationError):SpareOpeningDecision.objects.filter(pk=decision.pk).update(remarks='Changed')
    def test_self_review_always_rejected_including_superusers_and_rejections(self):
        self.grant(self.requester,'review_spareopening');response,_=self.propose()
        for superuser in [False,True]:
            self.requester.is_superuser=superuser;self.requester.save()
            for outcome in ['APPROVED','REJECTED']:
                reviewed,_=self.decide(response.data,actor=self.requester,outcome=outcome);self.assertEqual(reviewed.status_code,403,reviewed.data)
        self.assertEqual(SpareOpeningDecision.objects.count(),0);self.assertEqual(SpareStockMovement.objects.count(),0)
        with self.assertRaises(IntegrityError),transaction.atomic():
            SpareOpeningDecision.objects.create(request_id=response.data['id'],command_id=uuid4(),requester=self.requester,reviewer=self.requester,outcome='REJECTED',remarks='Forbidden self review',payload_digest='0'*64)
    def test_superuser_and_role_labels_do_not_implicitly_grant_opening_authority(self):
        user=get_user_model().objects.create_superuser(username='unconfirmed-superuser',password=None)
        self.assertFalse(can_request(user));self.assertFalse(can_review(user))
        caps=self.auth_client(user).get('/api/spares/capabilities/').data
        self.assertFalse(caps['can_request_opening']);self.assertFalse(caps['can_review_opening']);self.assertFalse(caps['can_open'])
        response,_=self.propose(actor=user);self.assertEqual(response.status_code,403)
        admin=get_user_model().objects.create_user(username='role-only-admin',role='admin');self.grant(admin,'view_spareitem')
        response,_=self.propose(actor=admin);self.assertEqual(response.status_code,403)
    def test_group_and_assigned_role_grants_are_configurable_and_revocable(self):
        from accounts.models import Role
        user=get_user_model().objects.create_user(username='configured-user');self.grant(user,'view_spareitem')
        group=Group.objects.create(name='isolated-opening-requesters');user.groups.add(group)
        permission=Permission.objects.get(codename='request_spareopening')
        group.permissions.add(permission);self.assertTrue(can_request(user));group.permissions.remove(permission);self.assertFalse(can_request(user))
        role=Role.objects.get(code='production_manager');role.permissions.add(Permission.objects.get(codename='review_spareopening'))
        user.assigned_role=role;user.save();self.assertTrue(can_review(user));role.permissions.clear();self.assertFalse(can_review(user))
    def test_direct_opening_is_denied_even_with_forged_approval_fields(self):
        self.requester.is_superuser=True;self.requester.save()
        for extra in [{},{'approved':True,'approved_by':self.reviewer.pk,'opening_request_id':str(uuid4())}]:
            response,_=self.ordinary(is_opening=True,quantity='0',**extra);self.assertEqual(response.status_code,403,response.data)
        with self.assertRaises(Exception) as denied:
            post_movement(self.item.pk,{'request_id':str(uuid4()),'is_opening':True,'movement_type':'IN','quantity':'1','unit':'Each'},self.requester)
        self.assertEqual(getattr(denied.exception,'status_code',None),403);self.assertEqual(SpareStockMovement.objects.count(),0)
    def test_master_creation_cannot_bypass_opening_approval(self):
        user=get_user_model().objects.create_superuser(username='master-superuser',password=None)
        for quantity in ['0','5']:
            response=self.auth_client(user).post('/api/spares/',{'code':'NEW-SP','name':'No bypass','unit':'Each','category':'LOCAL','opening_stock':quantity},format='json');self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(SpareItem.objects.count(),1);self.assertEqual(SpareStockMovement.objects.count(),0)
    def test_request_and_review_retries_bind_actor_payload_and_snapshot(self):
        response,body=self.propose();self.assertEqual(response.status_code,201,response.data)
        replay=self.auth_client(self.requester).post(f'/api/spares/{self.item.pk}/opening-requests/',body,format='json');self.assertEqual(replay.status_code,200);self.assertEqual(replay.data['id'],response.data['id'])
        conflict=self.auth_client(self.requester).post(f'/api/spares/{self.item.pk}/opening-requests/',{**body,'quantity':'99'},format='json');self.assertEqual(conflict.status_code,409)
        wrong,_=self.decide(response.data,expected_digest='0'*64);self.assertEqual(wrong.status_code,409)
        approved,review=self.decide(response.data);self.assertEqual(approved.status_code,200,approved.data)
        replay=self.auth_client(self.reviewer).post(f"/api/spares/opening-requests/{response.data['id']}/review/",review,format='json');self.assertEqual(replay.status_code,200)
        self.assertEqual(replay.data['decision']['id'],approved.data['decision']['id']);self.assertEqual(SpareStockMovement.objects.count(),1)
        changed=self.auth_client(self.reviewer).post(f"/api/spares/opening-requests/{response.data['id']}/review/",{**review,'outcome':'REJECTED'},format='json');self.assertEqual(changed.status_code,409)
    def test_rejection_leaves_unknown_and_allows_new_proposal(self):
        response,_=self.propose();rejected,_=self.decide(response.data,outcome='REJECTED');self.assertEqual(rejected.status_code,200,rejected.data)
        self.assertIsNone(balance(self.item)['current_stock']);self.assertEqual(SpareStockMovement.objects.count(),0)
        again,_=self.propose();self.assertEqual(again.status_code,201,again.data);self.assertEqual(SpareOpeningRequest.objects.count(),2)
    def test_explicit_zero_is_only_known_after_approval(self):
        response,_=self.propose(quantity='0');self.assertEqual(response.status_code,201,response.data);self.assertIsNone(balance(self.item)['current_stock'])
        approved,_=self.decide(response.data);self.assertEqual(approved.status_code,200,approved.data);self.assertEqual(balance(self.item)['opening_stock'],0)
    def test_non_object_request_bodies_are_validation_errors(self):
        for body in [[],[{}],'invalid',7]:
            response=self.auth_client(self.requester).post(f'/api/spares/{self.item.pk}/opening-requests/',body,format='json')
            self.assertEqual(response.status_code,400,response.data)
        proposal,_=self.propose()
        for body in [[],[{}],'invalid',7]:
            response=self.auth_client(self.reviewer).post(f"/api/spares/opening-requests/{proposal.data['id']}/review/",body,format='json')
            self.assertEqual(response.status_code,400,response.data)

    def test_pending_duplicates_and_invalid_proposals_do_not_write_stock(self):
        for changes in [{'quantity':'-1'},{'quantity':'NaN'},{'quantity':'0.0000001'},{'unit':'KG'},{'remarks':''},{'date':(timezone.localdate()+timedelta(days=1)).isoformat()},{'requested_by':self.reviewer.pk}]:
            response,_=self.propose(**changes);self.assertEqual(response.status_code,400,(changes,response.data))
        first,_=self.propose();self.assertEqual(first.status_code,201)
        duplicate,_=self.propose();self.assertEqual(duplicate.status_code,409);self.assertEqual(SpareOpeningRequest.objects.count(),1);self.assertEqual(SpareStockMovement.objects.count(),0)
    def test_ordinary_in_out_validation_history_and_consumables_independence(self):
        from inventory.models import RawMovement
        from accounting.models import PostingEvent,JournalLine
        before=(RawMovement.objects.count(),PostingEvent.objects.count(),JournalLine.objects.count(),ConsumableStockMovement.objects.count())
        unknown,_=self.ordinary();self.assertEqual(unknown.status_code,400)
        proposal,_=self.propose();self.decide(proposal.data)
        received,body=self.ordinary();self.assertEqual(received.status_code,201,received.data)
        replay=self.auth_client(self.requester).post(f'/api/spares/{self.item.pk}/movements/',body,format='json');self.assertEqual(replay.status_code,200)
        out,_=self.ordinary(movement_type='OUT',quantity='3');self.assertEqual(out.status_code,201,out.data);self.assertEqual(balance(self.item)['current_stock'],9)
        too_much,_=self.ordinary(movement_type='OUT',quantity='10');self.assertEqual(too_much.status_code,400)
        wrong_unit,_=self.ordinary(unit='KG');self.assertEqual(wrong_unit.status_code,400)
        reviewer_post,_=self.ordinary(actor=self.reviewer);self.assertEqual(reviewer_post.status_code,403)
        self.assertEqual(self.auth_client(self.requester).get(f'/api/spares/{self.item.pk}/movements/').data['count'],3)
        self.assertEqual(before,(RawMovement.objects.count(),PostingEvent.objects.count(),JournalLine.objects.count(),ConsumableStockMovement.objects.count()))
    def test_queue_visibility_and_no_self_review_capability(self):
        proposal,_=self.propose();other_requester=get_user_model().objects.create_user(username='another-requester');self.grant(other_requester,'view_spareitem','request_spareopening')
        self.assertEqual(self.auth_client(other_requester).get('/api/spares/opening-requests/').data['count'],0)
        queue=self.auth_client(self.reviewer).get('/api/spares/opening-requests/');self.assertEqual(queue.data['count'],1);self.assertTrue(queue.data['results'][0]['can_review'])
        self.grant(self.requester,'review_spareopening');own=self.auth_client(self.requester).get('/api/spares/opening-requests/');self.assertFalse(own.data['results'][0]['can_review'])
        self.assertEqual(self.auth_client(self.requester).patch('/api/spares/opening-requests/',{}).status_code,405)
        self.assertEqual(self.auth_client(self.reviewer).post(f"/api/spares/opening-requests/{proposal.data['id']}/review/",{'command_id':str(uuid4()),'outcome':'APPROVED','expected_digest':proposal.data['payload_digest'],'remarks':'Review','quantity':'999'},format='json').status_code,400)
    def test_unit_changes_and_stale_proposals_require_rejection_not_overwrite(self):
        proposal,_=self.propose();self.auth_client(self.requester).patch(f'/api/spares/{self.item.pk}/',{'unit':'Set'},format='json')
        invalid,_=self.decide(proposal.data);self.assertEqual(invalid.status_code,400);self.assertEqual(SpareOpeningDecision.objects.count(),0);self.assertEqual(SpareStockMovement.objects.count(),0)
        rejected,_=self.decide(proposal.data,outcome='REJECTED');self.assertEqual(rejected.status_code,200)
    def test_audit_failure_rolls_back_decision_movement_and_request_transition(self):
        proposal,_=self.propose();before=EntryRevision.objects.count()
        with patch('inventory.spare_openings.audit',side_effect=RuntimeError('synthetic review audit failure')):
            with self.assertRaises(RuntimeError):self.decide(proposal.data)
        self.assertEqual(SpareStockMovement.objects.count(),0);self.assertEqual(SpareOpeningDecision.objects.count(),0);self.assertIsNone(balance(self.item)['current_stock'])
        self.assertEqual(SpareOpeningRequest.objects.get().status,'PENDING');self.assertEqual(EntryRevision.objects.count(),before)
    def test_permission_revocation_and_schema_failures_are_closed(self):
        proposal,_=self.propose();self.reviewer.user_permissions.clear();denied,_=self.decide(proposal.data);self.assertEqual(denied.status_code,403)
        with patch('inventory.spare_opening_api.storage_status',return_value=(False,'spares_migrations_required')):
            client=self.auth_client(self.requester);caps=client.get('/api/spares/capabilities/');self.assertEqual(caps.status_code,200);self.assertFalse(caps.data['schema_ready']);self.assertFalse(caps.data['can_post'])
            result=client.get('/api/spares/');self.assertEqual(result.status_code,503);self.assertEqual(result.data['code'],'spares_migrations_required')
        user=get_user_model().objects.create_user(username='no-spare-grants');self.assertEqual(self.auth_client(user).get('/api/spares/capabilities/').status_code,403)
        client=APIClient();self.assertEqual(client.get('/api/spares/capabilities/').status_code,401)
    def test_imported_local_filters_and_existing_initialized_spares_remain(self):
        local=SpareItem.objects.create(code='KEEP-LOCAL',name='Local part',category='LOCAL',unit='Both',created_by=self.requester)
        for category in ['LOCAL','IMPORTED']:
            rows=self.auth_client(self.requester).get('/api/spares/',{'category':category});self.assertEqual(rows.status_code,200);self.assertEqual(rows.data['count'],1)
        proposal,_=self.propose(item=local);approved,_=self.decide(proposal.data);self.assertEqual(approved.status_code,200,approved.data)
        second,_=self.propose(item=local);self.assertEqual(second.status_code,400);self.assertEqual(SpareItem.objects.get(pk=local.pk).code,'KEEP-LOCAL')


class SpareOpeningMigrationTests(TransactionTestCase):
    def test_migration_preserves_unknown_and_legacy_openings_and_grants_nothing(self):
        executor=MigrationExecutor(connection);latest=executor.loader.graph.leaf_nodes();before=[('inventory','0011_catalogue_integration')];after=[('inventory','0012_spare_opening_approval')]
        try:
            executor.migrate(before);apps=executor.loader.project_state(before).apps
            # TransactionTestCase flushes seed rows between tests; restore the 0011 fixture.
            apps.get_model('inventory','CatalogueSequence').objects.get_or_create(scope='catalogue-lock',defaults={'value':0})
            user=apps.get_model('accounts','User').objects.create(username='spare-migration-owner')
            model=apps.get_model('inventory','SpareItem');unknown=model.objects.create(code='LEGACY-UNKNOWN',name='Unknown opening',unit='Each',category='LOCAL',created_by_id=user.pk)
            known=model.objects.create(code='LEGACY-ZERO',name='Explicit zero',unit='Both',category='IMPORTED',created_by_id=user.pk)
            apps.get_model('inventory','SpareStockMovement').objects.create(item_id=known.pk,request_id=uuid4(),movement_type='IN',is_opening=True,quantity=0,unit='Both',reference='Original opening evidence',recorded_by_id=user.pk)
            from inventory.spare_opening_api import storage_status
            self.assertEqual(storage_status(),(False,'spares_migrations_required'))
            runtime_user=get_user_model().objects.get(pk=user.pk);runtime_user.is_superuser=True;runtime_user.is_active=True;runtime_user.save()
            client=APIClient();client.force_authenticate(runtime_user)
            listing=client.get('/api/spares/');self.assertEqual(listing.status_code,200,listing.data)
            caps=client.get('/api/spares/capabilities/').data;self.assertTrue(caps['schema_ready']);self.assertFalse(caps['opening_schema_ready']);self.assertFalse(caps['can_open'])
            self.assertEqual(client.get('/api/spares/opening-requests/').status_code,503)
            executor=MigrationExecutor(connection);executor.migrate(after);apps=executor.loader.project_state(after).apps
            self.assertEqual(apps.get_model('inventory','SpareOpeningRequest').objects.count(),0);self.assertEqual(apps.get_model('inventory','SpareOpeningDecision').objects.count(),0)
            self.assertIsNone(balance(SpareItem.objects.get(pk=unknown.pk))['current_stock']);self.assertEqual(balance(SpareItem.objects.get(pk=known.pk))['current_stock'],0);self.assertEqual(SpareItem.objects.get(pk=known.pk).code,'LEGACY-ZERO')
            self.assertFalse(user.user_permissions.filter(codename__in=['request_spareopening','review_spareopening','view_spareopening']).exists())
        finally:MigrationExecutor(connection).migrate(latest)

