"""Spares-only file-backed SQLite race checks. Uses no dotenv/shared database/network."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4


def main():
    root=Path(__file__).resolve().parent
    isolated=types.ModuleType('spares_opening_concurrency_settings');isolated.__file__=str(root/'config'/'settings.py')
    with tempfile.TemporaryDirectory(prefix='spares-opening-test-',dir=root/'tmp') as directory:
        with patch.dict(os.environ,{'DJANGO_SECRET_KEY':'disposable-spare-opening-races-only','DJANGO_DEBUG':'true','DJANGO_ALLOWED_HOSTS':'testserver,localhost'},clear=True),patch('dotenv.load_dotenv',return_value=False):
            exec(compile(Path(isolated.__file__).read_text(),isolated.__file__,'exec'),isolated.__dict__)
        isolated.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(Path(directory)/'isolated.sqlite3'),'OPTIONS':{'timeout':10}}}
        isolated.EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
        sys.modules[isolated.__name__]=isolated;os.environ['DJANGO_SETTINGS_MODULE']=isolated.__name__
        def deny_network(*args,**kwargs):raise RuntimeError('Outbound network blocked in spare-opening race tests.')
        with patch.object(socket.socket,'connect',deny_network),patch.object(socket.socket,'connect_ex',deny_network):
            import django
            django.setup()
            from django.core.management import call_command
            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Permission
            from django.db import connections
            from django.utils import timezone
            from rest_framework.test import APIClient
            from rest_framework_simplejwt.tokens import AccessToken
            from inventory.catalog_models import SpareItem,SpareStockMovement,ConsumableStockMovement
            from inventory.catalogue_service import balance
            from inventory.spare_opening_models import SpareOpeningRequest,SpareOpeningDecision
            try:
                assert Path(isolated.DATABASES['default']['NAME']).parent==Path(directory)
                call_command('migrate',interactive=False,verbosity=0)
                users=[get_user_model().objects.create_user(username=f'isolated-spare-actor-{i}') for i in range(3)]
                def grant(user,*names):user.user_permissions.add(*Permission.objects.filter(content_type__app_label='inventory',codename__in=names))
                grant(users[0],'view_spareitem','request_spareopening','add_sparestockmovement','view_sparestockmovement')
                for user in users[1:]:grant(user,'view_spareitem','review_spareopening')
                items=[SpareItem.objects.create(code=f'KEEP-SP-{i}',name=f'Isolated spare {i}',category='IMPORTED' if i%2 else 'LOCAL',unit='Each',created_by=users[0]) for i in range(3)]
                def client(user):
                    c=APIClient();c.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}');return c
                def parallel(actors,bodies,path):
                    barrier=Barrier(len(bodies))
                    def worker(pair):
                        user_id,body=pair
                        try:
                            user=get_user_model().objects.get(pk=user_id);c=client(user);barrier.wait(timeout=20)
                            r=c.post(path,body,format='json');return r.status_code,r.data
                        finally:connections.close_all()
                    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:return list(pool.map(worker,zip(actors,bodies)))
                proposal=lambda:{'submission_id':str(uuid4()),'quantity':'10','unit':'Each','date':timezone.localdate().isoformat(),'remarks':'Verified synthetic physical count'}
                path=f'/api/spares/{items[0].pk}/opening-requests/'
                raced=parallel([users[0].pk]*2,[proposal(),proposal()],path)
                assert sorted(s for s,_ in raced)==[201,409],raced
                opening=next(row for status,row in raced if status==201)
                assert SpareOpeningRequest.objects.filter(item=items[0]).count()==1
                assert balance(items[0])['opening_stock'] is None
                users[0].is_superuser=True;users[0].save();grant(users[0],'review_spareopening')
                decision=lambda row:{'command_id':str(uuid4()),'outcome':'APPROVED','expected_digest':row['payload_digest'],'remarks':'Independent synthetic count verification'}
                self_review=client(users[0]).post(f"/api/spares/opening-requests/{opening['id']}/review/",decision(opening),format='json')
                assert self_review.status_code==403,self_review.data
                users[0].is_superuser=False;users[0].save()
                approved=parallel([users[1].pk,users[2].pk],[decision(opening),decision(opening)],f"/api/spares/opening-requests/{opening['id']}/review/")
                assert sorted(s for s,_ in approved)==[200,409],approved
                assert SpareOpeningDecision.objects.filter(request_id=opening['id']).count()==1
                assert balance(items[0])['current_stock']==10
                body=proposal();replays=parallel([users[0].pk]*2,[body,body],f'/api/spares/{items[1].pk}/opening-requests/')
                assert sorted(s for s,_ in replays)==[200,201],replays
                assert len({r['id'] for _,r in replays})==1
                request=replays[0][1];body=decision(request)
                duplicate=parallel([users[1].pk]*2,[body,body],f"/api/spares/opening-requests/{request['id']}/review/")
                assert [s for s,_ in duplicate]==[200,200],duplicate
                assert len({r['decision']['id'] for _,r in duplicate})==1
                issue=lambda:{'request_id':str(uuid4()),'movement_type':'OUT','quantity':'6','unit':'Each'}
                path=f'/api/spares/{items[0].pk}/movements/'
                issues=parallel([users[0].pk]*2,[issue(),issue()],path)
                assert sorted(s for s,_ in issues)==[201,400],issues
                assert balance(items[0])['current_stock']==4
                receipt={'request_id':str(uuid4()),'movement_type':'IN','quantity':'2','unit':'Each'}
                repeated=parallel([users[0].pk]*2,[receipt,receipt],path)
                assert sorted(s for s,_ in repeated)==[200,201],repeated
                assert balance(items[0])['current_stock']==6
                third=client(users[0]).post(f'/api/spares/{items[2].pk}/opening-requests/',proposal(),format='json');assert third.status_code==201,third.data
                reject={**decision(third.data),'outcome':'REJECTED'}
                compete=parallel([users[1].pk,users[2].pk],[decision(third.data),reject],f"/api/spares/opening-requests/{third.data['id']}/review/")
                assert sorted(s for s,_ in compete)==[200,409],compete
                final=SpareOpeningRequest.objects.get(pk=third.data['id'])
                assert (balance(items[2])['current_stock']==10) if final.status=='APPROVED' else (balance(items[2])['current_stock'] is None)
                assert SpareOpeningDecision.objects.filter(request=final).count()==1
                refs=list(SpareStockMovement.objects.values_list('transaction_reference',flat=True));assert len(refs)==len(set(refs))
                assert ConsumableStockMovement.objects.count()==0
                connections.close_all()
                assert SpareOpeningRequest.objects.count()==3
                print('PASS: temporary file-backed SQLite + real JWT: duplicate proposals, competing reviewers, replayed decisions, superuser self-review denial, approve/reject race, concurrent ordinary overspend/replay, unique references, persistence and Consumables separation. No shared DB or network used.')
            finally:connections.close_all()


if __name__=='__main__':main()
