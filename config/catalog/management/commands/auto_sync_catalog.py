"""
Sincronización del catálogo de Modaverse. Dos modos, dos líneas de crontab (root,
servidor en UTC; México = UTC−6 fijo). Comparten el candado para no escribir el
JSON a la vez; si el otro no suelta en 30 min, la corrida se salta.

  # Stock de gorras: 18:00 y 06:00 México
  0 0,12 * * *  cd /root/app && flock -w 1800 /tmp/ryal_catalog.lock env PYTHONUTF8=1 venv/bin/python config/manage.py auto_sync_catalog --stock-only >> /var/log/ryal_stock.log 2>&1
  # Gorra + categoría rotativa, cada 2 días a las 20:00 México
  0 2 */2 * *   cd /root/app && flock -w 1800 /tmp/ryal_catalog.lock env PYTHONUTF8=1 venv/bin/python config/manage.py auto_sync_catalog >> /var/log/ryal_sync.log 2>&1
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Gorra es la categoría de mayor volumen/rotación del negocio — se sincroniza en
# TODAS las corridas del cron (cada 2 días), sin esperar su turno en el schedule
# rotativo. Decisión de Bryan 2026-07-13.
# (keywords_load, label, scraper_kw, images_hint)
_ALWAYS = (['gorra'], 'Gorra', 'gorra', 'gorras')

# Slot secuencial de 2 días (0-7) para las 8 categorías restantes — NO es día de
# la semana. Con el cron corriendo cada 2 días, cada ejecución cae en el
# siguiente slot; el ciclo completo tarda 16 días. slot = (fecha_ordinal // 2) % 8
# — ver slot_for_date(). Gorra queda fuera de esta rotación (ver _ALWAYS arriba).
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
#
# 2026-09-16: el Paso 4 (import_images --fill-gaps) salió del pipeline por decisión
# de Bryan: no se completan galerías de productos existentes. images_hint se conserva
# como referencia para correr import_images a mano.
_SCHEDULE = {
    0: (['deportiva'],      'Camisetas deportivas',        'deportiva',    'deportivas'),
    1: (['1:1'],            'Camisetas/Sudaderas 1:1',     '1:1',          '1a1'),
    2: (['g5'],             'Camisetas/Sudaderas G5',      'G5',           'g5'),
    3: (['calzado'],        'Calzado',                     None,           None),
    4: (['van cleef'],      'Van Cleef & Arpels',          'van cleef',    'van-cleef'),
    5: (['reloj'],          'Reloj',                       'reloj',        'reloj'),
    6: (['chrome hearts'],  'Joyería Chrome Hearts',       'chrome hearts', 'joyeria'),
    7: (['bolsos'],         'Bolsos de lujo de gama alta', 'bolsos',       'bolsos'),
}

# Ruta del scraper relativa a la raíz del repo
_SCRAPER = 'scrape_modaverse_final.py'

# Categorías con stock sincronizado 2 veces al día (--stock-only). Extender a más
# categorías exige medir antes cuánto tarda su scrape.
_STOCK_SYNC = ['gorra']


def slot_for_date(d: date) -> int:
    """Slot secuencial (0-7) para la fecha dada, avanzando 1 slot cada 2 días."""
    return (d.toordinal() // 2) % 8


def category_for_slot(slot: int):
    """Retorna (keywords, label) para el slot rotativo dado (0-7). None si no hay entrada."""
    entry = _SCHEDULE.get(slot)
    if entry is None:
        return None
    keywords, label = entry[0], entry[1]
    return (keywords, label)


class Command(BaseCommand):
    help = (
        'Sincroniza Gorra (siempre) + la categoría rotativa del slot '
        '(crontab cada 2 días, ciclo rotativo de 16 días).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--day', type=int, default=None,
            help='Forzar slot rotativo (0-7) en vez de calcularlo de la fecha de hoy. Por defecto: slot de hoy.',
        )
        parser.add_argument(
            '--skip-gorra', action='store_true',
            help='Omitir la sincronización siempre-activa de Gorra (solo la categoría rotativa).',
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
        parser.add_argument(
            '--stock-only', action='store_true',
            help='Solo stock de _STOCK_SYNC: scrape → reconcile_catalog → sync_stock_modaverse. '
                 'Sin carga de productos ni imágenes.',
        )

    def handle(self, *args, **options):
        if options['stock_only']:
            self._stock_only(options)
            return

        to_run = []

        if not options['skip_gorra']:
            to_run.append(_ALWAYS)

        slot = options['day'] if options['day'] is not None else slot_for_date(date.today())
        entry = _SCHEDULE.get(slot)
        if entry is None:
            self.stdout.write(f'Sin categoría rotativa programada para slot {slot}.')
        else:
            to_run.append(entry)

        if not to_run:
            return

        if options['dry_run']:
            for keywords, label, scraper_kw, images_hint in to_run:
                self.stdout.write(
                    f'[dry-run] {label}'
                    + (f'  |  scraper: --category {scraper_kw}' if scraper_kw else '  |  sin scrape')
                )
            return

        for keywords, label, scraper_kw, images_hint in to_run:
            self._sync_one(keywords, label, scraper_kw, images_hint, options)

    def _scrape(self, scraper_kw, options):
        """Corre el scraper de una categoría. True si terminó con código 0."""
        repo_root = Path(__file__).resolve().parents[4]
        scraper   = repo_root / _SCRAPER
        if not scraper.exists():
            self.stdout.write(self.style.WARNING(f'  ⚠ Scraper no encontrado: {scraper}'))
            return False
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
            self.stdout.write(self.style.WARNING(f'  ⚠ Scraper terminó con código {result.returncode}.'))
            return False
        self.stdout.write('  ✓ Scrape completado.')
        return True

    def _stock_only(self, options):
        for kw in _STOCK_SYNC:
            if options['dry_run']:
                self.stdout.write(f'[dry-run] {kw}  |  scrape → reconcile_catalog → sync_stock_modaverse')
                continue
            self.stdout.write(f'[auto_sync_catalog --stock-only] {kw}')
            if not options['no_scrape'] and not self._scrape(kw, options):
                self.stdout.write(self.style.ERROR(
                    f'  ✗ El scrape de "{kw}" falló: no se reconcilia ni se sincroniza stock con el JSON viejo.'
                ))
                continue
            call_command('reconcile_catalog', category=[kw], verbosity=options['verbosity'])
            call_command('sync_stock_modaverse', category=[kw], verbosity=options['verbosity'])
            self.stdout.write(self.style.SUCCESS(f'  ✓ Stock de {kw} sincronizado.'))

    def _sync_one(self, keywords, label, scraper_kw, images_hint, options):
        self.stdout.write(
            f'[auto_sync_catalog] {label}'
            + (f'  |  scraper: --category {scraper_kw}' if scraper_kw else '  |  sin scrape (calzado)')
        )

        # ── Paso 1: scrape ────────────────────────────────────────────────────
        scrape_fallo = False
        if scraper_kw and not options['no_scrape']:
            scrape_fallo = not self._scrape(scraper_kw, options)
            if scrape_fallo:
                self.stdout.write(self.style.WARNING('  ⚠ Continuando con el JSON existente (sin sincronizar stock).'))

        # ── Paso 2: load_productos ────────────────────────────────────────────
        # La reconciliación (baja de productos eliminados) se ejecuta dentro
        # de load_productos al recibir --category. No se repite aquí.
        self.stdout.write(f'  ► Cargando productos ({label})...')
        call_command('load_productos', category=keywords, verbosity=options['verbosity'])

        # ── Paso 3: imágenes de productos nuevos pendientes ───────────────────
        self.stdout.write(f'  ► Descargando imágenes de pendientes...')
        call_command('import_pending_images', workers=4, verbosity=options['verbosity'])

        # ── Paso 4: stock (solo Modaverse, y nunca contra un JSON viejo) ──────
        if scraper_kw and not scrape_fallo:
            self.stdout.write(f'  ► Sincronizando stock ({label})...')
            call_command('sync_stock_modaverse', category=keywords, verbosity=options['verbosity'])

        self.stdout.write(self.style.SUCCESS(f'  ✓ {label} sincronizada.'))
