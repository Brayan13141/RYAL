import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.conf import settings

from catalog.models import Category, Product, ProductImage, SizeGroup
from orders.models import Order
from orders.views import _cart_key
from orders.management.commands.sync_modaverse_order import Command as SyncCmd


# ── Fix A: _names_match word-boundary ────────────────────────────────────────

class NamesMatchTests(TestCase):
    """_names_match NO debe matchear substrings que se extienden sin espacio."""

    def _m(self, a, b):
        return SyncCmd._names_match(a, b)

    # Casos que DEBEN matchear
    def test_exact_match(self):
        self.assertTrue(self._m("9026", "9026"))

    def test_exact_match_mixed_case(self):
        self.assertTrue(self._m("Air Force 1", "air force 1"))

    def test_longer_description_con_espacio(self):
        # "Air Force 1" SÍ debe matchear "Air Force 1 Low White"
        self.assertTrue(self._m("Air Force 1", "Air Force 1 Low White"))

    def test_reverse_longer_with_space(self):
        # "Jordan 1 Low" contiene "Jordan 1" → match
        self.assertTrue(self._m("Jordan 1 Low", "Jordan 1"))

    def test_model_code_exact(self):
        self.assertTrue(self._m("RJ-003", "RJ-003"))

    def test_match_cjk_suffix_codigo_alfanumerico(self):
        # "TAA0638" SÍ debe matchear "TAA0638黑金" (código con letras + color CJK)
        self.assertTrue(self._m("TAA0638", "TAA0638黑金"))

    # Casos que NO deben matchear (root cause de item 1)
    def test_no_match_cjk_color_suffix(self):
        # "9026" NO debe matchear "9026白" (variante de color diferente)
        self.assertFalse(self._m("9026", "9026白"))

    def test_no_match_cjk_color_suffix_2(self):
        self.assertFalse(self._m("9005", "9005黑"))

    def test_no_match_digit_suffix(self):
        # "白金-10" NO debe matchear "白金-109" (modelo diferente)
        self.assertFalse(self._m("白金-10", "白金-109"))

    def test_no_match_digit_suffix_2(self):
        # "玫瑰金-10" NO debe matchear "玫瑰金-109"
        self.assertFalse(self._m("玫瑰金-10", "玫瑰金-109"))

    def test_no_match_dash_color_suffix(self):
        # "RJ-003" NO debe matchear "RJ-003-BLACK"
        self.assertFalse(self._m("RJ-003", "RJ-003-BLACK"))

    def test_no_match_jordan_1_vs_jordan_10(self):
        # "Jordan 1" NO debe matchear "Jordan 10"
        self.assertFalse(self._m("Jordan 1", "Jordan 10"))

    def test_empty_a_no_match(self):
        self.assertFalse(self._m("", "Air Force 1"))

    def test_empty_b_no_match(self):
        self.assertFalse(self._m("Air Force 1", ""))

    def test_both_empty_no_match(self):
        self.assertFalse(self._m("", ""))


# ── Fix B: _card_name_ok reemplaza el bypass "if not name" ───────────────────

class CardNameOkTests(TestCase):
    """
    _card_name_ok(name, mv_name, card_name) verifica si la tarjeta es la correcta.
    Sin nombre NI mv_name → confiar solo en PID (retorna True).
    Con mv_name → DEBE verificar (no bypass por name vacío).
    """

    def _ok(self, name, mv_name, card_name):
        return SyncCmd._card_name_ok(name, mv_name, card_name)

    def test_sin_nombre_ni_mv_acepta_cualquier_card(self):
        # Sin información de nombre, confiamos en el PID — aceptar
        self.assertTrue(self._ok("", "", "Jordan 1 High"))

    def test_con_mv_name_verifica_aunque_name_vacio(self):
        # mv_name="9026", card="9026白" → NO debe aceptar (modelo diferente)
        self.assertFalse(self._ok("", "9026", "9026白"))

    def test_con_mv_name_acepta_match_exacto(self):
        self.assertTrue(self._ok("", "9026", "9026"))

    def test_name_correcto_acepta(self):
        self.assertTrue(self._ok("Jordan 1", "", "Jordan 1 High"))

    def test_name_incorrecto_rechaza(self):
        self.assertFalse(self._ok("Jordan 1", "", "Nike Dunk Low"))

    def test_name_vacio_mv_incorrecto_rechaza(self):
        # Si mv_name no coincide, debe rechazar aunque name esté vacío
        self.assertFalse(self._ok("", "Jordan 1", "Nike Dunk Low"))


