"""
Repara el daño de la colisión de SKU al aprobar pendientes.

Causa (ya corregida en load_productos/_next_sku_index y PendingProduct.approve):
_next_sku_index solo miraba Product, así que dos corridas de load_productos
antes de aprobar reservaban el MISMO SKU para dos productos distintos. Al
aprobar el segundo, approve() hacía get_or_create(sku=...), encontraba el
producto del primero y le sobrescribía nombre/precio — sin crear nunca el
producto nuevo.

Este comando repara las dos consecuencias:
  a) pendientes marcados 'approved' que nunca llegaron al catálogo → los crea
     con un SKU libre (reusando PendingProduct.approve, ya corregido).
  b) productos cuyo nombre/precio quedaron pisados por un pendiente ajeno →
     los restaura desde SU PROPIO pendiente (el que comparte supplier_url).

Uso:
    python manage.py repair_sku_colisiones --dry-run   # solo reporta
    python manage.py repair_sku_colisiones
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from catalog.modaverse import pid_from_url, read_modaverse_json
from catalog.models import Category, PendingProduct, Product


class Command(BaseCommand):
    help = 'Repara productos perdidos/pisados por colisión de SKU al aprobar'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo reporta lo que haría, sin escribir')

    def handle(self, *args, **options):
        dry = options['dry_run']
        if dry:
            self.stdout.write(self.style.WARNING('DRY-RUN — no se escribe nada\n'))

        recuperados = self._recuperar_perdidos(dry)
        restaurados = self._restaurar_pisados(dry)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{recuperados} productos recuperados · {restaurados} productos restaurados'
        ))

    # ── a) aprobados que nunca llegaron al catálogo ───────────────────────────

    def _recuperar_perdidos(self, dry):
        # Reconciliar por productId, no por el string de la URL: el catálogo
        # guarda #/proinfo/{pid} y #/product/{cat}?pid={pid} para el mismo
        # producto, y comparar strings crearía duplicados.
        pids_en_catalogo = {
            pid for pid in (
                pid_from_url(u) for u in
                Product.objects.exclude(supplier_url='')
                               .values_list('supplier_url', flat=True)
            ) if pid
        }
        urls_en_catalogo = set(
            Product.objects.exclude(supplier_url='')
                           .values_list('supplier_url', flat=True)
        )

        huerfanos = []
        for pp in PendingProduct.objects.filter(status='approved').select_related('category'):
            if not pp.supplier_url or pp.supplier_url in urls_en_catalogo:
                continue
            pid = pid_from_url(pp.supplier_url)
            if pid and pid in pids_en_catalogo:
                continue
            huerfanos.append(pp)
        self.stdout.write(f'── Aprobados sin producto en catálogo: {len(huerfanos)} ──')

        sin_categoria = [pp for pp in huerfanos if pp.category is None]
        resueltas = self._categorias_desde_json(sin_categoria) if sin_categoria else {}

        n = omitidos = 0
        for pp in huerfanos:
            if pp.category is None:
                destino = resueltas.get(pp.pk)
                if destino is None:
                    omitidos += 1
                    continue
                pp.category = destino
                if not dry:
                    pp.save(update_fields=['category'])
            if dry:
                self.stdout.write(f'  + {pp.display_name[:45]}')
                n += 1
                continue
            with transaction.atomic():
                product = pp.approve()
            self.stdout.write(f'  + {product.sku} — {product.name[:40]}')
            n += 1

        if omitidos:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ {omitidos} omitidos: sin categoría y el JSON no la ubica '
                f'en el árbol (categoría eliminada por el proveedor)'
            ))
        return n

    def _categorias_desde_json(self, pendientes):
        """Resuelve la Category de pendientes sin categoría usando el JSON.

        Solo resuelve cuando el category_id del producto está en el árbol del
        JSON y ya existe esa Category en el catálogo. Si el proveedor eliminó la
        categoría, no se inventa un destino: se deja para decisión manual.
        """
        data = read_modaverse_json()
        if not data:
            self.stdout.write(self.style.WARNING(
                '  ⚠ Sin scraped_modaverse.json — no se puede resolver categorías'
            ))
            return {}

        # category_id del árbol → nombre de la subcategoría (o de la raíz)
        nombre_por_id = {}
        for cat in data.get('categories', []):
            nombre_por_id[cat['id']] = cat.get('name_es') or cat.get('name_zh') or ''
            for sub in cat.get('subcategories', []):
                nombre_por_id[sub['id']] = sub.get('name_es') or sub.get('name_zh') or ''

        cat_id_por_pid = {
            p.get('sku'): p.get('category_id', '')
            for p in data.get('products', []) if p.get('sku')
        }

        resueltas = {}
        for pp in pendientes:
            pid = pid_from_url(pp.supplier_url)
            nombre = nombre_por_id.get(cat_id_por_pid.get(pid, ''), '')
            if not nombre:
                continue
            destino = Category.objects.filter(slug=slugify(nombre)[:50]).first() \
                or Category.objects.filter(name=nombre).first()
            if destino is not None:
                resueltas[pp.pk] = destino
        return resueltas

    # ── b) productos pisados por un pendiente ajeno ───────────────────────────

    def _restaurar_pisados(self, dry):
        """Restaura solo lo que tiene FIRMA de colisión.

        No basta con que el producto difiera de su pendiente: un nombre editado
        a mano también difiere y no debe revertirse. La firma del bug es que el
        nombre actual del producto sea el de OTRO pendiente que reservó su mismo
        SKU — prueba de que ese pendiente ajeno lo sobrescribió al aprobarse.
        """
        self.stdout.write('\n── Productos con nombre/precio pisados ──')

        por_sku = {}
        for pp in PendingProduct.objects.filter(status='approved').exclude(supplier_url=''):
            sku = (pp.raw_data or {}).get('sku') or ''
            if sku:
                por_sku.setdefault(sku, []).append(pp)

        n = 0
        for sku, pendientes in por_sku.items():
            if len(pendientes) < 2:
                continue                      # sin SKU compartido no hubo colisión
            product = Product.objects.filter(sku=sku).first()
            if product is None:
                continue

            propio = next((p for p in pendientes
                           if p.supplier_url == product.supplier_url), None)
            if propio is None:
                continue                      # el producto no es de ningún pendiente
            pisador = next((p for p in pendientes
                            if p.supplier_url != product.supplier_url
                            and p.display_name == product.name), None)
            if pisador is None:
                continue                      # sin firma de sobrescritura

            cambios = {}
            if propio.display_name and product.name != propio.display_name:
                cambios['name'] = propio.display_name
                cambios['modaverse_name'] = propio.modaverse_name or propio.display_name
            if propio.base_price and product.base_price != propio.base_price:
                cambios['base_price'] = propio.base_price
            if not cambios:
                continue

            self.stdout.write(
                f'  ~ {product.sku}: {product.name[:30]!r} → {propio.display_name[:30]!r}'
                + ('' if 'base_price' not in cambios
                   else f'  ${product.base_price} → ${propio.base_price}')
            )
            n += 1
            if dry:
                continue
            for campo, valor in cambios.items():
                setattr(product, campo, valor)
            product.save(update_fields=list(cambios))
        return n
