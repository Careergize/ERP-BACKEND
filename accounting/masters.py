"""Chart classification and reusable finance master definitions."""
from .models import AccountGroup, Currency, VoucherType

BASE_VOUCHERS = {
    'receipt':'Receipt', 'payment':'Payment', 'invoice':'Sales', 'bill':'Purchase',
    'expense':'Expense', 'transfer':'Contra', 'journal':'Journal',
}


def seed_masters(group_model=AccountGroup, currency_model=Currency, voucher_model=VoucherType, book=None, account_model=None, document_model=None):
    groups={}
    for kind,name in [('asset','Assets'),('liability','Liabilities'),('equity','Equity'),('income','Income'),('expense','Expenses')]:
        groups[kind],_=group_model.objects.get_or_create(name=name,defaults={'kind':kind})
    if account_model:
        # New group metadata only. Never change posted journal amounts or parties.
        for kind,group in groups.items():
            account_model.objects.filter(kind=kind,group__isnull=True).update(group=group)
    codes={book.currency} if book else set()
    if document_model: codes.update(document_model.objects.values_list('currency',flat=True).distinct())
    for code in codes:
        currency_model.objects.get_or_create(code=code,defaults={'name':code,'symbol':code})
    for kind,name in BASE_VOUCHERS.items():
        voucher_model.objects.get_or_create(name=name,defaults={'base_kind':kind})
