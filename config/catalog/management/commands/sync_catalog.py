"""
Sincroniza el catálogo de modaverse completo:
  1. Corre scrape_modaverse_final.py → genera scraped_modaverse.json
  2. Corre load_productos → carga nuevos productos a BD
  3. Corre repair_images → corrige paths de imágenes rotas

Calzado (yupoo_pf) se carga por separado:
    python manage.py load_productos --only calzado --no-images
    python manage.py download_yupoo_images

Uso:
    python manage.py sync_catalog                   # scraping + carga + repair
    python manage.py sync_catalog --skip-scraping   # usa JSON existente
    python manage.py sync_catalog --no-images       # sin descargar imágenes
    python manage.py sync_catalog --skip-repair     # sin repair_images al final
"""
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

PROJECT_ROOT      = Path(__file__).resolve().parents[4]
SCRAPER_MODAVERSE = PROJECT_ROOT / 'scrape_modaverse_final.py'


class Command(BaseCommand):
    help = 'Scraping + carga de productos modaverse en un solo comando'

    def add_arguments(self, parser):
        parser.add_argument('--skip-scraping', action='store_true',
                            help='Omite el scraping; usa JSON existente')
        parser.add_argument('--no-images', action='store_true',
                            help='No descarga imágenes nuevas')
        parser.add_argument('--skip-repair', action='store_true',
                            help='No corre repair_images al final')

    def handle(self, *args, **options):
        skip_scrap   = options['skip_scraping']
        no_images    = options['no_images']
        skip_repair  = options['skip_repair']
        img_kwargs   = {'no_images': True} if no_images else {}

        self.stdout.write(self.style.SUCCESS('╔══════════════════════════════╗'))
        self.stdout.write(self.style.SUCCESS('║   RYAL — Sync Catálogo       ║'))
        self.stdout.write(self.style.SUCCESS('╚══════════════════════════════╝\n'))

        # ── Paso 1: Scraping ──────────────────────────────────────────────────
        if not skip_scrap:
            self._run_scraper(SCRAPER_MODAVERSE, 'modaverse (multi-categoría)', timeout=3600)
        else:
            self.stdout.write('⏭  Scraping omitido (--skip-scraping)')

        # ── Paso 2: Cargar productos ──────────────────────────────────────────
        self.stdout.write('\n── Cargando productos a BD ──')
        call_command('load_productos', only='modaverse', **img_kwargs)

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
