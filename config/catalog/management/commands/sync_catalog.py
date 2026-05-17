"""
Sincroniza el catálogo completo:
  1. Corre scrape_modaverse_v5.py → genera scraped_modaverse.json (gorras, camisetas,
     jerseys, sudaderas, airpods)
  2. Corre load_productos → carga nuevos productos a BD
  3. Corre repair_images → corrige paths de imágenes rotas

Yupoo/tenis está deshabilitado por defecto — usar --only tenis cuando sea necesario.

Uso:
    python manage.py sync_catalog                   # solo modaverse (default)
    python manage.py sync_catalog --only tenis      # solo yupoo/tenis
    python manage.py sync_catalog --only all        # modaverse + tenis
    python manage.py sync_catalog --skip-scraping   # carga desde JSONs existentes
    python manage.py sync_catalog --no-images       # sin descargar imágenes
"""
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

PROJECT_ROOT      = Path(__file__).resolve().parents[4]
SCRAPER_MODAVERSE = PROJECT_ROOT / 'scrape_modaverse_final.py'
SCRAPER_YUPOO     = PROJECT_ROOT / 'scrape_yupoo.py'


class Command(BaseCommand):
    help = 'Scraping + carga de productos en un solo comando'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only', choices=['modaverse', 'tenis', 'all'], default='modaverse',
            help='Qué sincronizar (default: modaverse — sin yupoo)'
        )
        parser.add_argument('--skip-scraping', action='store_true',
                            help='Omite el scraping; usa JSONs existentes')
        parser.add_argument('--no-images', action='store_true',
                            help='No descarga imágenes nuevas')
        parser.add_argument('--skip-repair', action='store_true',
                            help='No corre repair_images al final')

    def handle(self, *args, **options):
        only         = options['only']
        skip_scrap   = options['skip_scraping']
        no_images    = options['no_images']
        skip_repair  = options['skip_repair']
        img_kwargs   = {'no_images': True} if no_images else {}

        self.stdout.write(self.style.SUCCESS('╔══════════════════════════════╗'))
        self.stdout.write(self.style.SUCCESS('║   RYAL — Sync Catálogo v5    ║'))
        self.stdout.write(self.style.SUCCESS('╚══════════════════════════════╝\n'))

        # ── Paso 1: Scraping ──────────────────────────────────────────────────
        if not skip_scrap:
            if only in ('modaverse', 'all'):
                self._run_scraper(SCRAPER_MODAVERSE, 'modaverse (multi-categoría)', timeout=3600)
            if only in ('tenis', 'all'):
                self._run_scraper(SCRAPER_YUPOO, 'yupoo (tenis)', timeout=300)
        else:
            self.stdout.write('⏭  Scraping omitido (--skip-scraping)')

        # ── Paso 2: Cargar productos ──────────────────────────────────────────
        self.stdout.write('\n── Cargando productos a BD ──')
        if only in ('modaverse', 'all'):
            call_command('load_productos', only='modaverse', **img_kwargs)
        if only in ('tenis', 'all'):
            call_command('load_productos', only='tenis', **img_kwargs)

        # ── Paso 3: Reparar imágenes ──────────────────────────────────────────
        if not skip_repair:
            self.stdout.write('\n── Reparando imágenes ──')
            call_command('repair_images')

        self.stdout.write(self.style.SUCCESS('\n✅ Sincronización completada.'))

    def _run_scraper(self, script_path: Path, label: str, timeout: int = 300):
        self.stdout.write(f'\n── Scraping {label} ──')
        if not script_path.exists():
            self.stdout.write(self.style.WARNING(f'  ⚠ Script no encontrado: {script_path}'))
            return
        self.stdout.write(f'  Ejecutando: {script_path.name}')
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=False,
                text=True,
                cwd=str(PROJECT_ROOT),
                timeout=timeout,
            )
            if result.returncode != 0:
                self.stdout.write(self.style.ERROR(f'  ✗ Código {result.returncode}'))
            else:
                self.stdout.write(f'  ✓ {label} scrapeado')
        except subprocess.TimeoutExpired:
            self.stdout.write(self.style.ERROR(f'  ✗ Timeout ({timeout}s)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ✗ {e}'))
