from .models import Account, AccountGroup, Party

LEDGERS = (
    ('Machinery', 'Fixed Assets', 'asset'),
    ('Motor Vehicles', 'Fixed Assets', 'asset'),
    ('Furniture & Fittings', 'Fixed Assets', 'asset'),
    ('Computer Equipment', 'Fixed Assets', 'asset'),
    ('Cash', 'Cash-in-Hand', 'asset'),
    ('Bank - Operating', 'Bank Accounts', 'asset'),
    ('Customer A', 'Sundry Debtors', 'customer'),
    ('Supplier A', 'Sundry Creditors', 'supplier'),
    ('Raw Material Stock', 'Stock-in-Hand', 'asset'),
    ('VAT Payable', 'Duties & Taxes', 'liability'),
    ('PAYE Payable', 'Duties & Taxes', 'liability'),
    ('NSSF Payable', 'Current Liabilities', 'liability'),
    ('Bank Loan', 'Secured Loans', 'liability'),
    ('Director Loan', 'Unsecured Loans', 'liability'),
    ('Share Capital', 'Capital Account', 'equity'),
    ('Sales', 'Sales Accounts', 'income'),
    ('Purchases', 'Purchase Accounts', 'expense'),
    ('Factory Wages', 'Direct Expenses', 'expense'),
    ('Electricity', 'Indirect Expenses', 'expense'),
    ('Office Rent', 'Indirect Expenses', 'expense'),
    ('Interest Received', 'Indirect Incomes', 'income'),
    ('Depreciation', 'Indirect Expenses', 'expense'),
)

PNL_SECTIONS = {
    'Sales Accounts': 'sales',
    'Direct Incomes': 'sales',
    'Purchase Accounts': 'direct_expense',
    'Direct Expenses': 'direct_expense',
    'Indirect Expenses': 'indirect_expense',
    'Indirect Incomes': 'other_income',
}


def seed_tally_ledgers(account_model=Account, group_model=AccountGroup, party_model=Party, alias='default'):
    accounts = account_model.objects.using(alias)
    groups = group_model.objects.using(alias)
    parties = party_model.objects.using(alias)
    for index, (name, group_name, kind) in enumerate(LEDGERS, 1):
        is_party = kind in ('customer', 'supplier')
        group_kind = {'customer': 'asset', 'supplier': 'liability'}.get(kind, kind)
        group = groups.filter(name__iexact=group_name).first()
        if group is None or group.kind != group_kind:
            continue
        manager = parties if is_party else accounts
        matches = list(manager.filter(name__iexact=name, kind=kind)[:2])
        if matches:
            if len(matches) != 1:
                continue
            ledger = matches[0]
            if ledger.group_id is None or (
                not is_party and ledger.group.name == {'asset': 'Assets', 'income': 'Income'}.get(kind)
            ):
                ledger.group = group
                ledger.save(update_fields=['group'], using=alias)
            continue
        if is_party:
            parties.create(name=name, kind=kind, group=group)
        else:
            accounts.get_or_create(code=f'TALLY-{index:02}', defaults={
                'name': name, 'kind': kind, 'group': group,
                'cash': group_name in ('Cash-in-Hand', 'Bank Accounts'),
                'pnl_section': PNL_SECTIONS.get(group_name, ''),
            })
