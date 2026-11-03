import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Category, Product, ProductImage, ProductVariant, SizeGroup, VolumeTier,
)
from orders.edicion import (
    EdicionRechazada, agregar_renglon, cambiar_cantidad, motivo_no_editable,
    precio_sugerido, quitar_renglon, renglon_existente, variante_texto,
)
from orders.models import (
    Order, OrderItem, OrderItemChange, OrderPayment, SupplierOrder, SupplierOrderItem,
)
from orders.variantes import nombre_variante


class EdicionTests(TestCase):
    """Servicio de edición de renglones de un pedido web desde el panel."""

    @classmethod
    def setUpTestData(cls):
        # Mismo fixture de categorías que tests_minimos: gorras (20 por pedido)
        # y calzado (12 por modelo/color). Precio = base 100 + envío 0 + margen 100.
        cls.gorras = Category.objects.create(name='Gorras', slug='gorras', min_order_qty=20)
        cls.calzado = Category.objects.create(
            name='Calzado', slug='calzado', min_qty_per_item=12, has_color_variants=True,
        )
        cls.ropa = Category.objects.create(name='Ropa', slug='ropa')
        cls.gorra = cls._producto('G1', 'Gorra lisa', cls.gorras)
        cls.gorra2 = cls._producto('G2', 'Gorra bordada', cls.gorras)
        cls.numeros = SizeGroup.objects.create(name='Calzado', sizes=[25, 26, 27])
        cls.tenis = cls._producto('T1', 'Tenis Air', cls.calzado, size_group=cls.numeros)
        cls.blanco = ProductImage.objects.create(
            product=cls.tenis, image='products/a.jpg', color_label='Blanco', display_order=0)
        # Calzado sin grupo de tallas: renglón regular con mínimo propio.
        cls.chanclas = cls._producto('T2', 'Chanclas', cls.calzado)
        # Dos colorways sin etiqueta, ambos display_order=0 (como los sube el panel).
        cls.tenis_run = cls._producto('T3', 'Tenis Run', cls.calzado, size_group=cls.numeros)
        cls.run_a = ProductImage.objects.create(product=cls.tenis_run, image='products/r1.jpg', display_order=0)
        cls.run_b = ProductImage.objects.create(product=cls.tenis_run, image='products/r2.jpg', display_order=0)
        cls.playera = cls._producto('P1', 'Playera', cls.ropa)
        cls.sudadera = cls._producto('S1', 'Sudadera', cls.ropa)
        cls.tallas = SizeGroup.objects.create(name='Ropa', sizes=['S', 'M', 'L'])
        cls.con_talla = cls._producto('P2', 'Playera con talla', cls.ropa, size_group=cls.tallas)
        cls.con_color = cls._producto('P3', 'Playera con color', cls.ropa,
                                      variant_colors=['Rojo', 'Negro'])
        cls.staff = User.objects.create_user('staff', password='x', is_staff=True)

    @staticmethod
    def _producto(sku, nombre, categoria, **extra):
        return Product.objects.create(
            sku=sku, name=nombre, category=categoria, base_price=100, **extra,
        )

    # ── helpers ──────────────────────────────────────────────────────────
    def _pedido(self, *specs, status='pending', notes=''):
        order = Order.objects.create(
            order_code=f'ED-{Order.objects.count() + 1}', customer_name='Ana',
            customer_phone='4440000000', status=status, notes=notes,
        )
        for s in specs:
            product, qty = s[0], s[1]
            kw = s[2] if len(s) > 2 else {}
            OrderItem.objects.create(
                order=order, product=product, quantity=qty,
                price_snapshot=Decimal(kw.get('price', '200')),
                cost_snapshot=Decimal(kw['cost']) if 'cost' in kw else Decimal('100'),
                sku_snapshot=product.sku if product else 'SKU', name_snapshot=product.name if product else 'Viejo',
                variant_snapshot=nombre_variante(kw.get('size'), kw.get('color'), kw.get('img'), None, None),
            )
        return order

    def _pagar(self, order, monto):
        OrderPayment.objects.create(order=order, fecha=datetime.date(2026, 10, 30), monto=Decimal(monto))
        order.recalc_paid()

    def _estado(self, order):
        """Foto de todo lo que una edición rechazada no puede tocar."""
        order.refresh_from_db()
        return (
            sorted(order.items.values_list('pk', 'product_id', 'quantity', 'price_snapshot', 'cost_snapshot')),
            order.descuento_aplicado, order.is_paid, order.notes,
            order.item_changes.count(),
            SupplierOrderItem.objects.filter(order_item__order=order).count(),
        )

    def assertRechaza(self, order, mensaje, fn, *args, **kwargs):
        antes = self._estado(order)
        with self.assertRaisesMessage(EdicionRechazada, mensaje):
            fn(*args, **kwargs)
        self.assertEqual(self._estado(order), antes)

    # ── agregar ──────────────────────────────────────────────────────────
    def test_agregar_crea_renglon_con_snapshots(self):
        order = self._pedido((self.playera, 1))
        item = agregar_renglon(order, self.staff, self.sudadera, 3)
        item.refresh_from_db()
        self.assertEqual(item.order_id, order.pk)
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.price_snapshot, Decimal('200.00'))
        self.assertEqual(item.cost_snapshot, Decimal('100.00'))
        self.assertEqual(item.sku_snapshot, 'S1')
        self.assertEqual(item.name_snapshot, 'Sudadera')
        self.assertEqual(item.variant_snapshot, '')
        self.assertIsNone(item.variant)

    def test_agregar_con_variante_y_talla_arma_variant_snapshot(self):
        order = self._pedido((self.playera, 1))
        variante = ProductVariant.objects.create(product=self.sudadera, name='Azul', extra_price=10)
        item = agregar_renglon(order, self.staff, self.sudadera, 1, variant=variante)
        self.assertEqual(item.variant_snapshot, 'Azul')
        self.assertEqual(item.variant, variante)
        self.assertEqual(item.price_snapshot, Decimal('210.00'))
        item2 = agregar_renglon(order, self.staff, self.con_talla, 1, size_name='M')
        self.assertEqual(item2.variant_snapshot, 'Talla M')

    def test_agregar_precio_manual_se_guarda_tal_cual(self):
        order = self._pedido((self.playera, 1))
        item = agregar_renglon(order, self.staff, self.sudadera, 2, precio=Decimal('1'))
        item.refresh_from_db()
        self.assertEqual(item.price_snapshot, Decimal('1.00'))
        self.assertEqual(item.cost_snapshot, Decimal('100.00'))

    def test_agregar_aplica_tier_por_renglon(self):
        VolumeTier.objects.create(category=self.ropa, min_qty=50, discount_amount=50)
        self.assertEqual(precio_sugerido(self.sudadera, 49), Decimal('200.00'))
        self.assertEqual(precio_sugerido(self.sudadera, 50), Decimal('150.00'))
        order = self._pedido((self.playera, 1))
        item = agregar_renglon(order, self.staff, self.sudadera, 50)
        self.assertEqual(item.price_snapshot, Decimal('150.00'))

    def test_agregar_dos_veces_suma_al_mismo_renglon(self):
        order = self._pedido((self.playera, 1))
        primero = agregar_renglon(order, self.staff, self.con_talla, 2, size_name='M')
        segundo = agregar_renglon(order, self.staff, self.con_talla, 3, size_name='M')
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(order.items.filter(product=self.con_talla).count(), 1)
        self.assertEqual(order.items.get(product=self.con_talla).quantity, 5)
        acciones = list(order.item_changes.order_by('id').values_list('accion', flat=True))
        self.assertEqual(acciones, [OrderItemChange.AGREGAR, OrderItemChange.CANTIDAD])
        ultimo = order.item_changes.order_by('id').last()
        self.assertEqual((ultimo.cantidad_antes, ultimo.cantidad_despues), (2, 5))
        # Otra talla es otro renglón.
        agregar_renglon(order, self.staff, self.con_talla, 1, size_name='L')
        self.assertEqual(order.items.filter(product=self.con_talla).count(), 2)

    def test_agregar_rechaza_inactivo_y_coming_soon(self):
        order = self._pedido((self.playera, 1))
        msg = 'Ese producto no está disponible en la tienda.'
        Product.objects.filter(pk=self.sudadera.pk).update(is_active=False)
        self.sudadera.refresh_from_db()
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.sudadera, 1)
        Product.objects.filter(pk=self.sudadera.pk).update(is_active=True, status='coming_soon')
        self.sudadera.refresh_from_db()
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.sudadera, 1)
        # También cuando el producto ya está en el pedido: no se suma a escondidas.
        Product.objects.filter(pk=self.playera.pk).update(status='sold_out')
        self.playera.refresh_from_db()
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.playera, 1)

    def test_agregar_exige_talla_y_color(self):
        order = self._pedido((self.playera, 1))
        self.assertRechaza(order, 'Selecciona la talla.',
                           agregar_renglon, order, self.staff, self.con_talla, 1)
        self.assertRechaza(order, 'Selecciona el color.',
                           agregar_renglon, order, self.staff, self.con_color, 1)
        item = agregar_renglon(order, self.staff, self.con_color, 1, color='Rojo')
        self.assertEqual(item.variant_snapshot, 'Rojo')

    def test_agregar_cantidad_y_precio_invalidos(self):
        order = self._pedido((self.playera, 1))
        self.assertRechaza(order, 'La cantidad debe ser al menos 1.',
                           agregar_renglon, order, self.staff, self.sudadera, 0)
        self.assertRechaza(order, 'El precio no puede ser negativo.',
                           agregar_renglon, order, self.staff, self.sudadera, 1, precio=Decimal('-1'))

    def test_agregar_regular_bajo_min_producto_rechaza(self):
        Product.objects.filter(pk=self.sudadera.pk).update(min_order_qty=3)
        self.sudadera.refresh_from_db()
        order = self._pedido((self.playera, 1))
        self.assertRechaza(order, 'Mínimo 3 piezas para Sudadera',
                           agregar_renglon, order, self.staff, self.sudadera, 2)
        agregar_renglon(order, self.staff, self.sudadera, 3)
        # Ya en el pedido, sumar 1 no vuelve a exigir el mínimo (igual que cart_add).
        item = agregar_renglon(order, self.staff, self.sudadera, 1)
        self.assertEqual(item.quantity, 4)

    def test_agregar_calzado_con_menos_de_12_rechaza(self):
        order = self._pedido((self.playera, 1))
        self.assertRechaza(
            order, 'No se puede: faltan 7 piezas de Tenis Air · Blanco (mínimo 12)',
            agregar_renglon, order, self.staff, self.tenis, 5, size_name='26', image=self.blanco)
        # Regular en calzado: aplica el mínimo propio (12) antes que el de grupo.
        self.assertRechaza(order, 'Mínimo 12 piezas para Chanclas',
                           agregar_renglon, order, self.staff, self.chanclas, 5)
        item = agregar_renglon(order, self.staff, self.tenis, 12, size_name='26', image=self.blanco)
        self.assertEqual(item.variant_snapshot, 'Blanco · Talla 26')

    def test_agregar_merge_con_precio_explicito_lo_aplica_al_combinado(self):
        # El modal ya mostró el renglón existente y el precio de la cantidad combinada.
        VolumeTier.objects.create(category=self.ropa, min_qty=50, discount_amount=50)
        order = self._pedido((self.playera, 1), (self.sudadera, 50, {'price': '150'}))
        existente = order.items.get(product=self.sudadera)
        item = agregar_renglon(order, self.staff, self.sudadera, 10, precio=Decimal('140'))
        self.assertEqual(item.pk, existente.pk)
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.price_snapshot), (60, Decimal('140.00')))
        self.assertEqual(order.items.filter(product=self.sudadera).count(), 1)
        cambio = order.item_changes.get()
        self.assertEqual(cambio.accion, OrderItemChange.CANTIDAD)
        self.assertEqual((cambio.cantidad_antes, cambio.cantidad_despues), (50, 60))
        self.assertEqual((cambio.precio_antes, cambio.precio_despues), (Decimal('150'), Decimal('140')))

    def test_renglon_existente_usa_el_mismo_texto_que_agregar(self):
        order = self._pedido((self.playera, 1))
        self.assertIsNone(renglon_existente(
            order, self.con_talla, None, variante_texto(self.con_talla, size_name='M')))
        item = agregar_renglon(order, self.staff, self.con_talla, 2, size_name='M')
        self.assertEqual(renglon_existente(
            order, self.con_talla, None, variante_texto(self.con_talla, size_name='M')), item)
        self.assertIsNone(renglon_existente(
            order, self.con_talla, None, variante_texto(self.con_talla, size_name='L')))

    def test_colorways_sin_etiqueta_numeran_como_la_tienda(self):
        # La tienda numera por posición en product.images.all(), no por display_order.
        posiciones = [i.pk for i in self.tenis_run.images.all()]
        num = {pk: posiciones.index(pk) + 1 for pk in posiciones}
        order = self._pedido((self.playera, 1))
        a = agregar_renglon(order, self.staff, self.tenis_run, 12, size_name='26', image=self.run_a)
        b = agregar_renglon(order, self.staff, self.tenis_run, 12, size_name='26', image=self.run_b)
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(order.items.filter(product=self.tenis_run).count(), 2)
        self.assertEqual({a.variant_snapshot, b.variant_snapshot},
                         {'Color 1 · Talla 26', 'Color 2 · Talla 26'})
        self.assertEqual(a.variant_snapshot, f'Color {num[self.run_a.pk]} · Talla 26')
        # Mismo texto que arma cart_add con el color_num que manda el front.
        for img, renglon in ((self.run_a, a), (self.run_b, b)):
            self.client.post(reverse('orders:cart_add'), data=json.dumps({
                'product_id': self.tenis_run.pk, 'size_name': '26', 'image_pk': img.pk,
                'color_num': num[img.pk], 'qty': 12,
            }), content_type='application/json')
            nombres = [v['variant_name'] for v in self.client.session['cart'].values()
                       if v.get('image_pk') == img.pk]
            self.assertEqual(nombres, [renglon.variant_snapshot])

    def test_agregar_talla_inexistente_rechaza(self):
        order = self._pedido((self.playera, 1))
        msg = 'Esa talla no existe para este producto.'
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.con_talla, 1, size_name='XL')
        # Sin grupo de tallas, cualquier talla sobra.
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.sudadera, 1, size_name='M')
        # El grupo guarda números; la talla llega como texto.
        item = agregar_renglon(order, self.staff, self.tenis, 12, size_name='26', image=self.blanco)
        self.assertEqual(item.variant_snapshot, 'Blanco · Talla 26')

    # ── estado y proveedor ───────────────────────────────────────────────
    def test_estado_no_editable_rechaza(self):
        msg = 'Solo se pueden editar pedidos Pendientes o Confirmados.'
        for status in ('in_preparation', 'shipped', 'delivered', 'cancelled'):
            with self.subTest(status=status):
                order = self._pedido((self.playera, 1), (self.sudadera, 1), status=status)
                item = order.items.get(product=self.playera)
                self.assertEqual(motivo_no_editable(order), msg)
                self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.sudadera, 1)
                self.assertRechaza(order, msg, cambiar_cantidad, order, self.staff, item, 3)
                self.assertRechaza(order, msg, quitar_renglon, order, self.staff, item)
        for status in ('pending', 'confirmed'):
            order = self._pedido((self.playera, 1), status=status)
            self.assertIsNone(motivo_no_editable(order))
            agregar_renglon(order, self.staff, self.sudadera, 1)

    def test_estado_se_revalida_dentro_del_bloqueo(self):
        # La instancia que trae el llamador dice 'pending', pero en la BD ya se movió.
        order = self._pedido((self.playera, 1))
        Order.objects.filter(pk=order.pk).update(status='in_preparation')
        with self.assertRaises(EdicionRechazada):
            agregar_renglon(order, self.staff, self.sudadera, 1)
        self.assertEqual(order.items.count(), 1)

    def test_supplier_lanzado_rechaza_y_no_lanzado_sincroniza(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 1))
        so = SupplierOrder.objects.create(order=order)
        Product.objects.filter(pk=self.gorra.pk).update(
            supplier_url='https://modaverse.example/p/1')
        self.gorra.refresh_from_db()
        item = agregar_renglon(order, self.staff, self.gorra, 20)
        si = SupplierOrderItem.objects.get(order_item=item)
        self.assertEqual(si.supplier_order, so)
        self.assertEqual(si.status, 'pending')
        # Quitar: el CASCADE se lleva el ítem del proveedor.
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        agregar_renglon(order, self.staff, self.sudadera, 1)
        nuevo = order.items.get(product=self.sudadera)
        quitar_renglon(order, self.staff, nuevo)
        self.assertFalse(SupplierOrderItem.objects.filter(order_item_id=nuevo.pk).exists())

        msg = 'El pedido al proveedor ya se lanzó; ya no se pueden cambiar productos.'
        # Lanzado: aunque esté de nuevo en 'pending' (reintento).
        so.run_started_at = datetime.datetime(2026, 10, 30, tzinfo=datetime.timezone.utc)
        so.save()
        self.assertEqual(motivo_no_editable(order), msg)
        self.assertRechaza(order, msg, agregar_renglon, order, self.staff, self.sudadera, 1)
        # Ítem marcado 'added' a mano, sin run_started_at.
        so.run_started_at = None
        so.save()
        si.status = 'added'
        si.save()
        self.assertRechaza(order, msg, cambiar_cantidad, order, self.staff, item, 25)

    def test_sin_supplier_order_no_crea_items_de_proveedor(self):
        order = self._pedido((self.playera, 1))
        agregar_renglon(order, self.staff, self.sudadera, 1)
        self.assertFalse(SupplierOrderItem.objects.exists())

    # ── cantidad ─────────────────────────────────────────────────────────
    def test_cantidad_sube_cruza_tier_y_baja_regresa(self):
        VolumeTier.objects.create(category=self.ropa, min_qty=50, discount_amount=50)
        order = self._pedido((self.playera, 10))
        item = order.items.get()
        item = cambiar_cantidad(order, self.staff, item, 50)
        self.assertEqual((item.quantity, item.price_snapshot), (50, Decimal('150.00')))
        item = cambiar_cantidad(order, self.staff, item, 49)
        self.assertEqual((item.quantity, item.price_snapshot), (49, Decimal('200.00')))
        item = cambiar_cantidad(order, self.staff, item, 60, precio=Decimal('175'))
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.price_snapshot), (60, Decimal('175.00')))

    def test_cantidad_no_toca_cost_snapshot(self):
        order = self._pedido((self.playera, 10, {'cost': '37.50'}))
        item = order.items.get()
        Product.objects.filter(pk=self.playera.pk).update(base_price=999)
        cambiar_cantidad(order, self.staff, item, 20)
        item.refresh_from_db()
        self.assertEqual(item.cost_snapshot, Decimal('37.50'))

    def test_cantidad_en_renglon_sin_producto_conserva_precio(self):
        order = self._pedido((self.playera, 1))
        viejo = OrderItem.objects.create(
            order=order, product=None, quantity=2, price_snapshot=Decimal('333.33'),
            cost_snapshot=None, sku_snapshot='OLD', name_snapshot='Producto borrado')
        item = cambiar_cantidad(order, self.staff, viejo, 5)
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.price_snapshot), (5, Decimal('333.33')))
        self.assertIsNone(item.cost_snapshot)

    def test_cantidad_sin_cambio_no_registra(self):
        order = self._pedido((self.playera, 3))
        item = order.items.get()
        cambiar_cantidad(order, self.staff, item, 3)
        self.assertEqual(order.item_changes.count(), 0)

    def test_cantidad_invalida_y_renglon_ajeno_rechazan(self):
        order = self._pedido((self.playera, 3))
        item = order.items.get()
        self.assertRechaza(order, 'La cantidad debe ser al menos 1.',
                           cambiar_cantidad, order, self.staff, item, 0)
        self.assertRechaza(order, 'El precio no puede ser negativo.',
                           cambiar_cantidad, order, self.staff, item, 4, precio=Decimal('-0.01'))
        otro = self._pedido((self.sudadera, 1))
        ajeno = otro.items.get()
        self.assertRechaza(order, 'Ese renglón ya no está en el pedido.',
                           cambiar_cantidad, order, self.staff, ajeno, 2)
        self.assertRechaza(order, 'Ese renglón ya no está en el pedido.',
                           quitar_renglon, order, self.staff, ajeno)
        self.assertEqual(ajeno.__class__.objects.get(pk=ajeno.pk).quantity, 1)

    # ── mínimos ──────────────────────────────────────────────────────────
    def test_bajar_gorras_bajo_20_rechaza_y_revierte(self):
        order = self._pedido((self.gorra, 12), (self.gorra2, 8))
        item = order.items.get(product=self.gorra)
        self.assertRechaza(order, 'No se puede: faltan 1 piezas de Gorras (mínimo 20)',
                           cambiar_cantidad, order, self.staff, item, 11)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 12)
        self.assertEqual(order.item_changes.count(), 0)
        # Quitar un renglón de gorras también deja la raíz corta.
        self.assertRechaza(order, 'No se puede: faltan 8 piezas de Gorras (mínimo 20)',
                           quitar_renglon, order, self.staff, order.items.get(product=self.gorra2))

    def test_bajar_calzado_bajo_12_por_modelo_rechaza(self):
        order = self._pedido(
            (self.tenis, 6, {'size': '26', 'img': self.blanco}),
            (self.tenis, 6, {'size': '27', 'img': self.blanco}),
        )
        item = order.items.get(variant_snapshot='Blanco · Talla 26')
        self.assertRechaza(order, 'No se puede: faltan 1 piezas de Tenis Air · Blanco (mínimo 12)',
                           cambiar_cantidad, order, self.staff, item, 5)
        self.assertRechaza(order, 'No se puede: faltan 6 piezas de Tenis Air · Blanco (mínimo 12)',
                           quitar_renglon, order, self.staff, item)

    def test_pedido_que_ya_venia_corto_acepta_edicion_que_no_empeora(self):
        order = self._pedido((self.gorra, 10))   # ya le faltan 10
        agregar_renglon(order, self.staff, self.gorra2, 5)          # 15: sigue corto, mejora
        item = order.items.get(product=self.gorra)
        cambiar_cantidad(order, self.staff, item, 10, precio=Decimal('180'))  # mismo total
        self.assertRechaza(order, 'No se puede: faltan 6 piezas de Gorras (mínimo 20)',
                           cambiar_cantidad, order, self.staff, item, 9)

    # ── quitar ───────────────────────────────────────────────────────────
    def test_quitar_ultimo_renglon_rechaza(self):
        order = self._pedido((self.playera, 1))
        self.assertRechaza(order, 'No se puede quitar el último producto; cancela el pedido.',
                           quitar_renglon, order, self.staff, order.items.get())

    def test_quitar_borra_renglon(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 2))
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        self.assertEqual(list(order.items.values_list('product_id', flat=True)), [self.playera.pk])

    # ── pagos y cupón ────────────────────────────────────────────────────
    def test_quitar_en_pedido_liquidado_deja_saldo_a_favor(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 1))   # 400
        self._pagar(order, '400')
        self.assertTrue(order.is_paid)
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        order.refresh_from_db()
        self.assertEqual(order.balance_due, Decimal('-200'))
        self.assertTrue(order.is_paid)

    def test_agregar_en_pedido_liquidado_reabre_saldo(self):
        order = self._pedido((self.playera, 1))
        self._pagar(order, '200')
        self.assertTrue(order.is_paid)
        agregar_renglon(order, self.staff, self.sudadera, 1)
        order.refresh_from_db()
        self.assertFalse(order.is_paid)
        self.assertEqual(order.balance_due, Decimal('200'))

    def test_cantidad_baja_en_pedido_con_saldo_lo_liquida(self):
        order = self._pedido((self.playera, 2))   # 400
        self._pagar(order, '200')
        self.assertFalse(order.is_paid)
        cambiar_cantidad(order, self.staff, order.items.get(), 1)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)

    def test_cupon_se_recorta_al_subtotal(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 1))   # 400
        Order.objects.filter(pk=order.pk).update(descuento_aplicado=Decimal('300'))
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        order.refresh_from_db()
        self.assertEqual(order.descuento_aplicado, Decimal('200'))
        self.assertEqual(order.total, Decimal('0'))
        # Si vuelve a subir el subtotal, el cupón recortado se queda como está.
        agregar_renglon(order, self.staff, self.sudadera, 1)
        order.refresh_from_db()
        self.assertEqual(order.descuento_aplicado, Decimal('200'))

    def test_cupon_menor_al_subtotal_no_cambia(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 1))
        Order.objects.filter(pk=order.pk).update(descuento_aplicado=Decimal('50'))
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        order.refresh_from_db()
        self.assertEqual(order.descuento_aplicado, Decimal('50'))

    # ── notas y bitácora ─────────────────────────────────────────────────
    def test_notes_no_cambia(self):
        order = self._pedido((self.playera, 1), (self.sudadera, 1), notes='Entregar en la tarde')
        item = agregar_renglon(order, self.staff, self.gorra, 20)
        cambiar_cantidad(order, self.staff, item, 21)
        quitar_renglon(order, self.staff, order.items.get(product=self.sudadera))
        order.refresh_from_db()
        self.assertEqual(order.notes, 'Entregar en la tarde')

    def test_bitacora_registra_antes_y_despues(self):
        order = self._pedido((self.playera, 2), (self.sudadera, 1))
        item = agregar_renglon(order, self.staff, self.con_talla, 3, size_name='L', precio=Decimal('190'))
        c = order.item_changes.get()
        self.assertEqual(c.accion, OrderItemChange.AGREGAR)
        self.assertEqual(c.usuario, self.staff)
        self.assertEqual(c.descripcion, 'Playera con talla — Talla L')
        self.assertEqual((c.cantidad_antes, c.cantidad_despues), (None, 3))
        self.assertEqual((c.precio_antes, c.precio_despues), (None, Decimal('190')))

        cambiar_cantidad(order, self.staff, item, 4, precio=Decimal('185.50'))
        c = order.item_changes.order_by('id').last()
        self.assertEqual(c.accion, OrderItemChange.CANTIDAD)
        self.assertEqual((c.cantidad_antes, c.cantidad_despues), (3, 4))
        self.assertEqual((c.precio_antes, c.precio_despues), (Decimal('190'), Decimal('185.50')))

        quitar_renglon(order, None, order.items.get(product=self.sudadera))
        c = order.item_changes.order_by('id').last()
        self.assertEqual(c.accion, OrderItemChange.QUITAR)
        self.assertIsNone(c.usuario)
        self.assertEqual(c.descripcion, 'Sudadera')
        self.assertEqual((c.cantidad_antes, c.cantidad_despues), (1, None))
        self.assertEqual((c.precio_antes, c.precio_despues), (Decimal('200'), None))
        self.assertEqual(order.item_changes.count(), 3)