class CartKeyColorTests(TestCase):
    """_cart_key incorpora el color (dimensión variant_colors)."""

    def test_talla_y_color(self):
        self.assertEqual(
            _cart_key(7, None, size_name="L", color="Rojo burdeos"),
            "7_size_L_color_Rojo burdeos",
        )

    def test_solo_color(self):
        self.assertEqual(_cart_key(7, None, color="Negro"), "7_color_Negro")

    def test_solo_talla_sin_color_sin_cambios(self):
        self.assertEqual(_cart_key(7, None, size_name="L"), "7_size_L")


class CartAddColorTests(TestCase):
    """cart_add: exige color cuando variant_colors no está vacío; arma variant_name."""

    def setUp(self):
        self.cat = Category.objects.create(name="Calidad 1:1", slug="calidad-11")
        self.sg = SizeGroup.objects.create(name="Ropa · M·L", sizes=["M", "L"])
        self.prod_tc = Product.objects.create(
            sku="RYL-GEN-1", name="Jersey", category=self.cat, base_price=Decimal("250"),
            size_group=self.sg, variant_colors=["Rojo burdeos", "Negro"],
        )
        self.prod_solo_color = Product.objects.create(
            sku="RYL-GEN-2", name="Gorra Edición", category=self.cat, base_price=Decimal("150"),
            variant_colors=["Negro"],
        )
        self.url = reverse("orders:cart_add")

    def _post(self, payload):
        return self.client.post(self.url, data=json.dumps(payload), content_type="application/json")

    def test_rechaza_sin_color_cuando_hay_variant_colors(self):
        res = self._post({"product_id": self.prod_tc.pk, "size_name": "L", "qty": 1})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_talla_mas_color_arma_variant_name(self):
        res = self._post({"product_id": self.prod_tc.pk, "size_name": "L",
                          "color": "Rojo burdeos", "qty": 1})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        cart = self.client.session["cart"]
        key = f"{self.prod_tc.pk}_size_L_color_Rojo burdeos"
        self.assertIn(key, cart)
        self.assertEqual(cart[key]["variant_name"], "Talla L / Rojo burdeos")

    def test_solo_color_arma_variant_name(self):
        res = self._post({"product_id": self.prod_solo_color.pk, "color": "Negro", "qty": 1})
        self.assertEqual(res.status_code, 200)
        cart = self.client.session["cart"]
        key = f"{self.prod_solo_color.pk}_color_Negro"
        self.assertIn(key, cart)
        self.assertEqual(cart[key]["variant_name"], "Negro")


class CartAddFootwearColorwayTests(TestCase):
    """Regresión: calzado con SizeGroup + colorway por imagen (sin variant_colors).

    Verifica que el path combinado size_name + image_pk produzca la clave antigua
    ``{pid}_img_{image_pk}_size_{size}`` y el label ``{color_label} · Talla {size}``.
    Este path no tenía cobertura previa (Task 4, commit 12f7628).
    """

    def setUp(self):
        # Categoría raíz (calzado): price/margin sencilla para no enredar final_price
        self.root_cat = Category.objects.create(
            name="Calzado", slug="calzado",
            profit_margin=Decimal("0"), shipping_cost=Decimal("0"),
        )
        # SizeGroup de tallas numéricas de calzado infantil
        self.sg = SizeGroup.objects.create(
            name="Calzado 24-26", sizes=["24", "25", "26"]
        )
        # Producto con has_color_variants=True en el propio producto y SizeGroup asignado
        # variant_colors=[] → el guard "exige color" no dispara
        self.product = Product.objects.create(
            sku="RYL-SHOE-CW-1",
            name="Balenciaga Triple S",
            category=self.root_cat,
            base_price=Decimal("350"),
            size_group=self.sg,
            has_color_variants=True,
            variant_colors=[],          # NO variant_colors → no exige color por texto
        )
        # Imagen con color_label — simula un colorway "Blanco"
        # El archivo no necesita existir en disco; cart_add solo lee pk/color_label/display_order
        self.img = ProductImage.objects.create(
            product=self.product,
            image="products/shoe_blanco.jpg",
            is_cover=True,
            color_label="Blanco",
            display_order=0,
        )
        self.url = reverse("orders:cart_add")

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_footwear_size_plus_colorway_key_and_label(self):
        """El path ``size_name and img_obj`` produce clave y label correctos."""
        pid = self.product.pk
        img_pk = self.img.pk

        res = self._post({
            "product_id": pid,
            "size_name": "26",
            "image_pk": img_pk,
            "qty": 1,
        })

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()["ok"])

        cart = self.client.session["cart"]
        expected_key = f"{pid}_img_{img_pk}_size_26"
        self.assertIn(
            expected_key, cart,
            f"Clave esperada '{expected_key}' no encontrada en carrito: {list(cart.keys())}",
        )
        self.assertEqual(
            cart[expected_key]["variant_name"],
            "Blanco · Talla 26",
        )


