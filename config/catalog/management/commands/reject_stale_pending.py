"""Management command: reject_stale_pending

Rechaza PendingProduct (status='pending', cover_image='') cuyo pid ya no existe
en scraped_modaverse.json (yn_launch='1').

Solo afecta productos sin imagen de portada; los que tienen imagen se ignoran.
"""
from django.core.management.base import BaseCommand

from catalog.models import PendingProduct
from catalog.modaverse import pid_from_url, read_modaverse_json


class Command(BaseCommand):
    help = (
        'Rechaza PendingProduct (pending + sin cover_image) cuyo pid ya no existe '
        'en scraped_modaverse.json (yn_launch="1").'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=True,
            help='Muestra el plan sin escribir nada (default).'
        )
        parser.add_argument(
            '--apply', action='store_true', default=False,
            help='Ejecuta los rechazos de verdad (requiere --no-dry-run).'
        )

    def handle(self, *args, **options):
        dry_run = not options['apply']  # default True unless --apply passed
        if dry_run and options['apply']:
            self.stderr.write(self.style.ERROR(
                'No uses --dry-run y --apply juntos. Usa solo --apply para ejecutar.'
            ))
            return

        # ── 1. Cargar live_pids desde scraped_modaverse.json ──────────────────
        data = read_modaverse_json()
        if data is None:
            self.stderr.write(self.style.ERROR('No se encontró scraped_modaverse.json'))
            return

        products = data.get('products', [])
        live_pids = {
            p['sku'] for p in products
            if p.get('sku') and p.get('yn_launch', '1') == '1'
        }

        if not live_pids:
            self.stderr.write(self.style.WARNING(
                'live_pids está vacío (scraped_modaverse.json vacío o yn_launch=0). '
                'Nada que comparar.'
            ))
            return

        # ── 2. Candidatos: PendingProduct pending + sin cover_image ────────────
        candidates_qs = PendingProduct.objects.filter(
            status='pending',
            cover_image=''
        ).exclude(supplier_url='')

        total_candidates = candidates_qs.count()
        if total_candidates == 0:
            self.stdout.write('No hay PendingProduct pending sin cover_image.')
            return

        # ── 3. Filtrar candidatos cuyo pid NO está en live_pids ───────────────
        stale_candidates = []
        for pp in candidates_qs:
            pid = pid_from_url(pp.supplier_url)
            if pid and pid not in live_pids:
                stale_candidates.append((pp, pid))

        stale_count = len(stale_candidates)
        self.stdout.write(
            f'Total pending sin cover_image: {total_candidates}\n'
            f'PIDs vivos en Modaverse (yn_launch=1): {len(live_pids)}\n'
            f'Candidatos a rechazar (pid fuera de live_pids): {stale_count}'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING('[dry-run] No se escriben cambios.'))
            if stale_candidates:
                self.stdout.write('Primeros 10 candidatos:')
                for pp, pid in stale_candidates[:10]:
                    self.stdout.write(f'  [{pp.pk}] pid={pid} url={pp.supplier_url[:80]}')
                if stale_count > 10:
                    self.stdout.write(f'  … y {stale_count - 10} más')
            return

        # ── 4. Aplicar rechazos ───────────────────────────────────────────────
        rejected = 0
        for pp, pid in stale_candidates:
            pp.reject(notes='ya no disponible en Modaverse (sin imagen)')
            rejected += 1

        self.stdout.write(self.style.SUCCESS(
            f'Rechazados: {rejected}/{stale_count} candidatos.'
        ))