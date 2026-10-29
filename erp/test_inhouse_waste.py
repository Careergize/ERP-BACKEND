from uuid import uuid4
from io import BytesIO
from zipfile import ZipFile
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .inhouse_waste import FIELDS

class InhouseWasteTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('waste',role='recycling_operator',assigned_role=Role.objects.get(code='recycling_operator'))
        self.client.force_authenticate(self.user)
        self.row = dict(id='d1',date='2026-08-01',notes='',status='Active',correctionReason='',**{f:'0' for f in FIELDS})
        self.rates = dict(id='r1',month='2026-08',currency='UGX',blackRate='2000',clearLdRate='2500',mixedRate='2400',ppRate='2500',correctionReason='')
    def put(self,rows,version=0,key='inhouse-waste-entries'):
        return self.client.put('/api/shared-entries/'+key+'/',dict(content=rows,expectedVersion=version,commandId=str(uuid4())),format='json')
    def report(self,**params):
        return self.client.get('/api/recycling/inhouse-waste/report/',{'month':'2026-08',**params})
    def test_totals_valuation_and_missing_rates(self):
        row={**self.row,**dict(zip(FIELDS,['9351.8','423','1972.3','210','13.9','469.9','393.3','7.1']))}
        self.assertEqual(self.put([row,{**self.row,'id':'other','date':'2026-09-01','pp':'100'}]).status_code,200)
        self.assertIsNone(self.report().data['amount'])
        self.assertEqual(self.put([self.rates],key='inhouse-waste-rates').status_code,200)
        d=self.report().data
        self.assertEqual(d['totals']['total'],'12841.3')
        self.assertEqual(d['totals']['hdpeTotal'],'11957.1')
        self.assertEqual(d['totals']['ldpeTotal'],'877.1')
        self.assertEqual([r['quantity'] for r in d['valuation']],['9351.8','469.9','3012.5','7.1'])
        self.assertEqual(d['amount'],'27126100.00')
    def test_validation_correction_void_and_conflict(self):
        for field,value in [('hdpeBlack','-1'),('pp','NaN'),('pp','1.0001'),('date','2026-02-30'),('notes','\x00')]:
            self.assertEqual(self.put([{**self.row,field:value}]).status_code,400)
        self.assertEqual(self.put([self.row,{**self.row,'id':'dup'}]).status_code,400)
        self.assertEqual(self.put([self.row]).status_code,200)
        self.assertEqual(self.put([self.row]).status_code,409)
        self.assertEqual(self.put([],1).status_code,400)
        self.assertEqual(self.put([{**self.row,'pp':'10'}],1).status_code,400)
        corrected={**self.row,'pp':'10','correctionReason':'Weighed again'}
        self.assertEqual(self.put([corrected],1).status_code,200)
        void={**corrected,'status':'Voided','correctionReason':'Duplicate'}
        self.assertEqual(self.put([void],2).status_code,200)
        self.assertEqual(self.report().data['totals']['total'],'0')
        self.assertEqual(self.put([corrected],3).status_code,400)
    def test_rates_validation_history_and_export(self):
        for changes in [dict(currency=''),dict(month='2026-13'),dict(ppRate='-2')]:
            self.assertEqual(self.put([{**self.rates,**changes}],key='inhouse-waste-rates').status_code,400)
        self.assertEqual(self.put([self.rates],key='inhouse-waste-rates').status_code,200)
        self.assertEqual(self.put([{**self.rates,'ppRate':'1'}],1,'inhouse-waste-rates').status_code,400)
        self.assertEqual(self.put([{**self.rates,'ppRate':'1','correctionReason':'New rate'}],1,'inhouse-waste-rates').status_code,200)
        self.assertEqual(self.put([{**self.row,'notes':'=SUM(1,2)'}]).status_code,200)
        result=self.report(export='xlsx')
        self.assertEqual(result.status_code,200)
        with ZipFile(BytesIO(result.content)) as z:
            xml=z.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('=SUM(1,2)',xml)
            self.assertNotIn('<f>',xml)
            self.assertIn('Monthly valuation',z.read('xl/worksheets/sheet2.xml').decode())
        self.assertEqual(self.report(month='2026-8').status_code,400)
        self.assertEqual(self.report(export='pdf').status_code,400)
    def test_permissions(self):
        for role,code in [('project_manager',200),('qc_inspector',403)]:
            user=get_user_model().objects.create_user(role,role=role,assigned_role=Role.objects.get(code=role))
            self.client.force_authenticate(user)
            self.assertEqual(self.report().status_code,code)
            self.assertEqual(self.put([self.row]).status_code,403)
            self.assertEqual(self.put([self.rates],key='inhouse-waste-rates').status_code,403)
        self.client.force_authenticate(None)
        self.assertEqual(self.report().status_code,401)

    def test_custom_item_quantity_rate_and_export(self):
        item = dict(id='custom-1', name='HDPE Blue')
        key = 'inhouse-waste-items'
        self.assertEqual(self.put([item], key=key).status_code, 200)
        self.assertEqual(self.put([item, dict(id='custom-2', name='hdpe blue')], 1, key).status_code, 400)
        self.assertEqual(self.put([], 1, key).status_code, 400)
        self.assertEqual(self.put([{**item, 'name':'Renamed'}], 1, key).status_code, 400)
        self.assertEqual(self.put([{**self.row, 'extraQuantities':{'unknown':'1'}}]).status_code, 400)
        self.assertEqual(self.put([{**self.row, 'extraQuantities':{'custom-1':'-1'}}]).status_code, 400)
        row = {**self.row, 'hdpeBlack':'2', 'extraQuantities':{'custom-1':'3.125'}}
        self.assertEqual(self.put([row]).status_code, 200)
        self.assertEqual(self.put([self.rates], key='inhouse-waste-rates').status_code, 200)
        d = self.report().data
        self.assertEqual(d['totals']['total'], '5.125')
        self.assertIsNone(d['amount'])
        self.assertIsNone(d['valuation'][-1]['rate'])
        updated = {**self.rates, 'customRates':{'custom-1':'100.1'}, 'correctionReason':'Set new item rate'}
        self.assertEqual(self.put([updated], 1, 'inhouse-waste-rates').status_code, 200)
        d = self.report().data
        self.assertEqual(d['valuation'][-1]['amount'], '312.81')
        self.assertEqual(d['amount'], '4312.81')
        with ZipFile(BytesIO(self.report(export='xlsx').content)) as z:
            self.assertIn('HDPE Blue kg', z.read('xl/worksheets/sheet1.xml').decode())
            self.assertIn('HDPE Blue', z.read('xl/worksheets/sheet2.xml').decode())
        # A later catalog addition must not rewrite historical entries or invent amounts.
        self.assertEqual(self.put([item, dict(id='custom-2', name='PET Green')], 1, key).status_code, 200)
        self.assertEqual(self.report().data['amount'], '4312.81')
        self.assertEqual(self.put([{**row,'status':'Voided','correctionReason':'Duplicate'}],1).status_code,200)
        self.assertEqual(self.report().data['totals']['total'],'0')
