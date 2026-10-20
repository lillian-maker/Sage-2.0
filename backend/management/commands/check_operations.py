"""Read-only by default; actual alert sending is a separate explicit action."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from backend.operations import backup_status, site_status, billing_status, send_fault_alert


class Command(BaseCommand):
    help = 'Check website readiness and supplied off-site backup metadata; does not create backups.'

    def add_arguments(self, parser):
        parser.add_argument('--origin')
        parser.add_argument('--backup-manifest')
        parser.add_argument('--billing', action='store_true')
        parser.add_argument('--send-alerts', action='store_true')

    def handle(self, *args, **options):
        if not options['origin'] and not options['backup_manifest'] and not options['billing']:
            raise CommandError('Specify --origin, --backup-manifest and/or --billing.')
        results = []
        if options['origin']:
            results.append(site_status(options['origin']))
        if options['billing']:
            results.append(billing_status())
        if options['backup_manifest']:
            path = Path(options['backup_manifest'])
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
                    raise ValueError
                manifest = json.loads(path.read_text())
            except (OSError, ValueError):
                raise CommandError('BACKUP_MANIFEST_INVALID') from None
            results.append(backup_status(manifest))
        for result in results:
            if options['send_alerts'] and not result['ok']:
                result['alerts'] = send_fault_alert(result['code'], serious=True)
        self.stdout.write(json.dumps({'mode': 'send-alerts' if options['send_alerts'] else 'read-only',
            'checks': results, 'actual_backup_created': False}, ensure_ascii=False))
        if not all(result['ok'] for result in results):
            raise CommandError('OPERATIONS_CHECK_FAILED')