class CheckoutConfirmValidacionesTests(TestCase):
    """checkout_confirm: rechaza teléfono inválido y viola mínimos de categoría."""

    def setUp(self):
        self.cat = Category.objects.create(
            name="Gorras Mayoreo", slug="gorras-mayoreo", min_order_qty=5,
        )
        self.product = Product.objects.create(
            sku="RYL-VAL-1", name="Gorra Mayoreo", category=self.cat,
            base_price=Decimal("100"),
        )
        self.url = reverse("orders:checkout_confirm")

    def _set_cart(self, cart_dict):
        session = self.client.session
        session["cart"] = cart_dict
        session.save()

    def _cart_2_unidades(self):
        return {
            f"{self.product.pk}_none": {
                "product_id": self.product.pk, "variant_id": None, "image_pk": None,
                "variant_name": "", "quantity": 2, "price": 100.0,
            }
        }

    def test_telefono_invalido_no_crea_order(self):
        self._set_cart(self._cart_2_unidades())
        res = self.client.post(self.url, {"nombre": "Ana", "telefono": "123"})
        self.assertRedirects(res, reverse("orders:checkout"))
        self.assertEqual(Order.objects.count(), 0)

    def test_nombre_vacio_no_crea_order(self):
        self._set_cart(self._cart_2_unidades())
        res = self.client.post(self.url, {"nombre": "", "telefono": "5512345678"})
        self.assertRedirects(res, reverse("orders:checkout"))
        self.assertEqual(Order.objects.count(), 0)

    def test_violacion_categoria_no_crea_order(self):
        # category.min_order_qty=5, carrito trae 2 → viola el mínimo total
        self._set_cart(self._cart_2_unidades())
        res = self.client.post(self.url, {"nombre": "Ana", "telefono": "5512345678"})
        # Verificar la sesión ANTES de assertRedirects: su GET interno a
        # /checkout hace session.pop('checkout_warnings') y consume la clave.
        self.assertIn("checkout_warnings", self.client.session)
        self.assertRedirects(res, reverse("orders:checkout"))
        self.assertEqual(Order.objects.count(), 0)


@override_settings(RATELIMIT_ENABLE=False)
class CostoConOverrideSubcategoriaTests(TestCase):
    """El costo registrado en ventas web debe usar effective_base_price:
    con base_price_override en la subcategoría, el precio de venta ya sale
    del override — el costo también, o la ganancia queda mal calculada."""

    def setUp(self):
        self.root = Category.objects.create(
            name="Joyeria", slug="joyeria",
            profit_margin=Decimal("100"), shipping_cost=Decimal("50"),
        )
        self.sub = Category.objects.create(
            name="Chrome Hearts", slug="chrome-hearts", parent=self.root,
            base_price_override=Decimal("300"),
        )
        # base_price individual desactualizado a propósito — el override manda
        self.product = Product.objects.create(
            sku="RYL-CH-1", name="Anillo CH", category=self.sub,
            base_price=Decimal("120"),
        )
        self.url = reverse("orders:checkout_confirm")

    def _set_cart(self, qty=1):
        session = self.client.session
        session["cart"] = {
            f"{self.product.pk}_none": {
                "product_id": self.product.pk, "variant_id": None, "image_pk": None,
                "variant_name": "", "quantity": qty,
                "price": float(self.product.final_price),
            }
        }
        session.save()

    def test_cost_snapshot_usa_effective_base_price(self):
        self._set_cart()
        res = self.client.post(self.url, {"nombre": "Ana", "telefono": "5512345678"})
        self.assertEqual(res.status_code, 302)
        item = Order.objects.get().items.get()
        # costo real = override subcategoría (300) + envío raíz (50), NO base_price viejo (120)
        self.assertEqual(item.cost_snapshot, Decimal("350"))

    def test_ganancia_fallback_sin_cost_snapshot_usa_override(self):
        order = Order.objects.create(
            order_code="TEST-GAN-1", customer_name="Ana", customer_phone="5512345678",
        )
        order.items.create(
            product=self.product, quantity=2,
            price_snapshot=self.product.final_price,   # 300 + 50 + 100 = 450
            cost_snapshot=None,
            sku_snapshot=self.product.sku, name_snapshot=self.product.name,
        )
        # ganancia = (450 − (300 + 50)) × 2 = 200 — con base_price viejo daría 560
        self.assertEqual(order.ganancia, Decimal("200"))


