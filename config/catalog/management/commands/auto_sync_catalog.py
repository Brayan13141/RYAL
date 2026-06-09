"""
Ejecuta load_productos para la categoría correspondiente al día de la semana.
Pensado para correr vía crontab una vez al día:
  0 3 * * * cd ~/WEB_RYAL && PYTHONUTF8=1 venv/bin/python config/manage.py auto_sync_catalog
"""
from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Semana: 0=lunes … 6=domingo
_SCHEDULE = {
    0: (['gorra'],      'Gorra'),
    1: (['deportiva'],  'Camisetas deportivas'),
    2: (['1:1'],        'Camisetas/Sudaderas 1:1'),
    3: (['g5'],         'Camisetas/Sudaderas G5'),
    4: (['calzado'],    'Calzado'),
    5: (['auricular'],  'Electrónica'),
    6: (['van cleef'],  'Van Cleef & Arpels'),
}


def category_for_weekday(weekday: int):
    """Retorna (keywords, label) para el día dado (0=lunes). None si no hay entrada."""
    return _SCHEDULE.get(weekday)


class Command(BaseCommand):
    help = 'Sincroniza la categoría del día con load_productos (crontab semanal).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--day', type=int, default=None,
            help='Forzar día de semana (0=lunes … 6=domingo). Por defecto: hoy.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué categoría correría sin ejecutar load_productos.',
        )

    def handle(self, *args, **options):
        weekday = options['day'] if options['day'] is not None else date.today().weekday()
        entry = category_for_weekday(weekday)

        if entry is None:
            self.stdout.write(f'Sin categoría programada para día {weekday}.')
            return

        keywords, label = entry
        self.stdout.write(f'[auto_sync_catalog] día {weekday} → {label} (keywords: {keywords})')

        if options['dry_run']:
            self.stdout.write('  --dry-run: no se ejecutó load_productos.')
            return

        call_command('load_productos', category=keywords, verbosity=options['verbosity'])
        self.stdout.write(self.style.SUCCESS(f'  ✓ {label} sincronizada.'))
