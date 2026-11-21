"""Load development/test sample data from backend/workbench/seeds/*.py.

Each seed module defines ORDER (int) and seed(ctx); ctx is a dict shared by all
modules so later seeds can reuse objects created earlier (ctx['users'], ...).
Seeds must be idempotent: running the command twice must not duplicate rows.
"""
import importlib
import pkgutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from backend.workbench import seeds as seeds_package


def seed_modules():
    modules = []
    for info in pkgutil.iter_modules(seeds_package.__path__):
        if info.name.startswith('_'):
            continue
        module = importlib.import_module('%s.%s' % (seeds_package.__name__, info.name))
        modules.append((getattr(module, 'ORDER', 100), info.name, module))
    return sorted(modules, key=lambda item: (item[0], item[1]))


class Command(BaseCommand):
    help = 'Seed app workbench sample data (SAGE_ENV development/test only).'

    def add_arguments(self, parser):
        parser.add_argument('--only', action='append', default=[], help='Seed module name; repeatable.')

    def handle(self, *args, **options):
        if settings.ENVIRONMENT not in ('development', 'test'):
            raise CommandError('SEED_ENVIRONMENT_FORBIDDEN')
        modules = seed_modules()
        names = {name for _, name, _ in modules}
        unknown = set(options['only']) - names
        if unknown:
            raise CommandError('UNKNOWN_SEED ' + ', '.join(sorted(unknown)))
        selected = [item for item in modules if not options['only'] or item[1] in options['only']]
        ctx = {}
        with transaction.atomic():
            for _, name, module in selected:
                module.seed(ctx)
                self.stdout.write('seeded %s' % name)