@override_settings(RATELIMIT_ENABLE=False)
class DescuentoCapCheckoutTests(TestCase):
    """El descuento aplicado en checkout nunca puede exceder el subtotal del
    pedido — sin tope, un código mayor al total dejaba Order.total negativo."""

    def setUp(self):
        from catalog.models import CodigoDescuento
        self.cat = Category.objects.create(name="Gorras", slug="gorras")
        self.product = Product.objects.create(
            sku="RYL-CAP-1", name="Gorra NY", category=self.cat,
            base_price=Decimal("100"),
        )
        # final_price = 100 + 0 + 100 (margen default) = 200
        self.code = CodigoDescuento.objects.create(
            codigo="MEGA", descuento=Decimal("10000"), tipo_descuento="fijo",
        )
        self.url = reverse("orders:checkout_confirm")

    def _set_cart(self):
        session = self.client.session
        session["cart"] = {
            f"{self.product.pk}_none": {
                "product_id": self.product.pk, "variant_id": None, "image_pk": None,
                "variant_name": "", "quantity": 1, "price": 200.0,
            }
        }
        session.save()

    def test_descuento_se_capea_al_subtotal(self):
        self._set_cart()
        res = self.client.post(self.url, {
            "nombre": "Ana", "telefono": "5512345678", "codigo_descuento": "MEGA",
        })
        self.assertEqual(res.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.descuento_aplicado, Decimal("200"))  # no 10000
        self.assertEqual(order.total, Decimal("0"))                 # nunca negativo
        self.code.refresh_from_db()
        self.assertEqual(self.code.usos_actuales, 1)

    def test_codigo_agotado_no_se_aplica(self):
        self.code.usos_max = 1
        self.code.usos_actuales = 1
        self.code.save()
        self._set_cart()
        self.client.post(self.url, {
            "nombre": "Ana", "telefono": "5512345678", "codigo_descuento": "MEGA",
        })
        order = Order.objects.get()
        self.assertEqual(order.descuento_aplicado, Decimal("0"))
        self.code.refresh_from_db()
        self.assertEqual(self.code.usos_actuales, 1)  # sin rebasar usos_max


