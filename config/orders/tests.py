import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product, ProductImage, SizeGroup
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
