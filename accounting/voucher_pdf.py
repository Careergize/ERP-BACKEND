"""Read-only posted voucher download using the existing ReportLab dependency."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .api import AccountingAPIView
from .models import Book, Document
from .reports import document_data
from .services import day, require, ZERO

NAMES={'invoice':'Sales invoice','bill':'Supplier bill','receipt':'Customer receipt','payment':'Supplier payment',
       'expense':'Expense','transfer':'Contra / transfer','journal':'Journal','opening':'Opening balances',
       'reverse':'Reversal','assign-cost':'Cost assignment','stock-receipt':'Stock valuation receipt','stock-issue':'Stock valuation issue'}


def voucher_pdf(data, company, reporting_currency, as_of):
    output=BytesIO();styles=getSampleStyleSheet()
    fonts=Path(reportlab.__file__).parent/'fonts'
    font='Helvetica';bold='Helvetica-Bold'
    if (fonts/'Vera.ttf').exists() and (fonts/'VeraBd.ttf').exists():
        if 'VoucherSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('VoucherSans',str(fonts/'Vera.ttf')))
            pdfmetrics.registerFont(TTFont('VoucherSansBold',str(fonts/'VeraBd.ttf')))
        font='VoucherSans';bold='VoucherSansBold'
    normal=ParagraphStyle('VoucherBody',parent=styles['Normal'],fontName=font,fontSize=8,leading=12,wordWrap='CJK')
    strong=ParagraphStyle('VoucherStrong',parent=normal,fontName=bold)
    heading=ParagraphStyle('VoucherHeading',parent=normal,fontName=bold,fontSize=17,leading=23,textColor=colors.HexColor('#123f31'))
    small=ParagraphStyle('VoucherSmall',parent=normal,fontSize=7,leading=10,textColor=colors.HexColor('#52665c'))
    right=ParagraphStyle('VoucherRight',parent=normal,alignment=TA_RIGHT)
    def p(value,style=normal):return Paragraph(escape(str(value if value not in (None,'') else '-')).replace('\n','<br/>'),style)
    def amount(value):return f'{Decimal(str(value)):,.2f}'
    def qty(value):return format(Decimal(str(value)),'f').rstrip('0').rstrip('.') if '.' in str(value) else str(value)
    def table(headers,rows,widths,numeric=()):
        cells=[[p(h,strong) for h in headers]]+[[p(v,right if i in numeric else normal) for i,v in enumerate(row)] for row in rows]
        t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf3ed')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#97afa2')),('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#dae4de')),
            ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6)]))
        return t
    width=A4[0]-84
    title=data.get('voucherTypeName') or NAMES.get(data['kind'],data['kind'])
    status='REVERSED' if data.get('reversed') else 'POSTED REVERSAL' if data.get('reversalOf') else 'POSTED'
    story=[p('ACCOUNTING VOUCHER',small),p(company,heading),Spacer(1,8),p(title,strong),p(data['number'],heading),
           p(f"Date: {data['date']}  |  Status: {status}  |  Record ID: {data['id']}",small),Spacer(1,14)]
    fields=[['Party / particulars',data.get('partyName') or 'Company account'],['Cost category',data.get('costCategoryName') or 'Uncategorised'],
            ['Voucher amount',f"{data['currency']} {amount(data['total'])}"],['Saved exchange rate',f"{data.get('fx','1')} {reporting_currency} per 1 {data['currency']}"],
            ['Balances as of',str(as_of)]]
    if data.get('outstanding') is not None:fields.append(['Outstanding invoice / bill',f"{data['currency']} {amount(data['outstanding'])}"])
    if data.get('unallocated') is not None:fields.append(['Unallocated receipt / payment',f"{data['currency']} {amount(data['unallocated'])}"])
    if data.get('reversalOf'):fields.append(['Reversal of record',data['reversalOf']])
    story+=[table(['Field','Details'],fields,[150,width-150]),Spacer(1,14),p('Payment accounts and journal',strong)]
    journal=data.get('journal',[])
    journal_rows=[[r['account'],r['accountName'],r.get('partyName') or '-',amount(r['debit']),amount(r['credit'])] for r in journal]
    journal_rows.append(['','TOTAL','',amount(sum((Decimal(r['debit']) for r in journal),ZERO)),amount(sum((Decimal(r['credit']) for r in journal),ZERO))])
    story+=[p(f'Journal debit/credit amounts are in {reporting_currency}.',small),Spacer(1,5),table(['Code','Account','Party','Debit','Credit'],journal_rows,[46,143,132,95,width-416],numeric=(3,4))]
    cash=[r['accountName'] for r in journal if r.get('cash')]
    if cash:story+=[Spacer(1,5),p('Cash / bank / mobile money: '+', '.join(dict.fromkeys(cash)),small)]
    if data.get('items'):
        item_rows=[[r['description'],qty(r['quantity']),r['unit'],qty(r['price']),amount(r['net']),amount(r['tax'])] for r in data['items']]
        story+=[Spacer(1,16),p(f"Items - amounts in {data['currency']}",strong),Spacer(1,5),table(['Description','Quantity','Unit','Rate','Net','Tax'],item_rows,[176,53,40,70,86,width-425],numeric=(1,3,4,5))]
    if data.get('allocations'):
        allocation_rows=[[r['billNumber'],', '.join(r.get('items',[])),amount(r['amount'])] for r in data['allocations']]
        story+=[Spacer(1,16),p(f"Payment allocations - {data['currency']}",strong),Spacer(1,5),table(['Invoice / bill','Items / allocation','Amount'],allocation_rows,[135,width-240,105],numeric=(2,))]
    if data.get('notes'):story+=[Spacer(1,16),p('Narration',strong),p(data['notes'])]
    story+=[Spacer(1,16),p('Source: '+str(data.get('source') or 'Manual accounting entry'),small),
            p('Recorded: '+str(data.get('createdAt','-'))+' | Actor ID: '+str(data.get('actor','-')),small),
            p('Generated from posted accounting records. Account/party names are current labels; balances and reversal status use the as-of date shown above. This download does not create another transaction.',small)]
    def footer(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(colors.HexColor('#d0ded5'));canvas.line(42,40,A4[0]-42,40)
        canvas.setFont(font,7);canvas.setFillColor(colors.HexColor('#52665c'));canvas.drawString(42,27,'Accounting voucher | '+str(data['id']))
        canvas.drawRightString(A4[0]-42,27,f'Page {doc.page}');canvas.restoreState()
    SimpleDocTemplate(output,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=38,bottomMargin=56,
                      title=str(data['number']),author=str(company)).build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


class VoucherPDF(AccountingAPIView):
    def get(self,request,pk):
        doc=get_object_or_404(Document.objects.select_related('party','reversal_of','voucher_type','cost_category'),pk=pk)
        as_of=day(request.query_params['asOf']) if request.query_params.get('asOf') else timezone.localdate()
        require(doc.date<=as_of,'The voucher is after the requested as-of date.')
        book=Book.objects.first();require(book is not None,'Configure accounting first.')
        response=HttpResponse(voucher_pdf(document_data(doc,as_of),book.company,book.currency,as_of),content_type='application/pdf')
        filename=re.sub(r'[^A-Za-z0-9_.-]+','-',doc.number).strip('.-')[:80] or f'voucher-{doc.pk}'
        response['Content-Disposition']=f'attachment; filename="{filename}.pdf"'
        response['Cache-Control']='private, no-store'
        return response
