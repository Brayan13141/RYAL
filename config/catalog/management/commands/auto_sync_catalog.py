"""
Scrape + carga la categoría del slot. Crontab cada 2 días en el servidor:
  0 2 */2 * * cd ~/WEB_RYAL && PYTHONUTF8=1 venv/bin/python config/manage.py auto_sync_catalog >> /var/log/ryal_sync.log 2>&1
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Slot secuencial de 2 días (0-8) — NO es día de la semana. Con el cron corriendo
# cada 2 días, cada ejecución cae en el siguiente slot; el ciclo completo de las
# 9 categorías tarda 18 días. slot = (fecha_ordinal // 2) % 9 — ver slot_for_date().
# (keywords_load, label, scraper_kw, images_hint)
# scraper_kw=None → no hay scraper para esa categoría (calzado usa yupoo)
# images_hint → valor de --only para import_images (galería completa, no solo
# la portada); None → no aplica (calzado usa download_yupoo_images aparte).
#
# 2026-07-13: el proveedor reestructuró su árbol de categorías (9 top-level en
# vez de 7). "Electrónica/auricular" ya no existe en modaverse.vip (retirada,
# is_active=False en BD) — se quitó del schedule. Se agregaron 3 categorías
# nuevas del proveedor: Reloj, Joyería Chrome Hearts, Bolsos de lujo de gama alta.
#
# 2026-07-13 (cont.): 85% del catálogo activo tenía exactamente 1 foto — import_images
# existía pero nunca se había sumado al pipeline automático. Se agrega como Paso 4
# (--fill-gaps, completa desde 1 hasta lo que el JSON tenga disponible por producto).
_SCHEDULE = {
    0: (['gorra'],          'Gorra',                       'gorra',        'gorras'),
    1: (['deportiva'],      'Camisetas deportivas',        'deportiva',    'deportivas'),
    2: (['1:1'],            'Camisetas/Sudaderas 1:1',     '1:1',          '1a1'),
    3: (['g5'],             'Camisetas/Sudaderas G5',      'G5',           'g5'),
    4: (['calzado'],        'Calzado',                     None,           None),
    5: (['van cleef'],      'Van Cleef & Arpels',          'van cleef',    'van-cleef'),
    6: (['reloj'],          'Reloj',                       'reloj',        'reloj'),
    7: (['chrome hearts'],  'Joyería Chrome Hearts',       'chrome hearts', 'joyeria'),
    8: (['bolsos'],         'Bolsos de lujo de gama alta', 'bolsos',       'bolsos'),
}

# Ruta del scraper relativa a la raíz del repo
_SCRAPER = 'scrape_modaverse_final.py'


def slot_for_date(d: date) -> int:
    """Slot secuencial (0-8) para la fecha dada, avanzando 1 slot cada 2 días."""
    return (d.toordinal() // 2) % 9


def category_for_slot(slot: int):
    """Retorna (keywords, label) para el slot dado (0-8). None si no hay entrada."""
    entry = _SCHEDULE.get(slot)
    if entry is None:
        return None
    keywords, label = entry[0], entry[1]
    return (keywords, label)


class Command(BaseCommand):
    help = 'Scrape + sincroniza la categoría del slot (crontab cada 2 días, ciclo de 18 días).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--day', type=int, default=None,
            help='Forzar slot (0-8) en vez de calcularlo de la fecha de hoy. Por defecto: slot de hoy.',
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
        slot = options['day'] if options['day'] is not None else slot_for_date(date.today())
        entry = _SCHEDULE.get(slot)

        if entry is None:
            self.stdout.write(f'Sin categoría programada para slot {slot}.')
            return

        keywords, label, scraper_kw, images_hint = entry
        self.stdout.write(
            f'[auto_sync_catalog] slot {slot} → {label}'
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

        # ── Paso 3: imágenes de productos nuevos pendientes ───────────────────
        self.stdout.write(f'  ► Descargando imágenes de pendientes...')
        call_command('import_pending_images', workers=4, verbosity=options['verbosity'])

        # La reconciliación (baja de productos eliminados) se ejecuta dentro
        # de load_productos al recibir --category. No se repite aquí.

        # ── Paso 4: galería completa de productos ya aprobados ────────────────
        # import_pending_images solo baja 1 foto de portada por PendingProduct.
        # Aquí se completa la galería de los Product ya aprobados de esta
        # categoría con --fill-gaps (hasta lo que el JSON tenga disponible).
        if images_hint:
            self.stdout.write(f'  ► Completando galería de productos ({label})...')
            call_command(
                'import_images', only=images_hint, fill_gaps=True,
                verbosity=options['verbosity'],
            )

        self.stdout.write(self.style.SUCCESS(f'  ✓ {label} sincronizada.'))
