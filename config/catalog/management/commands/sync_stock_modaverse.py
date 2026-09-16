"""Management command: sync_stock_modaverse

Lleva el stock de scraped_modaverse.json a `Product.status`:
- `out_of_stock` en el JSON: `available` pasa a `sold_out` (etiqueta "Agotado",
  el carrito lo rechaza). Solo si el registro es sano: una carcasa de la API no
  prueba que esté agotado.
- `available` en el JSON: `sold_out` vuelve a `available`.

`unlaunched` y los pids ausentes del JSON no se tocan: ocultarlos es trabajo de
reconcile_catalog. `coming_soon` nunca se toca.

Lo corre `auto_sync_catalog` (ver su docstring para el cron).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.management.commands.reconcile_catalog import modaverse_scope
from catalog.modaverse import category_filter_ids, pid_from_url, read_modaverse_json, registro_sano
from catalog.models import Product

MAX_SOLD_OUT_PCT = 5
_LOTE = 500


class Command(BaseCommand):
    help = 'Sincroniza Product.status (Disponible/Agotado) con el stock de scraped_modaverse.json.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--category', nargs='+', metavar='KEYWORD', required=True,
            help='Categorías a sincronizar (parcial, case-insensitive), mismo criterio que reconcile_catalog.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Calcula y reporta sin escribir.')
        parser.add_argument(
            '--force', action='store_true',
            help=f'Aplica aunque los nuevos agotados superen el {MAX_SOLD_OUT_PCT}%% del alcance.',
        )

    def handle(self, *args, **options):
        keywords = options['category']
        data = read_modaverse_json()
        if data is None:
            self.stderr.write(self.style.ERROR('No se encontró scraped_modaverse.json'))
            return

        filter_ids = category_filter_ids(data.get('categories', []), keywords)
        if not filter_ids:
            self.stderr.write(self.style.WARNING(f'Ninguna categoría coincide con: {keywords}'))
            return

        json_scope = {
            p['sku']: p for p in data.get('products', [])
            if p.get('sku') and p.get('category_id') in filter_ids
        }
        if not json_scope:
            self.stderr.write(self.style.ERROR(
                'Zero-guard: JSON scope vacío (0 productos). Posible scrape fallido. '
                'No se aplicó ningún cambio.'
            ))
            return

        a_agotado, a_disponible, carcasas = [], [], []
        candidatos = (
            modaverse_scope(keywords)
            .filter(status__in=['available', 'sold_out'])
            .only('pk', 'sku', 'name', 'status', 'supplier_url')
        )
        for prod in candidatos:
            rec = json_scope.get(pid_from_url(prod.supplier_url))
            if rec is None:
                continue
            if prod.status == 'available' and rec.get('status') == 'out_of_stock':
                (a_agotado if registro_sano(rec) else carcasas).append(prod)
            elif prod.status == 'sold_out' and rec.get('status') == 'available':
                a_disponible.append(prod)

        if a_agotado and not options['force']:
            activos = modaverse_scope(keywords).filter(is_active=True).count()
            pct = len(a_agotado) / activos * 100 if activos else 100.0
            if pct > MAX_SOLD_OUT_PCT:
                self.stderr.write(self.style.ERROR(
                    f'Umbral superado: {len(a_agotado)}/{activos} = {pct:.1f}% > {MAX_SOLD_OUT_PCT}% '
                    'pasarían a agotado. No se aplicó ningún cambio. Usa --force para proceder.'
                ))
                self._report('[abortado] ', a_agotado, a_disponible, carcasas)
                return

        if options['dry_run']:
            self._report('[dry-run] ', a_agotado, a_disponible, carcasas)
            return

        self._update([p.pk for p in a_agotado], 'sold_out')
        self._update([p.pk for p in a_disponible], 'available')
        self._report('', a_agotado, a_disponible, carcasas)

    def _update(self, pks, status):
        # update() no dispara auto_now: updated_at se pone a mano para que el
        # cambio de status quede fechado.
        ahora = timezone.now()
        for i in range(0, len(pks), _LOTE):
            Product.objects.filter(pk__in=pks[i:i + _LOTE]).update(status=status, updated_at=ahora)

    def _report(self, prefijo, a_agotado, a_disponible, carcasas):
        self.stdout.write(
            f'{prefijo}a_agotado={len(a_agotado)} · '
            f'a_disponible={len(a_disponible)} · '
            f'carcasas_ignoradas={len(carcasas)}'
        )
        for titulo, prods in (
            ('Pasan a agotado', a_agotado),
            ('Vuelven a disponible', a_disponible),
            ('Carcasas ignoradas (registro incompleto, no se marcan agotadas)', carcasas),
        ):
            if prods:
                self.stdout.write(f'  {titulo} (primeros {min(10, len(prods))}):')
                for p in prods[:10]:
                    self.stdout.write(f'    {p.sku} — {p.name}')
