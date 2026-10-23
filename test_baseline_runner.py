"""Task 01 isolated checks. Never loads .env or opens the configured database.

Usage: python -B test_baseline_runner.py check|drift|graph|test [test labels...]
Use a disposable venv installed from requirements.txt. No persistent test DB option.
"""
import os
from pathlib import Path
import socket
import sys
import types
from unittest.mock import patch


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if mode not in {'check', 'drift', 'graph', 'test'}:
        raise SystemExit('Only check, drift, graph and test are supported.')
    root = Path(__file__).resolve().parent
    isolated = types.ModuleType('task01_isolated_settings')
    isolated.__file__ = str(root / 'config' / 'settings.py')
    # Evaluate the actual settings with inert environment values, without dotenv I/O.
    with patch.dict(os.environ, {
        'DJANGO_SECRET_KEY': 'task01-disposable-test-key-never-use-operationally',
        'DJANGO_DEBUG': 'true', 'DJANGO_ALLOWED_HOSTS': 'testserver,localhost',
    }, clear=True), patch('dotenv.load_dotenv', return_value=False):
        exec(compile(Path(isolated.__file__).read_text(), isolated.__file__, 'exec'), isolated.__dict__)
    isolated.DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }}
    isolated.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    # No provider credentials survive the cleared environment above.
    sys.modules[isolated.__name__] = isolated
    os.environ['DJANGO_SETTINGS_MODULE'] = isolated.__name__

    def deny_network(*args, **kwargs):
        raise RuntimeError('Task 01 blocks outbound network connections.')

    import django
    with patch.object(socket.socket, 'connect', deny_network), patch.object(socket.socket, 'connect_ex', deny_network):
        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connections
        assert settings.DATABASES['default']['NAME'] == ':memory:'
        assert connections['default'].settings_dict['TEST']['NAME'] == ':memory:'
        print('SAFETY: dotenv skipped; DB=:memory:; test DB=:memory:; outbound connect blocked.', flush=True)
        if mode == 'test':
            call_command('test', *(sys.argv[2:] or ['accounts', 'erp', 'inventory', 'quotations']), verbosity=2, interactive=False)
        elif mode == 'drift':
            call_command('makemigrations', check=True, dry_run=True, verbosity=2)
        elif mode == 'graph':
            from django.db.migrations.loader import MigrationLoader
            loader = MigrationLoader(None)
            loader.graph.validate_consistency()
            for key in sorted(loader.disk_migrations):
                print(f'{key[0]}/{key[1]} <- {loader.disk_migrations[key].dependencies}')
            print('LEAVES:', loader.graph.leaf_nodes())
        else:
            call_command('check')


if __name__ == '__main__':
    main()