@override_settings(META_PIXEL_ID='TESTPIXEL', RATELIMIT_ENABLE=False)
class MetaPixelEventosTests(TestCase):
    """Eventos estándar del píxel de Meta en el funnel de compra
    (especificaciones: facebook.com/business/help/402791146561655).
    PageView/ViewContent/Contact ya existían — esto cubre el funnel medio."""

    def setUp(self):
        self.cat = Category.objects.create(name="Gorras Px", slug="gorras-px")
        # final_price = 100 + 0 + 100 (margen default) = 200
        self.product = Product.objects.create(
            sku="RYL-PX-1", name="Gorra Pixel", category=self.cat,
            base_price=Decimal("100"),
        )

    def _set_cart(self, qty=2):
        session = self.client.session
        session["cart"] = {
            f"{self.product.pk}_none": {
                "product_id": self.product.pk, "variant_id": None, "image_pk": None,
                "variant_name": "", "quantity": qty, "price": 200.0,
            }
        }
        session.save()

    def test_cart_add_devuelve_fb_event_para_addtocart(self):
        res = self.client.post(
            reverse("orders:cart_add"),
            data=json.dumps({"product_id": self.product.pk, "qty": 1}),
            content_type="application/json",
        )
        data = res.json()
        self.assertTrue(data["ok"])
        ev = data["fb_event"]
        self.assertEqual(ev["content_ids"], ["RYL-PX-1"])
        self.assertEqual(ev["content_type"], "product")
        self.assertEqual(ev["value"], 200.0)   # precio unitario × qty 1
        self.assertEqual(ev["currency"], "MXN")

    def test_checkout_dispara_initiate_checkout(self):
        self._set_cart(qty=2)
        res = self.client.get(reverse("orders:checkout"))
        self.assertContains(res, "InitiateCheckout")
        # escapejs renderiza los guiones del SKU como - (igual que ViewContent)
        self.assertContains(res, "'RYL\\u002DPX\\u002D1'")   # content_ids
        self.assertContains(res, "num_items: 2")

    def test_confirmation_dispara_purchase_con_total(self):
        order = Order.objects.create(
            order_code="TEST-PX-1", customer_name="Ana", customer_phone="5512345678",
        )
        order.items.create(
            product=self.product, quantity=2, price_snapshot=Decimal("200"),
            sku_snapshot=self.product.sku, name_snapshot=self.product.name,
        )
        res = self.client.get(
            reverse("orders:confirmation", kwargs={"token": order.tracking_token})
        )
        self.assertContains(res, "Purchase")
        self.assertContains(res, "value: 400")     # order.total = 200 × 2
        self.assertContains(res, "'RYL\\u002DPX\\u002D1'")
        self.assertContains(res, "MXN")

    def test_sin_pixel_id_no_se_renderizan_eventos(self):
        with override_settings(META_PIXEL_ID=""):
            self._set_cart()
            res = self.client.get(reverse("orders:checkout"))
            self.assertNotContains(res, "InitiateCheckout")


class SavedCartRestoreVolumeTierTests(TestCase):
    """Al restaurar el carrito guardado en el login, el precio debe reaplicar
    el descuento por volumen — antes se restauraba a precio lleno aunque la
    cantidad calificara para el tier."""

    def setUp(self):
        from django.contrib.auth.models import User
        from catalog.models import VolumeTier
        self.cat = Category.objects.create(name="Gorras Tier", slug="gorras-tier")
        # final_price = 100 + 0 + 100 (margen default) = 200
        self.product = Product.objects.create(
            sku="RYL-TIER-1", name="Gorra Tier", category=self.cat,
            base_price=Decimal("100"),
        )
        VolumeTier.objects.create(
            category=self.cat, min_qty=10, discount_amount=Decimal("50"),
        )
        self.user = User.objects.create_user(username="cliente1", password="pass")

    def _saved_item(self, qty):
        from orders.models import SavedCartItem
        return SavedCartItem.objects.create(
            user=self.user, cart_key=f"{self.product.pk}_none",
            product=self.product, quantity=qty,
        )

    def test_restaura_precio_con_tier_por_cantidad(self):
        self._saved_item(qty=12)
        self.client.login(username="cliente1", password="pass")
        cart = self.client.session["cart"]
        self.assertEqual(cart[f"{self.product.pk}_none"]["price"], 150.0)  # 200 − 50

    def test_restaura_precio_lleno_si_no_alcanza_el_tier(self):
        self._saved_item(qty=2)
        self.client.login(username="cliente1", password="pass")
        cart = self.client.session["cart"]
        self.assertEqual(cart[f"{self.product.pk}_none"]["price"], 200.0)


