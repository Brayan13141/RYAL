# -*- coding: utf-8 -*-
"""Pone al día `base_price` con el precio actual del proveedor.

`load_productos` ya sincroniza el precio de los productos que toca, pero solo
recorre las categorías de su corrida. Este comando hace la pasada completa (o
acotada con --category) y, sobre todo, tiene --dry-run: el precio es dinero, y
conviene ver la lista antes de escribirla.

Respeta las ediciones manuales del panel — ver `catalog.modaverse.sincronizar_precio`.
"""
from django.core.management.base import BaseCommand

from catalog.models import Category, Product
from catalog.modaverse import (
    category_filter_ids, pid_from_url, precio_proveedor,
    read_modaverse_json, sincronizar_precio,
)


class Command(BaseCommand):
    help = 'Sincroniza base_price con el precio actual de Modaverse (respeta precios editados a mano).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra qué cambiaría sin escribir nada.')
        parser.add_argument('--category', nargs='+', metavar='KEYWORD',
                            help='Limita el alcance a las categorías que coincidan.')
        parser.add_argument('--limit-list', type=int, default=40,
                            help='Cuántos cambios listar en pantalla (default 40).')

    def _read_modaverse_json(self):
        """Lee scraped_modaverse.json. Wrapper con warning de stdout."""
        data = read_modaverse_json()
        if data is None:
            self.stdout.write(self.style.WARNING('  ⚠ No se encontró scraped_modaverse.json'))
        return data

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — no se escribe nada'))

        data = self._read_modaverse_json()
        if data is None:
            return

        filter_ids = None
        if opts.get('category'):
            filter_ids = category_filter_ids(data.get('categories', []), opts['category'])
            if not filter_ids:
                self.stdout.write(self.style.ERROR(
                    f'  ⚠ Ninguna categoría coincide con: {opts["category"]}'))
                return

        # Precio del proveedor por productId, ya filtrado por alcance.
        precios = {}
        for p in data.get('products', []):
            if filter_ids is not None and p.get('category_id', '') not in filter_ids:
                continue
            pid = pid_from_url(p.get('url')) or p.get('sku') or ''
            if pid:
                precios[pid] = precio_proveedor(p)

        qs = Product.objects.select_related('category')
        if opts.get('category'):
            cat_ids = set()
            for raiz in Category.objects.filter(parent__isnull=True):
                subs = list(raiz.subcategories.all())
                nombres = [raiz.name] + [c.name for c in subs]
                if any(kw.lower() in n.lower() for kw in opts['category'] for n in nombres):
                    cat_ids.add(raiz.id)
                    cat_ids.update(c.id for c in subs)
            qs = qs.filter(category_id__in=cat_ids)

        cambios, adoptados, sin_json, manuales = [], 0, 0, 0
        for prod in qs:
            pid = pid_from_url(prod.supplier_url)
            if not pid or pid not in precios:
                sin_json += 1
                continue
            precio = precios[pid]
            antes = prod.base_price
            campos = sincronizar_precio(prod, precio)
            if not campos:
                continue
            if 'base_price' in campos:
                cambios.append((prod, antes, precio))
            elif prod.modaverse_price is not None and antes != precio:
                # la marca avanzó pero el precio no: o se adoptó, o es manual
                if antes == precio:
                    adoptados += 1
                else:
                    manuales += 1
            if not dry_run:
                prod.save(update_fields=campos)

        self.stdout.write(f'\n── Precios a actualizar: {len(cambios)} ──')
        for prod, antes, ahora in sorted(cambios, key=lambda c: -(c[1] - c[2]))[:opts['limit_list']]:
            flecha = self.style.SUCCESS('↓') if ahora < antes else self.style.WARNING('↑')
            self.stdout.write(
                f'  {flecha} {prod.sku:<16} {(prod.name or "")[:22]:<22} '
                f'{prod.category.name[:24]:<24} {antes} → {ahora}'
            )
        if len(cambios) > opts['limit_list']:
            self.stdout.write(f'  … y {len(cambios) - opts["limit_list"]} más')

        baja = sum(1 for _, a, n in cambios if n < a)
        ahorro = sum(a - n for _, a, n in cambios if n < a)
        self.stdout.write(
            f'\n{len(cambios)} precios actualizados · {baja} bajan (${ahorro} de costo base de más) '
            f'· {len(cambios) - baja} suben · {manuales} respetados por edición manual '
            f'· {sin_json} sin precio en el JSON'
        )
