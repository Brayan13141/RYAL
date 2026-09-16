# -*- coding: utf-8 -*-
"""Arma el carrito de modaverse.vip para un pedido de ryalsneackers.

Una llamada a la API por producto; sin navegador. El carrito de modaverse es un
array JSON en localStorage.user.shopCarList, así que armarlo es construir ese
array — no hace falta recorrer el sitio como lo hacía la versión anterior.

El pid sale de supplier_url, así que no hay búsqueda por nombre que pueda fallar.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from catalog.modaverse_api import ModaverseUnavailable, get_product, new_client
from orders.cart_builder import (
    build_cart_entry, build_cart_script, parse_variant, stock_warnings,
)
from orders.models import SupplierOrder

_PID_RE = re.compile(r'/proinfo/(\w+)|[?&]pid=(\w+)')


class Command(BaseCommand):
    help = 'Arma el carrito en modaverse.vip para el pedido dado'

    def add_arguments(self, parser):
        parser.add_argument('order_id', type=int)

    def handle(self, *args, **options):
        order_id = options['order_id']

        try:
            supplier_order = (
                SupplierOrder.objects
                .select_related('order')
                .prefetch_related('items__order_item__product__category')
                .get(order_id=order_id)
            )
        except SupplierOrder.DoesNotExist:
            raise CommandError(
                f'No existe SupplierOrder para el pedido #{order_id}. '
                f'Inicializalo primero desde el panel.'
            )

        pendientes = [i for i in supplier_order.items.all() if i.status == 'pending']
        if not pendientes:
            self.stdout.write(self.style.WARNING('No hay ítems pendientes. Nada que hacer.'))
            return

        supplier_order.status = 'running'
        supplier_order.save(update_fields=['status'])

        # (pid, talla, color) → entrada de carrito. Dos ítems del pedido con el
        # mismo producto Y la misma variante suman cantidades; con variantes
        # distintas son dos entradas separadas, cada una con su propio num.
        entradas = {}
        total = len(pendientes)
        client = new_client()

        try:
            for idx, item in enumerate(pendientes, 1):
                sku = item.order_item.sku_snapshot
                pid = self._pid(item.supplier_url)
                self.stdout.write('')
                self.stdout.write(f'===== [{idx}/{total}] {sku} =====')

                if not pid:
                    self._guardar(item, 'no_url', 'El producto no tiene supplier_url de modaverse')
                    self.stdout.write(self.style.WARNING('  sin URL de proveedor'))
                    continue

                producto = get_product(pid, client=client)
                if producto is None:
                    self._guardar(item, 'variant_not_found',
                                  f'La API dice que el producto {pid} no existe')
                    self.stdout.write(self.style.WARNING(f'  la API no conoce el pid {pid}'))
                    continue

                qty = item.order_item.quantity
                entry, status, notas = build_cart_entry(
                    producto, item.variant_target, qty,
                    category_name=self._categoria(item),
                )

                if status != 'added':
                    self._guardar(item, status, notas)
                    self.stdout.write(self.style.WARNING(f'  {notas}'))
                    continue

                avisos = stock_warnings(producto, qty)
                if avisos:
                    aviso_txt = '⚠ ' + '; '.join(avisos)
                    notas = f'{notas}; {aviso_txt}' if notas else aviso_txt

                talla, color = parse_variant(item.variant_target)
                clave = (pid, talla.casefold(), color.casefold())
                if clave in entradas:
                    entradas[clave]['num'] += qty
                else:
                    entradas[clave] = entry

                self._guardar(item, 'added', notas)
                self.stdout.write(self.style.SUCCESS(
                    f'  OK {producto.get("productName")} ×{qty} {item.variant_target}'.rstrip()
                ))
                if avisos:
                    self.stdout.write(self.style.WARNING(f'  {aviso_txt}'))

        except ModaverseUnavailable as exc:
            client.close()
            supplier_order.status = 'failed'
            supplier_order.cart_script = ''
            supplier_order.save(update_fields=['status', 'cart_script', 'updated_at'])
            self.stdout.write(self.style.ERROR(f'\nAPI de Modaverse inaccesible: {exc}'))
            self.stdout.write(self.style.ERROR(
                'El carrito NO se armó. Un carrito parcial se paga en mercancía '
                'equivocada, así que no se genera ninguno.'
            ))
            self.stdout.write(
                'Ver el plan de contingencia en '
                'docs/superpowers/specs/2026-09-15-modaverse-cart-api-design.md'
            )
            return
        finally:
            if not client.is_closed:
                client.close()

        cart_script = build_cart_script(list(entradas.values()))
        supplier_order.status = self._estado_final(supplier_order)
        supplier_order.cart_script = cart_script
        supplier_order.save(update_fields=['status', 'cart_script', 'updated_at'])

        self._resumen(supplier_order, len(entradas))

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _pid(supplier_url: str) -> str:
        m = _PID_RE.search(supplier_url or '')
        if not m:
            return ''
        return m.group(1) or m.group(2) or ''

    @staticmethod
    def _categoria(item) -> str:
        try:
            cat = item.order_item.product.category
            return cat.name if cat else ''
        except Exception:
            return ''

    @staticmethod
    def _guardar(item, status: str, notas: str):
        """Graba el ítem al instante: si algo se cuelga, el progreso ya está en la BD."""
        item.status = status
        item.notes  = notas or ''
        item.save(update_fields=['status', 'notes'])

    @staticmethod
    def _estado_final(supplier_order) -> str:
        estados = set(supplier_order.items.values_list('status', flat=True))
        if estados <= {'added', 'no_url'}:
            return 'done'
        if 'added' in estados:
            return 'partial'
        return 'failed'

    def _resumen(self, supplier_order, n: int):
        self.stdout.write('')
        self.stdout.write('--- Resumen ---')
        for item in supplier_order.items.select_related('order_item').all():
            icono = {'added': 'OK', 'variant_not_found': '!!',
                     'no_url': '--', 'pending': '??'}.get(item.status, '??')
            self.stdout.write(f'  [{icono}] {item.order_item.sku_snapshot} — {item.get_status_display()}')
            if item.notes:
                self.stdout.write(f'      {item.notes[:160]}')

        if n:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'Carrito generado: {n} ítem(s).'))
            self.stdout.write('  Para importarlo en tu navegador:')
            self.stdout.write('  1. Abrí https://www.modaverse.vip (cualquier página, NO la del carrito)')
            self.stdout.write('  2. F12 → Console → escribí "allow pasting" → Enter')
            self.stdout.write('  3. Pegá el script del panel → Enter')
            self.stdout.write('  4. La página se recarga sola con el carrito cargado')
        else:
            self.stdout.write(self.style.WARNING('No se generó carrito.'))
        self.stdout.write('')