class OrderPaymentModelTests(TestCase):
    """El saldo de un pedido web se calcula sobre el historial de pagos, e
    is_paid se deriva del saldo (no es un flag manual)."""

    def setUp(self):
        self.cat = Category.objects.create(name="Gorras Pay", slug="gorras-pay")
        self.product = Product.objects.create(
            sku="RYL-PAY-1", name="Gorra Pay", category=self.cat,
            base_price=Decimal("100"),
        )
        self.order = Order.objects.create(
            order_code="TEST-PAY-1", customer_name="Ana", customer_phone="5512345678",
        )
        # total = 450 × 2 = 900
        self.order.items.create(
            product=self.product, quantity=2, price_snapshot=Decimal("450"),
            sku_snapshot=self.product.sku, name_snapshot=self.product.name,
        )

    def _pago(self, monto):
        from orders.models import OrderPayment
        return OrderPayment.objects.create(
            order=self.order, fecha=timezone.localdate(), monto=Decimal(monto),
            metodo_pago="efectivo",
        )

    def test_sin_pagos_saldo_es_total(self):
        self.assertEqual(self.order.total, Decimal("900"))
        self.assertEqual(self.order.total_pagado, Decimal("0"))
        self.assertEqual(self.order.balance_due, Decimal("900"))

    def test_abono_parcial_deja_saldo(self):
        self._pago("100")
        self.assertEqual(self.order.total_pagado, Decimal("100"))
        self.assertEqual(self.order.balance_due, Decimal("800"))

    def test_recalc_paid_deriva_is_paid(self):
        self._pago("900")
        self.order.recalc_paid()
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)
        self.assertEqual(self.order.balance_due, Decimal("0"))

    def test_recalc_paid_reabre_si_falta_saldo(self):
        self.order.is_paid = True
        self.order.save(update_fields=["is_paid"])
        self._pago("100")
        self.order.recalc_paid()
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)


class PlanBackfillPaymentsTests(TestCase):
    """La lógica pura que decide qué pagos crear al migrar un pedido legacy."""

    def _plan(self, total, deposit, is_paid):
        from orders.payment_utils import plan_backfill_payments
        return plan_backfill_payments(Decimal(total), Decimal(deposit), is_paid)

    def test_liquidado_con_adelanto(self):
        self.assertEqual(
            self._plan("900", "100", True),
            [{"monto": Decimal("100"), "notas": "Adelanto migrado"},
             {"monto": Decimal("800"), "notas": "Liquidación migrada"}],
        )

    def test_no_liquidado_con_adelanto(self):
        self.assertEqual(
            self._plan("900", "100", False),
            [{"monto": Decimal("100"), "notas": "Adelanto migrado"}],
        )

    def test_liquidado_sin_adelanto(self):
        self.assertEqual(
            self._plan("900", "0", True),
            [{"monto": Decimal("900"), "notas": "Liquidación migrada"}],
        )

    def test_sin_adelanto_no_liquidado(self):
        self.assertEqual(self._plan("900", "0", False), [])

    def test_adelanto_igual_al_total_no_duplica(self):
        self.assertEqual(
            self._plan("500", "500", True),
            [{"monto": Decimal("500"), "notas": "Adelanto migrado"}],
        )


class OrderSeenAtTests(TestCase):
    def test_order_nace_sin_ver(self):
        order = Order.objects.create(
            order_code='SEEN-INIT-1', customer_name='Ana', customer_phone='5512345678',
        )
        self.assertIsNone(order.seen_at)


class NotifyNewOrderTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            order_code='NOTIFY-1', customer_name='Ana', customer_phone='5512345678',
        )
        self.order.items.create(
            product=None, quantity=1, price_snapshot=Decimal('850'),
            sku_snapshot='X', name_snapshot='X',
        )

    @patch('orders.notifications.urllib.request.urlopen')
    def test_arma_mensaje_y_url_correctos(self, mock_urlopen):
        from orders.notifications import notify_new_order
        notify_new_order(self.order)
        mock_urlopen.assert_called_once()
        request_obj = mock_urlopen.call_args[0][0]
        self.assertEqual(request_obj.full_url, f'{settings.BOT_NOTIFY_URL}/notify')
        payload = json.loads(request_obj.data.decode('utf-8'))
        self.assertEqual(payload['target'], 'orders')
        self.assertIn('NOTIFY-1', payload['message'])
        self.assertIn('Ana', payload['message'])
        self.assertIn('850', payload['message'])

    @patch('orders.notifications.urllib.request.urlopen', side_effect=OSError('conexión rechazada'))
    def test_no_propaga_si_falla_la_conexion(self, mock_urlopen):
        from orders.notifications import notify_new_order
        notify_new_order(self.order)  # no debe lanzar

    @patch('orders.notifications.threading.Thread')
    def test_async_lanza_thread_daemon_con_la_funcion_sincrona(self, mock_thread_cls):
        from orders.notifications import notify_new_order, notify_new_order_async
        notify_new_order_async(self.order)
        mock_thread_cls.assert_called_once_with(
            target=notify_new_order, args=(self.order,), daemon=True
        )
        mock_thread_cls.return_value.start.assert_called_once()
