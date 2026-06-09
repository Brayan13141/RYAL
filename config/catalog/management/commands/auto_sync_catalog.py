"""
Scrape + carga la categoría del día. Crontab diario en el servidor:
  0 2 * * * cd ~/WEB_RYAL && PYTHONUTF8=1 venv/bin/python config/manage.py auto_sync_catalog >> /var/log/ryal_sync.log 2>&1
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Semana: 0=lunes … 6=domingo
# (keywords_load, label, scraper_kw)
# scraper_kw=None → no hay scraper para esa categoría (calzado usa yupoo)
_SCHEDULE = {
    0: (['gorra'],      'Gorra',                    'gorra'),
    1: (['deportiva'],  'Camisetas deportivas',      'deportiva'),
    2: (['1:1'],        'Camisetas/Sudaderas 1:1',   '1:1'),
    3: (['g5'],         'Camisetas/Sudaderas G5',    'G5'),
    4: (['calzado'],    'Calzado',                   None),
    5: (['auricular'],  'Electrónica',               'Electronica'),
    6: (['van cleef'],  'Van Cleef & Arpels',        'van cleef'),
}

# Ruta del scraper relativa a la raíz del repo
_SCRAPER = 'scrape_modaverse_final.py'


def category_for_weekday(weekday: int):
    """Retorna (keywords, label) para el día dado (0=lunes). None si no hay entrada."""
    entry = _SCHEDULE.get(weekday)
    if entry is None:
        return None
    keywords, label = entry[0], entry[1]
    return (keywords, label)


class Command(BaseCommand):
    help = 'Scrape + sincroniza la categoría del día (crontab semanal).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--day', type=int, default=None,
            help='Forzar día de semana (0=lunes … 6=domingo). Por defecto: hoy.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué correría sin ejecutar nada.',
        )
        parser.add_argument(
            '--no-scrape', action='store_true',
            help='Saltar el scrape y solo ejecutar load_productos con el JSON existente.',
        )
        parser.add_argument(
            '--no-browser', action='store_true', default=True,
            help='Pasar --no-browser al scraper (httpx puro, sin Playwright). Activo por defecto.',
        )
        parser.add_argument(
            '--browser', dest='no_browser', action='store_false',
            help='Usar scrapling/Playwright en el scrape (solo si el entorno lo soporta).',
        )

    def handle(self, *args, **options):
        weekday = options['day'] if options['day'] is not None else date.today().weekday()
        entry = _SCHEDULE.get(weekday)

        if entry is None:
            self.stdout.write(f'Sin categoría programada para día {weekday}.')
            return

        keywords, label, scraper_kw = entry
        self.stdout.write(
            f'[auto_sync_catalog] día {weekday} → {label}'
            + (f'  |  scraper: --category {scraper_kw}' if scraper_kw else '  |  sin scrape (calzado)')
        )

        if options['dry_run']:
            self.stdout.write('  --dry-run: nada ejecutado.')
            return

        # ── Paso 1: scrape ────────────────────────────────────────────────────
        if scraper_kw and not options['no_scrape']:
            repo_root = Path(__file__).resolve().parents[4]
            scraper   = repo_root / _SCRAPER
            if not scraper.exists():
                self.stdout.write(self.style.WARNING(f'  ⚠ Scraper no encontrado: {scraper}'))
            else:
                self.stdout.write(f'  ► Scrapeando "{scraper_kw}"...')
                cmd = [sys.executable, '-X', 'utf8', str(scraper), '--category', scraper_kw]
                if options['no_browser']:
                    cmd.append('--no-browser')
                result = subprocess.run(
                    cmd,
                    capture_output=False,   # deja que stdout/stderr fluyan al log
                    cwd=str(repo_root),
                )
                if result.returncode != 0:
                    self.stdout.write(
                        self.style.WARNING(f'  ⚠ Scraper terminó con código {result.returncode} — continuando con JSON existente.')
                    )
                else:
                    self.stdout.write(f'  ✓ Scrape completado.')

        # ── Paso 2: load_productos ────────────────────────────────────────────
        self.stdout.write(f'  ► Cargando productos ({label})...')
        call_command('load_productos', category=keywords, verbosity=options['verbosity'])
        self.stdout.write(self.style.SUCCESS(f'  ✓ {label} sincronizada.'))
