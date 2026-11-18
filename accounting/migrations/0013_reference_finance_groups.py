from django.db import migrations


GROUPS = {
    'asset': (
        'Bank Accounts', 'Cash-in-Hand', 'Current Assets', 'Deposits (Asset)',
        'Fixed Assets', 'Investments', 'Loans & Advances (Asset)', 'Misc. Expenses (ASSET)',
        'Stock-in-Hand', 'Sundry Debtors', 'Opening Stock', 'Closing Stock', 'Finished Goods',
    ),
    'liability': (
        'Bank OCC A/c', 'Bank OD A/c', 'Current Liabilities', 'Duties & Taxes',
        'Loans (Liability)', 'Provisions', 'Secured Loans', 'Sundry Creditors', 'Unsecured Loans',
    ),
    'equity': ('Branch / Divisions', 'Capital Account', 'Reserves & Surplus', 'Retained Earnings', 'Suspense A/c'),
    'income': ('Direct Incomes', 'Income (Direct)', 'Income (Indirect)', 'Indirect Incomes', 'Sales Accounts'),
    'expense': (
        'Direct Expenses', 'Expenses (Direct)', 'Expenses (Indirect)', 'Indirect Expenses',
        'Purchase Accounts', 'Purchase Expenses', 'Admin & Operation Expenses', 'Employment Expenses',
        'Finance Expenses', 'Fuel Expense', 'Pex', 'Remuneration', 'Salary Account',
        'Staff Welfare', 'Vehicle Maintenance',
    ),
}

PARENTS = {
    'Finished Goods': 'Stock-in-Hand',
    **dict.fromkeys(('Admin & Operation Expenses', 'Employment Expenses', 'Finance Expenses',
                     'Fuel Expense', 'Pex', 'Remuneration', 'Salary Account',
                     'Staff Welfare', 'Vehicle Maintenance'), 'Indirect Expenses'),
}


def add_finance_groups(apps, schema_editor):
    alias = schema_editor.connection.alias
    groups = apps.get_model('accounting', 'AccountGroup').objects.using(alias)
    accounts = apps.get_model('accounting', 'Account').objects.using(alias)
    saved = {}
    for kind, names in GROUPS.items():
        for name in names:
            existing = groups.filter(name__iexact=name).first()
            parent = saved.get(PARENTS.get(name))
            saved[name] = existing or groups.create(
                name=name, kind=kind, parent=parent if parent and parent.kind == kind else None,
            )

    direct = saved['Direct Expenses']
    if direct.kind != 'expense':
        return
    salary = accounts.filter(name__iexact='Salary Advance', kind='expense').order_by('code').first()
    if salary:
        if not salary.group_id and salary.pnl_section in ('', 'direct_expense'):
            accounts.filter(pk=salary.pk).update(group=direct, pnl_section='direct_expense')
        return
    if not accounts.filter(name__iexact='Salary Advance').exists():
        accounts.get_or_create(code='SALARY-ADVANCE', defaults={
            'name': 'Salary Advance', 'kind': 'expense', 'group': direct,
            'pnl_section': 'direct_expense',
        })


class Migration(migrations.Migration):
    dependencies = [('accounting', '0012_document_receipt_mode')]
    operations = [migrations.RunPython(add_finance_groups, migrations.RunPython.noop)]
