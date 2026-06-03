import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product, ProductImage, SizeGroup
from orders.views import _cart_key


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
