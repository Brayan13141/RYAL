"""Management command: revive_pending

Devuelve a la cola (`status='pending'`) los PendingProduct que se rechazaron
porque el scraper los había guardado rotos, y que en el JSON de hoy ya están
sanos.

Por qué hace falta un comando y no basta con arreglar el JSON: `load_productos`
mete en `existing_urls` **todos** los pendientes sin mirar su estado, así que una
fila 'rejected' vuelve al producto invisible para siempre — nunca se re-encola
aunque sus datos ya sean correctos.

Caso que lo motivó (2026-08-17): entradas con `category_id` nulo caían en
'General' sin imágenes y con precio 0; se rechazaron a mano el 2026-07-04 por
inservibles. Eran 239 productos de `Gorro de Pico AA10` y `Sombrero de ala plana
AA4`, víctimas del bug de fusión del scraper, no productos malos.

Solo revive lo que HOY está sano: con categoría real, precio > 0 e imágenes. Un
rechazo sobre algo que sigue roto fue una decisión correcta y se respeta.
"""
from django.core.management.base import BaseCommand

from catalog.models import Category, PendingProduct, Product
from catalog.modaverse import (
    category_filter_ids,
    pid_from_url,
    precio_proveedor,
    read_modaverse_json,
)


class Command(BaseCommand):
    help = (
        'Devuelve a pending los PendingProduct rechazados que ya están sanos en '
        'scraped_modaverse.json (categoría real + precio + imágenes).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--category', nargs='+', metavar='KEYWORD',
            help='Limitar a una categoría por keyword, igual que el scraper. '
                 'Sin esto, revisa todo el JSON.',
        )
        parser.add_argument(
            '--apply', action='store_true', default=False,
            help='Ejecuta los cambios. Sin este flag solo reporta (dry-run).',
        )
        parser.add_argument(
            '--limit-list', type=int, default=20,
            help='Cuántos productos listar en el detalle (default 20).',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        data = read_modaverse_json()
        if data is None:
            self.stderr.write(self.style.ERROR('No se encontró scraped_modaverse.json'))
            return

        filter_ids = None
        if options.get('category'):
            filter_ids = category_filter_ids(data.get('categories', []), options['category'])
            if not filter_ids:
                self.stderr.write(self.style.ERROR(
                    f"Ninguna categoría coincide con: {options['category']}"))
                return

        # ── Entradas del JSON que hoy están sanas ─────────────────────────────
        sanos = {}
        for p in data.get('products', []):
            cat_id = p.get('category_id')
            if not cat_id:
                continue
            if filter_ids is not None and cat_id not in filter_ids:
                continue
            if not p.get('images'):
                continue
            if precio_proveedor(p) is None:
                continue
            pid = p.get('sku')
            if pid:
                sanos[pid] = p

        self.stdout.write(f'JSON: {len(sanos)} productos sanos en el alcance')

        # pids que ya están en el catálogo: revivirlos los duplicaría
        pids_en_catalogo = set()
        for url in Product.objects.exclude(supplier_url='').values_list('supplier_url', flat=True):
            pid = pid_from_url(url)
            if pid:
                pids_en_catalogo.add(pid)

        # ── Candidatos ────────────────────────────────────────────────────────
        a_revivir, ya_en_catalogo, sin_categoria = [], 0, []
        for pp in PendingProduct.objects.filter(status='rejected'):
            pid = pid_from_url(pp.supplier_url or '')
            entrada = sanos.get(pid)
            if entrada is None:
                continue
            if pid in pids_en_catalogo:
                ya_en_catalogo += 1
                continue
            cat = self._resolver_categoria(entrada.get('category'))
            if cat is None:
                sin_categoria.append((pp, entrada.get('category')))
                continue
            a_revivir.append((pp, entrada, cat))

        self.stdout.write(f'\n── A revivir: {len(a_revivir)} ──')
        for pp, entrada, cat in a_revivir[:options['limit_list']]:
            actual = pp.category.name if pp.category else '(ninguna)'
            self.stdout.write(
                f'  {pp.display_name[:32]:<32} {actual} → {cat.name} '
                f'· ${precio_proveedor(entrada)}'
            )
        if len(a_revivir) > options['limit_list']:
            self.stdout.write(f'  … y {len(a_revivir) - options["limit_list"]} más')

        if ya_en_catalogo:
            self.stdout.write(f'\n{ya_en_catalogo} omitidos: ya existen en el catálogo')
        if sin_categoria:
            self.stdout.write(self.style.WARNING(
                f'\n{len(sin_categoria)} omitidos: su categoría no existe en BD '
                f'(ej. {sin_categoria[0][1]!r}) — corre load_productos primero'
            ))

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f'\nDRY-RUN: no se escribió nada. Usa --apply para revivir '
                f'{len(a_revivir)} productos.'
            ))
            return

        for pp, entrada, cat in a_revivir:
            pp.status = 'pending'
            pp.category = cat
            pp.base_price = precio_proveedor(entrada)
            pp.reviewed_at = None
            raw = dict(pp.raw_data or {})
            imgs = entrada.get('images') or []
            if imgs:
                raw['image_url'] = imgs[0]
            pp.raw_data = raw
            pp.save(update_fields=[
                'status', 'category', 'base_price', 'reviewed_at', 'raw_data'])

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(a_revivir)} productos devueltos a la cola de pendientes.'
        ))

    def _resolver_categoria(self, nombre):
        """Category por nombre. Prefiere la subcategoría (con parent) cuando el
        mismo nombre existe suelto y colgando de un padre."""
        if not nombre:
            return None
        qs = Category.objects.filter(name=nombre)
        return qs.filter(parent__isnull=False).first() or qs.first()
