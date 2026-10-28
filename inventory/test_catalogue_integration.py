from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from rest_framework.test import APIClient
from .catalog_models import Consumable, SpareItem, ConsumableStockMovement, SpareStockMovement, ConsumableCodeReservation
from .catalogue_service import balance
from erp.models import EntryRevision, SharedEntry

class CatalogueIntegrationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='catalogue-integration')
        self.user.user_permissions.set(Permission.objects.filter(content_type__app_label='inventory'))
        self.client=APIClient(); self.client.force_authenticate(self.user)
    def create(self,**changes):
        body={'name':'Tea','category':'Pantry & Refreshments','subcategory':'Pantry & Refreshments','unit':'KG','opening_stock':'10','minimum_stock':'3','unit_cost':'2.5','department':'Stores','expense_classification':'Custom pantry ledger','creation_request_id':str(uuid4()),**changes}
        r=self.client.post('/api/consumables/',body,format='json'); self.assertEqual(r.status_code,201,r.data)
        return r.data,body
    def movement(self,item,type='IN',**changes):
        body={'request_id':str(uuid4()),'movement_type':type,'quantity':'2','unit':item['unit'],'supplier':'Supplier','storage_location':'Store','department':'Production','issued_to':'Operator','purpose':'Production Support',**changes}
        return self.client.post(f"/api/consumables/{item['id']}/movements/",body,format='json'),body
    def test_generated_codes_metadata_edit_and_request_replay(self):
        item,body=self.create(); self.assertEqual(item['code'],'CON-PAN-001'); self.assertEqual(item['created_by'],self.user.pk); self.assertEqual(item['stock']['current_stock'],'10.000000')
        duplicate=self.client.post('/api/consumables/',body,format='json'); self.assertEqual(duplicate.status_code,201); self.assertEqual(duplicate.data['id'],item['id']); self.assertEqual(Consumable.objects.count(),1)
        conflict=self.client.post('/api/consumables/',{**body,'name':'Changed'},format='json'); self.assertEqual(conflict.status_code,400)
        edited=self.client.patch(f"/api/consumables/{item['id']}/",{'name':'Edited','category':'Housekeeping & Cleaning','subcategory':'Housekeeping & Cleaning','expense_classification':'Custom housekeeping'},format='json')
        self.assertEqual(edited.status_code,200,edited.data); self.assertEqual(edited.data['code'],item['code']); self.assertEqual(edited.data['updated_by'],self.user.pk)
        self.assertEqual(self.client.patch(f"/api/consumables/{item['id']}/",{'code':'CON-HSK-001'}).status_code,400)
        row=Consumable.objects.get(pk=item['id']); row.code='CHANGED'
        with self.assertRaises(ModelValidationError):row.save()
        with self.assertRaises(ModelValidationError):Consumable.objects.filter(pk=item['id']).update(code='CHANGED')
    def test_generated_creation_with_real_jwt_and_cross_session_reload(self):
        from rest_framework_simplejwt.tokens import AccessToken
        client=APIClient();client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        created=client.post('/api/consumables/',{'name':'JWT item','category':'Office Stationery','subcategory':'Office Stationery','unit':'Pack','creation_request_id':str(uuid4())},format='json')
        self.assertEqual(created.status_code,201,created.data); self.assertEqual(created.data['code'],'CON-STA-001')
        self.assertTrue(created['Content-Type'].startswith('application/json'))
        other=APIClient();other.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        loaded=other.get(f"/api/consumables/{created.data['id']}/")
        self.assertEqual(loaded.status_code,200);self.assertEqual(loaded.data['code'],created.data['code'])
        self.assertEqual(other.get('/api/consumables/summary/').data['total_items'],1)

    def test_in_out_history_replay_and_inventory_separation(self):
        from inventory.models import RawMovement
        from accounting.models import PostingEvent, JournalLine
        before=(RawMovement.objects.count(),PostingEvent.objects.count(),JournalLine.objects.count())
        item,_=self.create(); received,body=self.movement(item,unit_cost='3.25',remarks='Receipt'); self.assertEqual(received.status_code,201,received.data); self.assertRegex(received.data['transaction_reference'],r'^CIN-\d{4}-00002$')
        replay=self.client.post(f"/api/consumables/{item['id']}/movements/",body,format='json'); self.assertEqual(replay.status_code,200); self.assertEqual(replay.data['id'],received.data['id'])
        changed=self.client.post(f"/api/consumables/{item['id']}/movements/",{**body,'quantity':'3'},format='json'); self.assertEqual(changed.status_code,400)
        issue,_=self.movement(item,'OUT',quantity='4'); self.assertEqual(issue.status_code,201,issue.data); self.assertTrue(issue.data['transaction_reference'].startswith('COUT-'))
        row=Consumable.objects.get(pk=item['id']); self.assertEqual(balance(row)['current_stock'],Decimal('8')); self.assertEqual(row.unit_cost,Decimal('3.25'))
        rejected,_=self.movement(item,'OUT',quantity='9'); self.assertEqual(rejected.status_code,400); self.assertEqual(balance(row)['current_stock'],8)
        self.assertEqual(self.client.get('/api/consumables/history/').data['count'],3)
        self.assertEqual(self.client.get('/api/consumables/history/?department=Production').data['count'],2)
        self.assertEqual(self.client.get('/api/consumables/activity/?department=Production').data['count'],2)
        self.assertEqual(SpareStockMovement.objects.count(),0)
        self.assertEqual(before,(RawMovement.objects.count(),PostingEvent.objects.count(),JournalLine.objects.count()))
        self.assertFalse(SharedEntry.objects.filter(key='inventory-ledger').exists())
    def test_unknown_and_explicit_zero_opening_and_unit_lock(self):
        item,_=self.create_without_opening()
        self.assertIsNone(item['stock']['current_stock'])
        invalid,_=self.movement(item); self.assertEqual(invalid.status_code,400)
        opened,_=self.movement(item,is_opening=True,quantity='0'); self.assertEqual(opened.status_code,201,opened.data)
        self.assertEqual(self.client.patch(f"/api/consumables/{item['id']}/",{'unit':'Each'}).status_code,400)
        self.assertEqual(self.client.patch(f"/api/consumables/{item['id']}/",{'opening_stock':'100'}).status_code,400)
        duplicate,_=self.movement(item,is_opening=True,quantity='0'); self.assertEqual(duplicate.status_code,400)
    def create_without_opening(self):
        body={'name':'No opening','category':'Office Stationery','subcategory':'Office Stationery','unit':'Pack','creation_request_id':str(uuid4())}
        r=self.client.post('/api/consumables/',body,format='json'); self.assertEqual(r.status_code,201,r.data); return r.data,body
    def test_validation_and_failed_commands_roll_back(self):
        item,_=self.create()
        for changes in [{'quantity':'-1'},{'quantity':'0'},{'quantity':'NaN'},{'quantity':'0.0000001'},{'unit':'Each'},{'department':'No such department'},{'date':'2999-01-01'},{'unit_cost':'-1'}]:
            r,_=self.movement(item,**changes); self.assertEqual(r.status_code,400,(changes,r.data))
        self.assertEqual(ConsumableStockMovement.objects.count(),1)
        with patch('inventory.catalogue_service.audit',side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):self.movement(item)
        self.assertEqual(ConsumableStockMovement.objects.count(),1); self.assertEqual(balance(Consumable.objects.get(pk=item['id']))['current_stock'],10)
    def test_audit_is_server_recorded_and_immutable(self):
        item,_=self.create(); self.client.patch(f"/api/consumables/{item['id']}/",{'name':'Edited','is_active':False},format='json')
        rows=self.client.get('/api/consumables/activity/'); self.assertEqual(rows.status_code,200); actions=[r['content']['action'] for r in rows.data['results']]
        self.assertIn('Item Created',actions); self.assertIn('Item Updated',actions); self.assertIn('Item Activated / Deactivated',actions); self.assertIn('Stock IN',actions)
        for event in rows.data['results']:self.assertEqual(event['actor_id'],self.user.pk); self.assertTrue(event['at'])
        entry=EntryRevision.objects.filter(entry__key__startswith='catalogue-consumables-').first()
        with self.assertRaises(ModelValidationError):EntryRevision.objects.filter(pk=entry.pk).update(content={})
        with self.assertRaises(ModelValidationError):entry.delete()
        movement=ConsumableStockMovement.objects.first()
        with self.assertRaises(ModelValidationError):movement.delete()
        with self.assertRaises(ModelValidationError):ConsumableStockMovement.objects.filter(pk=movement.pk).update(quantity=100)
        self.assertEqual(self.client.patch('/api/consumables/activity/',{}).status_code,405)
        self.assertEqual(self.client.delete(f"/api/consumables/{item['id']}/movements/").status_code,405)
    def test_permissions_are_not_role_name_or_frontend_flags(self):
        other=get_user_model().objects.create_user(username='without-grants',role='admin')
        client=APIClient(); self.assertEqual(client.get('/api/consumables/').status_code,401)
        client.force_authenticate(other); self.assertEqual(client.get('/api/consumables/').status_code,403)
        other.user_permissions.add(Permission.objects.get(codename='view_consumable'))
        other=get_user_model().objects.get(pk=other.pk); client.force_authenticate(other)
        item,_=self.create(); self.assertFalse(client.get('/api/consumables/capabilities/').data['can_post'])
        self.assertEqual(client.post(f"/api/consumables/{item['id']}/movements/",{}).status_code,403)
        self.assertEqual(client.get('/api/consumables/history/').status_code,403); self.assertEqual(client.get('/api/consumables/activity/').status_code,403)
        other.user_permissions.add(Permission.objects.get(codename='add_consumablestockmovement'))
        other=get_user_model().objects.get(pk=other.pk); client.force_authenticate(other)
        self.assertTrue(client.get('/api/consumables/capabilities/').data['can_post'])
        r=client.post(f"/api/consumables/{item['id']}/movements/",{'request_id':str(uuid4()),'movement_type':'OUT','quantity':'100','unit':'KG','department':'Production','issued_to':'Person','purpose':'Other'},format='json'); self.assertEqual(r.status_code,400)
    def test_spares_imported_local_movements_and_legacy_code_format(self):
        for category in ['IMPORTED','LOCAL']:
            r=self.client.post('/api/spares/',{'name':category,'code':f'SP-{category}','category':category,'unit':'Both','expense_classification':'Maintenance','creation_request_id':str(uuid4())},format='json'); self.assertEqual(r.status_code,201,r.data); item=r.data
            body={'request_id':str(uuid4()),'is_opening':True,'movement_type':'IN','quantity':'5','unit':'Both','reference':'Legacy source'}
            from .spare_opening_test_helpers import approved_opening
            opening,_=approved_opening(SpareItem.objects.get(pk=item['id']),self.user,'5','Legacy source');self.assertEqual(opening.reference,'Legacy source')
            issued=self.client.post(f"/api/spares/{item['id']}/movements/",{**body,'request_id':str(uuid4()),'is_opening':False,'movement_type':'OUT','quantity':'2'},format='json');self.assertEqual(issued.status_code,201,issued.data)
            self.assertEqual(self.client.get(f"/api/spares/{item['id']}/stock/").data['current_stock'],'3.000000')
            self.assertEqual(self.client.patch(f"/api/spares/{item['id']}/",{'description':'Edited'}).status_code,200)
            self.assertEqual(self.client.get(f"/api/spares/{item['id']}/").data['code'],item['code'])
        self.assertEqual(Consumable.objects.count(),0); self.assertEqual(ConsumableStockMovement.objects.count(),0); self.assertEqual(self.client.get('/api/spares/activity/').status_code,200)
    def test_filters_summary_and_low_stock_are_all_matching_records(self):
        low,_=self.create(opening_stock='3');zero,_=self.create(opening_stock='0'); self.create(opening_stock='5')
        summary=self.client.get('/api/consumables/summary/').data;self.assertEqual(summary['total_items'],3);self.assertEqual(summary['low_stock'],1);self.assertEqual(summary['out_of_stock'],1)
        self.assertEqual(Decimal(summary['stock_value']),Decimal('20'));self.assertEqual(self.client.get('/api/consumables/?low_stock=true').data['count'],2)
        self.assertEqual(self.client.get('/api/consumables/?category=Pantry%20%26%20Refreshments&department=Stores&is_active=true').data['count'],3)
        self.assertEqual(self.client.get('/api/consumables/?search='+low['code']).data['count'],1)
        self.assertEqual(self.client.get('/api/consumables/?ordering=-code').data['results'][0]['code'],'CON-PAN-003')
        self.assertEqual(self.client.get('/api/consumables/?ordering=stock').data['results'][0]['id'],zero['id'])
        self.assertEqual(self.client.get('/api/consumables/?ordering=-stock').data['results'][0]['code'],'CON-PAN-003')
    def test_reserved_codes_inactive_and_removed_records_are_not_reused(self):
        ConsumableCodeReservation.objects.create(code='CON-PAN-001')
        item,_=self.create();self.assertEqual(item['code'],'CON-PAN-002')
        self.client.patch(f"/api/consumables/{item['id']}/",{'is_active':False},format='json'); next_item,_=self.create();self.assertEqual(next_item['code'],'CON-PAN-003')
        with self.assertRaises(ModelValidationError):ConsumableCodeReservation.objects.filter(code=item['code']).delete()
    def test_legacy_records_not_reclassified_and_new_namespace_not_overridden(self):
        old=Consumable.objects.create(code='LEGACY-CODE',name='Unclassified',unit='Litres',created_by=self.user)
        changed=self.client.patch(f'/api/consumables/{old.pk}/',{'description':'Kept'},format='json');self.assertEqual(changed.status_code,200,changed.data);old.refresh_from_db();self.assertEqual(old.code,'LEGACY-CODE');self.assertEqual(old.category,'');self.assertIsNone(balance(old)['opening_stock'])
        spare=SpareItem.objects.create(code='DATE-UNKNOWN',name='Legacy undated receipt',category='LOCAL',unit='Each',created_by=self.user)
        SpareStockMovement.objects.create(item=spare,request_id=uuid4(),movement_type='IN',quantity=1,unit='Each',reference='Legacy evidence',recorded_by=self.user)
        self.assertIsNone(self.client.get(f'/api/spares/{spare.pk}/').data['last_purchase_date'])
        r=self.client.post('/api/consumables/',{'name':'Override','category':'Pantry & Refreshments','subcategory':'Pantry & Refreshments','unit':'Each','code':'CON-PAN-999'},format='json');self.assertEqual(r.status_code,400)
        r=self.client.post('/api/consumables/',{'name':'Spare','category':'Spare Parts','subcategory':'Imported Spare Parts','unit':'Each'},format='json');self.assertEqual(r.status_code,400)
    def test_backdated_issue_cannot_borrow_later_receipts(self):
        from datetime import timedelta
        from django.utils import timezone
        item,_=self.create_without_opening(); earlier=(timezone.localdate()-timedelta(days=2)).isoformat();later=(timezone.localdate()-timedelta(days=1)).isoformat()
        opened,_=self.movement(item,is_opening=True,quantity='1',date=earlier);self.assertEqual(opened.status_code,201)
        receipt,_=self.movement(item,date=later,quantity='10');self.assertEqual(receipt.status_code,201)
        issued,_=self.movement(item,'OUT',date=earlier,quantity='2');self.assertEqual(issued.status_code,400)

class CatalogueMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_legacy_ids_codes_and_movements(self):
        executor=MigrationExecutor(connection);latest=executor.loader.graph.leaf_nodes()
        before=[('inventory','0010_spares_consumables')];after=[('inventory','0011_catalogue_integration')]
        try:
            executor.migrate(before);apps=executor.loader.project_state(before).apps
            user=apps.get_model('accounts','User').objects.create(username='catalogue-migration')
            old=apps.get_model('inventory','Consumable').objects.create(name='Preserved',code='CON-PAN-001',unit='Litres',created_by_id=user.pk)
            spare=apps.get_model('inventory','SpareItem').objects.create(name='Spare',code='OLD-SPARE',category='LOCAL',unit='Both',created_by_id=user.pk)
            move=apps.get_model('inventory','SpareStockMovement').objects.create(item_id=spare.pk,request_id=uuid4(),movement_type='IN',is_opening=True,quantity=5,unit='Both',reference='ORIGINAL',recorded_by_id=user.pk)
            executor=MigrationExecutor(connection);executor.migrate(after);apps=executor.loader.project_state(after).apps
            preserved=apps.get_model('inventory','Consumable').objects.get(pk=old.pk);self.assertEqual(preserved.code,'CON-PAN-001');self.assertEqual(preserved.category,'');self.assertIsNone(preserved.updated_by_id)
            self.assertTrue(apps.get_model('inventory','ConsumableCodeReservation').objects.filter(code='CON-PAN-001').exists())
            self.assertEqual(apps.get_model('inventory','SpareStockMovement').objects.get(pk=move.pk).reference,'ORIGINAL')
            self.assertEqual(apps.get_model('inventory','SpareItem').objects.get(pk=spare.pk).code,'OLD-SPARE')
        finally:MigrationExecutor(connection).migrate(latest)
