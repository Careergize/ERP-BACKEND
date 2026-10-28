"""Credit documents using the project's existing ReportLab dependency and layout approach."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle


def credit_pdf(row,preview):
    if not preview and row.status!='Issued':raise ValueError('Only issued credit notes may be official documents.')
    invoice=row.snapshot['invoice'];line=row.snapshot['line'];company=invoice.get('company',{});styles=getSampleStyleSheet();output=BytesIO()
    def p(value):return Paragraph(escape(str(value)),styles['Normal'])
    story=[p('DRAFT PREVIEW — NOT ISSUED' if preview else 'ISSUED CREDIT NOTE'),Spacer(1,12)]
    for text in [company.get('name',''),company.get('address',''),f"Tax ID: {company.get('taxId','')}",f"{company.get('email','')} {company.get('contact','')}",f'{row.number} | {row.date} | Status: {row.status}',f"Customer: {invoice['customer']} (ID {row.customer_id})",f"Original invoice: {invoice['number']} | Return: {row.sales_return.number}"]:story.append(p(text))
    table=Table([[p(s) for s in ['Item','Quantity','Unit','Original price','Net','VAT','LED','Credit']], [p(s) for s in [line['product'],row.quantity,line['unit'],line['unitPrice'],row.net,row.vat,row.led,row.total]]],repeatRows=1)
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,'grey'),('VALIGN',(0,0),(-1,-1),'TOP')]))
    story+=[Spacer(1,12),table,Spacer(1,12),p(f"Total credit: {invoice['currency']} {row.total}"),p('Reason: '+row.reason),p('Remarks: '+row.remarks),p('General ledger: Not posted. This credit note is not a cash refund; receivables have not been adjusted.')]
    SimpleDocTemplate(output,pagesize=A4,title=row.number,author=company.get('name','')).build(story)
    return output.getvalue()
