from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

import datetime

from catalog.management.commands.import_images import _pid_from_url
from catalog.modaverse import parse_specifications
from catalog.management.commands.load_productos import (
    _category_filter_ids,
    _build_existing_pids,
)
from catalog.models import (
    Category, Product, Tag, TipoArticulo, CodigoDescuento,
    PendingProduct, ProductImage,
)
from catalog.services import buscar_tipo_articulo, validar_codigo
from catalog.views import _annotate_final


class PidFromUrlTests(TestCase):
    """import_images debe mapear por productId, no por la URL completa:
    load_productos guarda supplier_url como #/proinfo/{pid} mientras el JSON
    trae #/product/{cat}?pid={pid} — formatos distintos para el mismo producto."""

    def test_extrae_pid_de_formato_proinfo(self):
        url = "https://www.modaverse.vip/#/proinfo/PR20260517222823000258"
        self.assertEqual(_pid_from_url(url), "PR20260517222823000258")

    def test_extrae_pid_de_formato_product_con_query(self):
        url = "https://www.modaverse.vip/#/product/CA20260517222823000001?pid=PR20260517222823000258"
        self.assertEqual(_pid_from_url(url), "PR20260517222823000258")

    def test_extrae_pid_numerico_proinfo(self):
        url = "https://www.modaverse.vip/#/proinfo/2056380220272803841"
        self.assertEqual(_pid_from_url(url), "2056380220272803841")

    def test_url_sin_pid_devuelve_none(self):
        self.assertIsNone(_pid_from_url("https://putianshoefactory.x.yupoo.com/albums/123"))

    def test_url_vacia_o_none_devuelve_none(self):
        self.assertIsNone(_pid_from_url(""))
        self.assertIsNone(_pid_from_url(None))


class CategoryFilterIdsTests(TestCase):
    """load_productos --category debe limitar la carga a una categoría padre
    (o subcategoría) por keyword, igual que el scraper, para no re-tocar el
    resto del catálogo."""

    TREE = [
        {
            "id": "CAP",
            "name_es": "Gorra",
            "subcategories": [
                {"id": "CAP-1", "name_es": "Dandy y Barbas"},
                {"id": "CAP-2", "name_es": "Gorro pescador"},
            ],
        },
        {
            "id": "VC",
            "name_es": "Van Cleef & Arpels",
            "subcategories": [
                {"id": "VC-1", "name_es": " réplica top 380MXN"},
                {"id": "VC-2", "name_es": " versión alta gama 280MXN"},
            ],
        },
    ]

    def test_match_de_categoria_padre_incluye_sus_subcategorias(self):
        ids = _category_filter_ids(self.TREE, ["van cleef"])
        self.assertEqual(ids, {"VC", "VC-1", "VC-2"})

    def test_match_es_case_insensitive(self):
        ids = _category_filter_ids(self.TREE, ["VAN CLEEF"])
        self.assertEqual(ids, {"VC", "VC-1", "VC-2"})

    def test_match_de_subcategoria_incluye_a_su_padre(self):
        ids = _category_filter_ids(self.TREE, ["pescador"])
        self.assertEqual(ids, {"CAP", "CAP-2"})

    def test_no_incluye_otras_categorias(self):
        ids = _category_filter_ids(self.TREE, ["van cleef"])
        self.assertNotIn("CAP", ids)
        self.assertNotIn("CAP-1", ids)

    def test_keyword_sin_coincidencia_devuelve_set_vacio(self):
        self.assertEqual(_category_filter_ids(self.TREE, ["inexistente"]), set())


class BuildExistingPidsTests(TestCase):
    """El dedup de load_productos debe ser por productId, no por la URL completa:
    un mismo producto puede estar guardado como #/proinfo/{pid} (formato nuevo) o
    #/product/{cat}?pid={pid} (formato viejo, lo que hay en el servidor sin --fix-urls).
    Comparar el string completo no detecta que es el mismo → lo duplica."""

    def test_extrae_pids_de_ambos_formatos(self):
        urls = [
            "https://www.modaverse.vip/#/proinfo/PR001",
            "https://www.modaverse.vip/#/product/CA999?pid=PR002",
        ]
        self.assertEqual(_build_existing_pids(urls), {"PR001", "PR002"})

    def test_mismo_pid_en_distinto_formato_colapsa_a_uno(self):
        urls = [
            "https://www.modaverse.vip/#/product/CA999?pid=PR777",
            "https://www.modaverse.vip/#/proinfo/PR777",
        ]
        self.assertEqual(_build_existing_pids(urls), {"PR777"})

    def test_ignora_urls_sin_pid(self):
        urls = ["https://putianshoefactory.x.yupoo.com/albums/123", "", None]
        self.assertEqual(_build_existing_pids(urls), set())


class FinalPriceCascadeTests(TestCase):
    """El precio (margen + envío) debe resolverse desde la categoría RAÍZ, no
    desde la subcategoría directa del producto. La mayoría de los productos viven
    en subcategorías; editar la ganancia/envío de la categoría padre debe
    reflejarse en sus precios. Consistente con effective_min_qty/size_group."""

    def setUp(self):
        # Raíz: margen 100, envío 0. Subcategoría con valores DISTINTOS (deben ignorarse).
        self.root = Category.objects.create(
            name='Gorra', shipping_cost=Decimal('0'), profit_margin=Decimal('100'),
        )
        self.sub = Category.objects.create(
            name='Gorro pescador', parent=self.root,
            shipping_cost=Decimal('555'), profit_margin=Decimal('999'),
        )
        self.prod = Product.objects.create(
            sku='RYL-TEST-1', name='Test', category=self.sub,
            base_price=Decimal('200'),
        )

    def test_final_price_usa_margen_de_la_raiz_no_de_la_subcategoria(self):
        # 200 base + 0 envío_raíz + 100 margen_raíz = 300 (NO 200+555+999)
        self.assertEqual(self.prod.final_price, Decimal('300'))

    def test_effective_shipping_usa_envio_de_la_raiz(self):
        self.root.shipping_cost = Decimal('280')
        self.root.save()
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.effective_shipping, Decimal('280'))
        # 200 + 280 + 100 = 580
        self.assertEqual(self.prod.final_price, Decimal('580'))

    def test_cambiar_margen_de_raiz_actualiza_final_price(self):
        self.root.profit_margin = Decimal('150')
        self.root.save()
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.final_price, Decimal('350'))  # 200+0+150

    def test_producto_en_categoria_raiz_directa(self):
        prod_raiz = Product.objects.create(
            sku='RYL-TEST-2', name='Directo', category=self.root,
            base_price=Decimal('200'),
        )
        self.assertEqual(prod_raiz.final_price, Decimal('300'))  # 200+0+100

    def test_price_override_sigue_ganando(self):
        self.prod.price_override = Decimal('111')
        self.prod.save()
        self.assertEqual(self.prod.final_price, Decimal('111'))

    def test_shipping_override_sigue_ganando(self):
        self.prod.shipping_override = Decimal('50')
        self.prod.save()
        self.assertEqual(self.prod.effective_shipping, Decimal('50'))
        self.assertEqual(self.prod.final_price, Decimal('350'))  # 200+50+100

    def test_annotate_final_coincide_con_la_propiedad_y_usa_raiz(self):
        qs = _annotate_final(Product.objects.filter(pk=self.prod.pk))
        annotated = qs.first().final_price_calc
        self.assertEqual(annotated, Decimal('300'))
        self.assertEqual(annotated, self.prod.final_price)

    def test_annotate_final_envio_override_y_raiz(self):
        self.prod.shipping_override = Decimal('50')
        self.prod.save()
        qs = _annotate_final(Product.objects.filter(pk=self.prod.pk))
        self.assertEqual(qs.first().final_price_calc, Decimal('350'))

    def test_annotate_final_respeta_price_override(self):
        # La anotación del listado debe coincidir con la propiedad cuando hay override
        self.prod.price_override = Decimal('111')
        self.prod.save()
        qs = _annotate_final(Product.objects.filter(pk=self.prod.pk))
        self.assertEqual(qs.first().final_price_calc, Decimal('111'))
        self.assertEqual(qs.first().final_price_calc, self.prod.final_price)


class ParseSpecificationsTests(TestCase):
    """productSpecificationsList de getUserPage → {'sizes': [...], 'colors': [...]}.
    Agrupa por foreignLanguageName1; valor visible = foreignLanguageName2
    (fallback a specificationsValue); dedup preservando orden."""

    def test_agrupa_talla_y_color(self):
        specs = [
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "M", "specificationsValue": "M"},
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "L", "specificationsValue": "L"},
            {"foreignLanguageName1": "Color", "foreignLanguageName2": "Rojo burdeos", "specificationsValue": "Rojo burdeos"},
            {"foreignLanguageName1": "Color", "foreignLanguageName2": "Negro", "specificationsValue": "Negro"},
        ]
        self.assertEqual(
            parse_specifications(specs),
            {"sizes": ["M", "L"], "colors": ["Rojo burdeos", "Negro"]},
        )

    def test_solo_talla(self):
        specs = [{"foreignLanguageName1": "talla", "foreignLanguageName2": "XL", "specificationsValue": "XL"}]
        self.assertEqual(parse_specifications(specs), {"sizes": ["XL"], "colors": []})

    def test_fallback_a_specificationsValue_cuando_foreign_vacio(self):
        specs = [{"foreignLanguageName1": "Color", "foreignLanguageName2": "", "specificationsValue": "Azul"}]
        self.assertEqual(parse_specifications(specs), {"sizes": [], "colors": ["Azul"]})

    def test_dedup_preserva_orden(self):
        specs = [
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "M", "specificationsValue": "M"},
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "M", "specificationsValue": "M"},
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "S", "specificationsValue": "S"},
        ]
        self.assertEqual(parse_specifications(specs)["sizes"], ["M", "S"])

    def test_ignora_dimensiones_desconocidas(self):
        specs = [{"foreignLanguageName1": "material", "foreignLanguageName2": "Algodón", "specificationsValue": "Algodón"}]
        self.assertEqual(parse_specifications(specs), {"sizes": [], "colors": []})

    def test_null_y_lista_vacia(self):
        self.assertEqual(parse_specifications(None), {"sizes": [], "colors": []})
        self.assertEqual(parse_specifications([]), {"sizes": [], "colors": []})

    def test_nombres_chinos_de_dimension(self):
        specs = [
            {"foreignLanguageName1": "尺码", "foreignLanguageName2": "M", "specificationsValue": "M"},
            {"foreignLanguageName1": "颜色", "foreignLanguageName2": "Negro", "specificationsValue": "Negro"},
        ]
        self.assertEqual(parse_specifications(specs), {"sizes": ["M"], "colors": ["Negro"]})

    def test_mezcla_dimensiones_conocidas_y_desconocidas(self):
        specs = [
            {"foreignLanguageName1": "talla", "foreignLanguageName2": "S", "specificationsValue": "S"},
            {"foreignLanguageName1": "material", "foreignLanguageName2": "Algodón", "specificationsValue": "Algodón"},
            {"foreignLanguageName1": "Color", "foreignLanguageName2": "Rojo", "specificationsValue": "Rojo"},
        ]
        self.assertEqual(parse_specifications(specs), {"sizes": ["S"], "colors": ["Rojo"]})


class VariantColorsFieldTests(TestCase):
    """Product.variant_colors: lista JSON, default []."""

    def test_default_es_lista_vacia(self):
        cat = Category.objects.create(name="Ropa", slug="ropa")
        p = Product.objects.create(sku="RYL-T-1", name="Camiseta", category=cat, base_price=Decimal("100"))
        self.assertEqual(p.variant_colors, [])

    def test_guarda_lista_de_colores(self):
        cat = Category.objects.create(name="Ropa2", slug="ropa2")
        p = Product.objects.create(
            sku="RYL-T-2", name="Camiseta", category=cat, base_price=Decimal("100"),
            variant_colors=["Rojo burdeos", "Negro"],
        )
        p.refresh_from_db()
        self.assertEqual(p.variant_colors, ["Rojo burdeos", "Negro"])


from catalog.management.commands.load_productos import get_or_create_size_group
from catalog.models import SizeGroup


class GetOrCreateSizeGroupTests(TestCase):
    """Find-or-create de SizeGroup por conjunto de tallas (dedup en re-run)."""

    def test_crea_grupo_nuevo(self):
        sg = get_or_create_size_group(["M", "L", "XL"])
        self.assertEqual(sg.sizes, ["M", "L", "XL"])
        self.assertIsNone(sg.conversion_table)

    def test_re_run_no_duplica(self):
        a = get_or_create_size_group(["M", "L", "XL"])
        b = get_or_create_size_group(["M", "L", "XL"])
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(SizeGroup.objects.count(), 1)

    def test_conjuntos_distintos_son_grupos_distintos(self):
        a = get_or_create_size_group(["M", "L"])
        b = get_or_create_size_group(["S", "M", "L"])
        self.assertNotEqual(a.pk, b.pk)

    def test_dedup_de_tallas_repetidas(self):
        sg = get_or_create_size_group(["M", "M", "L"])
        self.assertEqual(sg.sizes, ["M", "L"])


from catalog.management.commands.load_productos import Command as LoadCommand


class ApplyVariantsTests(TestCase):
    """_apply_variants asigna size_group (find-or-create) y variant_colors, idempotente."""

    def setUp(self):
        self.cat = Category.objects.create(name="Calidad 1:1", slug="calidad-11")
        self.product = Product.objects.create(
            sku="RYL-GEN-900", name="Jersey", category=self.cat, base_price=Decimal("250"),
        )

    def test_asigna_tallas_y_colores(self):
        LoadCommand()._apply_variants(self.product, ["M", "L", "XL"], ["Rojo", "Negro"])
        self.product.refresh_from_db()
        self.assertIsNotNone(self.product.size_group)
        self.assertEqual(self.product.size_group.sizes, ["M", "L", "XL"])
        self.assertEqual(self.product.variant_colors, ["Rojo", "Negro"])

    def test_solo_colores_no_toca_size_group(self):
        LoadCommand()._apply_variants(self.product, [], ["Azul"])
        self.product.refresh_from_db()
        self.assertIsNone(self.product.size_group)
        self.assertEqual(self.product.variant_colors, ["Azul"])

    def test_idempotente_no_duplica_size_group(self):
        cmd = LoadCommand()
        cmd._apply_variants(self.product, ["M", "L"], ["Rojo"])
        cmd._apply_variants(self.product, ["M", "L"], ["Rojo"])
        self.assertEqual(SizeGroup.objects.count(), 1)


from django.core.management import call_command
from unittest import mock


class LoadProductosEnriqueceExistentesTests(TestCase):
    """Re-run con --category aplica sizes/colors a productos YA existentes (no solo skip)."""

    def test_enriquece_producto_existente_en_scope(self):
        cat = Category.objects.create(name="Calidad 1:1", slug="calidad-11")
        product = Product.objects.create(
            sku="RYL-C11-001", name="Jersey viejo", category=cat,
            base_price=Decimal("250"),
            supplier_url="https://www.modaverse.vip/#/proinfo/PR123",
        )
        self.assertIsNone(product.size_group)
        self.assertEqual(product.variant_colors, [])

        fake_data = {
            'products': [{
                "sku": "PR123", "name": "Jersey viejo", "category_id": "C1",
                "category": "Calidad 1:1",
                "url": "https://www.modaverse.vip/#/product/C1?pid=PR123",
                "images": [], "sizes": ["M", "L"], "colors": ["Rojo burdeos"],
            }],
            'categories': [{"id": "C1", "name_es": "Calidad 1:1", "subcategories": []}],
        }
        with mock.patch.object(LoadCommand, '_read_modaverse_json', return_value=fake_data):
            call_command('load_productos', '--category', 'Calidad 1:1', '--no-images')

        product.refresh_from_db()
        self.assertIsNotNone(product.size_group)
        self.assertEqual(product.size_group.sizes, ["M", "L"])
        self.assertEqual(product.variant_colors, ["Rojo burdeos"])

    def test_no_enriquece_productos_fuera_del_category(self):
        """Productos en categorías fuera del filtro --category no deben ser tocados."""
        # In-scope: Calidad 1:1
        cat_scope = Category.objects.create(name="Calidad 1:1", slug="calidad-11")
        product_scope = Product.objects.create(
            sku="RYL-C11-001", name="Jersey viejo", category=cat_scope,
            base_price=Decimal("250"),
            supplier_url="https://www.modaverse.vip/#/proinfo/PR123",
        )
        # Out-of-scope: Gorra
        cat_out = Category.objects.create(name="Gorra", slug="gorra")
        product_out = Product.objects.create(
            sku="RYL-CAP-001", name="Gorra exclusiva", category=cat_out,
            base_price=Decimal("150"),
            supplier_url="https://www.modaverse.vip/#/proinfo/PR999",
        )
        self.assertIsNone(product_out.size_group)
        self.assertEqual(product_out.variant_colors, [])

        fake_data = {
            'products': [
                {
                    "sku": "PR123", "name": "Jersey viejo", "category_id": "C1",
                    "category": "Calidad 1:1",
                    "url": "https://www.modaverse.vip/#/product/C1?pid=PR123",
                    "images": [], "sizes": ["M", "L"], "colors": ["Rojo burdeos"],
                },
                {
                    "sku": "PR999", "name": "Gorra exclusiva", "category_id": "C2",
                    "category": "Gorra",
                    "url": "https://www.modaverse.vip/#/product/C2?pid=PR999",
                    "images": [], "sizes": ["S", "M", "L"], "colors": ["Negro"],
                },
            ],
            'categories': [
                {"id": "C1", "name_es": "Calidad 1:1", "subcategories": []},
                {"id": "C2", "name_es": "Gorra", "subcategories": []},
            ],
        }
        with mock.patch.object(LoadCommand, '_read_modaverse_json', return_value=fake_data):
            call_command('load_productos', '--category', 'Calidad 1:1', '--no-images')

        # In-scope product must be enriched
        product_scope.refresh_from_db()
        self.assertIsNotNone(product_scope.size_group)
        self.assertEqual(product_scope.size_group.sizes, ["M", "L"])
        self.assertEqual(product_scope.variant_colors, ["Rojo burdeos"])

        # Out-of-scope product must NOT be touched
        product_out.refresh_from_db()
        self.assertIsNone(product_out.size_group)
        self.assertEqual(product_out.variant_colors, [])

    def test_reclasifica_producto_cuya_subcategoria_cambio_en_json(self):
        """Con --category, si el JSON movió un producto a otra subcat del mismo scope,
        load_productos actualiza la categoría Django sin crear un duplicado."""
        root = Category.objects.create(name="Gorra", slug="gorra")
        sub1 = Category.objects.create(name="Dandy y Barbas", slug="dandy-y-barbas", parent=root)
        sub2 = Category.objects.create(name="Sombrero plano", slug="sombrero-plano", parent=root)

        product = Product.objects.create(
            sku="RYL-CAP-001", name="Cap viejo", category=sub1,
            base_price=Decimal("150"),
            supplier_url="https://www.modaverse.vip/#/proinfo/PR001",
        )

        fake_data = {
            'products': [
                {"sku": "PR001", "name": "Cap viejo", "category_id": "S2",
                 "category": "Sombrero plano", "images": [], "sizes": [], "colors": []},
            ],
            'categories': [{"id": "CAP", "name_es": "Gorra", "subcategories": [
                {"id": "S1", "name_es": "Dandy y Barbas"},
                {"id": "S2", "name_es": "Sombrero plano"},
            ]}],
        }
        with mock.patch.object(LoadCommand, '_read_modaverse_json', return_value=fake_data):
            call_command('load_productos', '--category', 'gorra', '--no-images')

        product.refresh_from_db()
        self.assertEqual(product.category_id, sub2.pk)
        self.assertEqual(Product.objects.filter(supplier_url__icontains='PR001').count(), 1)

    def test_mueve_producto_de_categoria_ajena_al_scope(self):
        """Con --category gorra, un producto que estaba en otra cat. raíz
        es movido a la subcat gorras correcta sin crear duplicado."""
        root = Category.objects.create(name="Gorra", slug="gorra")
        sub = Category.objects.create(name="Gorras Planas", slug="gorras-planas", parent=root)
        other_root = Category.objects.create(name="Camisetas", slug="camisetas")
        other_sub = Category.objects.create(name="Jordan", slug="jordan", parent=other_root)
        product = Product.objects.create(
            sku="RYL-GEN-001", name="Gorra NY",
            category=other_sub,
            base_price=Decimal("150"),
            supplier_url="https://www.modaverse.vip/#/proinfo/PR001",
        )
        fake_data = {
            'products': [
                {"sku": "PR001", "name": "Gorra NY", "category_id": "S1",
                 "category": "Gorras Planas", "images": [], "sizes": [], "colors": []},
            ],
            'categories': [
                {"id": "CAP", "name_es": "Gorra", "subcategories": [
                    {"id": "S1", "name_es": "Gorras Planas"},
                ]},
                {"id": "CAM", "name_es": "Camisetas", "subcategories": [
                    {"id": "S2", "name_es": "Jordan"},
                ]},
            ],
        }
        with mock.patch.object(LoadCommand, '_read_modaverse_json', return_value=fake_data):
            call_command('load_productos', '--category', 'gorra', '--no-images')
        product.refresh_from_db()
        self.assertEqual(product.category_id, sub.pk)
        self.assertEqual(Product.objects.filter(supplier_url__icontains='PR001').count(), 1)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ProductDetailColorContextTests(TestCase):
    """product_detail pasa variant_colors al contexto y pinta los chips."""

    def setUp(self):
        self.cat = Category.objects.create(name="Calidad 1:1", slug="calidad-11")
        self.sg = SizeGroup.objects.create(name="Ropa · M·L", sizes=["M", "L"])
        self.prod = Product.objects.create(
            sku="RYL-GEN-10", name="Jersey", category=self.cat, base_price=Decimal("250"),
            size_group=self.sg, variant_colors=["Rojo burdeos", "Negro"], is_active=True,
        )

    def test_contexto_incluye_variant_colors(self):
        res = self.client.get(reverse("catalog:detail", args=[self.prod.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["variant_colors"], ["Rojo burdeos", "Negro"])

    def test_render_muestra_chips_de_color(self):
        res = self.client.get(reverse("catalog:detail", args=[self.prod.pk]))
        self.assertContains(res, 'class="color-chip"')
        self.assertContains(res, "Rojo burdeos")
        self.assertContains(res, "Negro")


# ── reconcile_catalog ────────────────────────────────────────────────────────

class ReconcileCatalogTests(TestCase):
    """Management command reconcile_catalog.

    JSON inyectado vía mock de read_modaverse_json; nunca toca el JSON real de 24k.
    Estructura del JSON de prueba:
      { 'categories': [...], 'products': [{'sku': pid, 'category_id': ..., ...}] }
    """

    TREE = [
        {
            'id': 'CAP',
            'name_es': 'Gorras',
            'subcategories': [
                {'id': 'CAP-1', 'name_es': 'Dandy y Barbas'},
            ],
        }
    ]

    def setUp(self):
        self.root = Category.objects.create(name='Gorras', slug='gorras')
        self.sub = Category.objects.create(
            name='Dandy y Barbas', slug='dandy-y-barbas', parent=self.root
        )

    def _make_product(self, pid, *, category=None, is_active=True,
                      auto_deactivated=False, yupoo=False, no_url=False):
        cat = category or self.sub
        if no_url:
            url = ''
        elif yupoo:
            url = f'https://putianshoefactory.x.yupoo.com/albums/{pid}'
        else:
            url = f'https://www.modaverse.vip/#/proinfo/{pid}'
        return Product.objects.create(
            sku=f'RYL-TEST-{pid}',
            name=f'Product {pid}',
            category=cat,
            base_price=Decimal('200'),
            supplier_url=url,
            is_active=is_active,
            auto_deactivated=auto_deactivated,
        )

    def _json(self, pids, *, cat_id='CAP-1', tree=None):
        return {
            'categories': tree or self.TREE,
            'products': [
                {'sku': pid, 'category_id': cat_id, 'name_es': f'Product {pid}'}
                for pid in pids
            ],
        }

    def _call(self, *args, json_data=None, **kwargs):
        stdout, stderr = StringIO(), StringIO()
        data = json_data if json_data is not None else self._json([])
        with patch(
            'catalog.management.commands.reconcile_catalog.read_modaverse_json',
            return_value=data,
        ):
            call_command(
                'reconcile_catalog', *args,
                stdout=stdout, stderr=stderr,
                **kwargs,
            )
        return stdout.getvalue(), stderr.getvalue()

    def test_desactiva_producto_cuyo_pid_no_esta_en_json(self):
        """Producto activo cuyo pid no aparece en el JSON → is_active=False, auto_deactivated=True.
        3 productos permanecen en JSON → 1/4 = 25 % < 30 % (threshold no dispara)."""
        p = self._make_product('PID001')
        for pid in ('PID002', 'PID003', 'PID004'):
            self._make_product(pid)
        self._call(json_data=self._json(['PID002', 'PID003', 'PID004']))
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertTrue(p.auto_deactivated)

    def test_reactiva_producto_auto_deactivated_que_reaparece(self):
        """Producto con auto_deactivated=True cuyo pid vuelve al JSON → is_active=True, auto_deactivated=False."""
        p = self._make_product('PID001', is_active=False, auto_deactivated=True)
        self._call(json_data=self._json(['PID001']))
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertFalse(p.auto_deactivated)

    def test_no_reactiva_producto_desactivado_manualmente(self):
        """Producto con is_active=False y auto_deactivated=False (manual) → no se reactiva."""
        p = self._make_product('PID001', is_active=False, auto_deactivated=False)
        self._call(json_data=self._json(['PID001']))
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertFalse(p.auto_deactivated)

    def test_no_desactiva_producto_movido_de_categoria(self):
        """Producto cuyo pid sigue en el JSON (en otra cat) no se desactiva."""
        p = self._make_product('PID001')
        json_data = {
            'categories': self.TREE + [
                {'id': 'OTH', 'name_es': 'Otra', 'subcategories': [
                    {'id': 'OTH-1', 'name_es': 'Sub Otra'}
                ]}
            ],
            'products': [
                {'sku': 'PID001', 'category_id': 'OTH-1', 'name_es': 'Product PID001'},
                {'sku': 'PID002', 'category_id': 'CAP-1', 'name_es': 'Product PID002'},
            ],
        }
        self._call(json_data=json_data)
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertFalse(p.auto_deactivated)

    def test_zero_guard_aborta_cuando_json_scope_es_vacio(self):
        """JSON con 0 productos → zero-guard aborta, no modifica nada."""
        p = self._make_product('PID001')
        _, stderr = self._call(json_data={'categories': self.TREE, 'products': []})
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertFalse(p.auto_deactivated)
        self.assertIn('zero', stderr.lower())

    def test_umbral_aborta_cuando_bajas_superan_el_porcentaje(self):
        """4 activos, 2 removidos = 50 % > 30 % → aborta sin --force."""
        for i in range(4):
            self._make_product(f'PID{i:03}')
        self._call(json_data=self._json(['PID000', 'PID001']))
        self.assertEqual(
            Product.objects.filter(is_active=True, sku__startswith='RYL-TEST-').count(), 4
        )

    def test_force_bypasses_umbral(self):
        """Con --force las bajas se aplican aunque superen el umbral."""
        for i in range(4):
            self._make_product(f'PID{i:03}')
        self._call('--force', json_data=self._json(['PID000', 'PID001']))
        self.assertEqual(
            Product.objects.filter(
                is_active=False, auto_deactivated=True, sku__startswith='RYL-TEST-'
            ).count(), 2
        )

    def test_dry_run_no_escribe_ninguna_baja(self):
        """--dry-run imprime el plan pero no escribe nada en BD.
        3 productos permanecen → 1/4 = 25 % < 30 % para que llegue al dry-run."""
        p = self._make_product('PID001')
        for pid in ('PID002', 'PID003', 'PID004'):
            self._make_product(pid)
        stdout, _ = self._call('--dry-run', json_data=self._json(['PID002', 'PID003', 'PID004']))
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertFalse(p.auto_deactivated)
        self.assertIn('dry-run', stdout.lower())

    def test_category_solo_toca_productos_del_scope(self):
        """--category gorras desactiva en Gorras pero no toca productos de Ropa.
        3 productos de Gorras permanecen → 1/4 = 25 % < 30 % (threshold no dispara)."""
        root_ropa = Category.objects.create(name='Ropa', slug='ropa')
        sub_ropa = Category.objects.create(name='Camisetas', slug='camisetas', parent=root_ropa)

        p_gorras = self._make_product('PID001', category=self.sub)   # en scope, removido
        for pid in ('PID002', 'PID003', 'PID004'):                    # en scope, permanecen
            self._make_product(pid, category=self.sub)
        p_ropa = self._make_product('PID005', category=sub_ropa)      # fuera de scope

        # PID002-PID004 en CAP-1 → zero-guard OK, 1/4 = 25 % < 30 %
        json_data = self._json(['PID002', 'PID003', 'PID004'])
        self._call('--category', 'gorras', json_data=json_data)

        p_gorras.refresh_from_db()
        p_ropa.refresh_from_db()
        self.assertFalse(p_gorras.is_active)
        self.assertTrue(p_gorras.auto_deactivated)
        self.assertTrue(p_ropa.is_active)

    def test_category_scope_incluye_subcats_django_sin_id_en_json_tree(self):
        """--category scope usa jerarquía Django, no el árbol JSON.

        Sub-categoría Django bajo 'gorras' que no tiene ID en el categories tree
        del JSON (cat legacy) → sus productos SÍ están en scope y se desactivan
        si su pid no aparece en el JSON.
        3 productos sobreviven → 1/4 = 25 % < 30 % (umbral no dispara).
        """
        sub_legacy = Category.objects.create(
            name='gorra normal', slug='gorra-normal', parent=self.root
        )
        p_legacy = self._make_product('PID010', category=sub_legacy)
        for pid in ('PID002', 'PID003', 'PID004'):
            self._make_product(pid, category=self.sub)

        self._call('--category', 'gorras', json_data=self._json(['PID002', 'PID003', 'PID004']))

        p_legacy.refresh_from_db()
        self.assertFalse(p_legacy.is_active)
        self.assertTrue(p_legacy.auto_deactivated)

    def test_prune_elimina_auto_desactivados_en_scope(self):
        """--prune borra permanentemente los auto_deactivated=True del scope."""
        p_dead = self._make_product('PID001', is_active=False, auto_deactivated=True)
        p_alive = self._make_product('PID002')
        self._call('--prune', json_data=self._json([]))
        self.assertFalse(Product.objects.filter(pk=p_dead.pk).exists())
        self.assertTrue(Product.objects.filter(pk=p_alive.pk).exists())

    def test_prune_dry_run_no_borra(self):
        """--prune --dry-run muestra el plan sin borrar nada."""
        p_dead = self._make_product('PID001', is_active=False, auto_deactivated=True)
        stdout, _ = self._call('--prune', '--dry-run', json_data=self._json([]))
        self.assertTrue(Product.objects.filter(pk=p_dead.pk).exists())
        self.assertIn('dry-run', stdout.lower())

    def test_prune_category_solo_borra_en_scope(self):
        """--prune --category gorras no toca productos auto-desactivados de otra categoría."""
        root_ropa = Category.objects.create(name='Ropa', slug='ropa')
        sub_ropa = Category.objects.create(name='Camisetas', slug='camisetas', parent=root_ropa)
        p_gorras_dead = self._make_product('PID001', is_active=False, auto_deactivated=True)
        p_ropa_dead = self._make_product('PID002', category=sub_ropa,
                                         is_active=False, auto_deactivated=True)
        self._call('--prune', '--category', 'gorras', json_data=self._json([]))
        self.assertFalse(Product.objects.filter(pk=p_gorras_dead.pk).exists())
        self.assertTrue(Product.objects.filter(pk=p_ropa_dead.pk).exists())

    def test_no_toca_productos_sin_url_modaverse(self):
        """Productos yupoo y manuales (sin URL modaverse) no son afectados."""
        p_yupoo = self._make_product('PID001', yupoo=True)
        p_manual = self._make_product('PID002', no_url=True)
        self._call(json_data=self._json(['PID003']))
        p_yupoo.refresh_from_db()
        p_manual.refresh_from_db()
        self.assertTrue(p_yupoo.is_active)
        self.assertTrue(p_manual.is_active)


class ReconcileYupooTests(TestCase):
    """reconcile_yupoo: soft-delete/reactivación de Calzado (Yupoo) por supplier_url exacta
    (Yupoo no expone yn_launch — la única señal de baja es ausencia en un scrape fresco)."""

    def setUp(self):
        self.root = Category.objects.create(name='Calzado', slug='calzado')

    def _make_product(self, url_id, *, is_active=True, auto_deactivated=False, modaverse=False):
        url = (
            f'https://www.modaverse.vip/#/proinfo/{url_id}' if modaverse
            else f'https://putianshoefactory.x.yupoo.com/albums/{url_id}'
        )
        return Product.objects.create(
            sku=f'RYL-TEST-{url_id}',
            name=f'Tenis {url_id}',
            category=self.root,
            base_price=Decimal('500'),
            supplier_url=url,
            is_active=is_active,
            auto_deactivated=auto_deactivated,
        )

    def _json(self, url_ids):
        return {
            'products': [
                {'url': f'https://putianshoefactory.x.yupoo.com/albums/{u}'}
                for u in url_ids
            ],
        }

    def _call(self, *args, json_data=None, **kwargs):
        stdout, stderr = StringIO(), StringIO()
        data = json_data if json_data is not None else self._json([])
        with patch(
            'catalog.management.commands.reconcile_yupoo.read_yupoo_json',
            return_value=data,
        ):
            call_command('reconcile_yupoo', *args, stdout=stdout, stderr=stderr, **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    def test_desactiva_producto_cuya_url_no_esta_en_scrape_fresco(self):
        """3 sobreviven → 1/4 = 25 % < 30 % (threshold no dispara)."""
        p = self._make_product('A001')
        for uid in ('A002', 'A003', 'A004'):
            self._make_product(uid)
        self._call(json_data=self._json(['A002', 'A003', 'A004']))
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertTrue(p.auto_deactivated)

    def test_reactiva_producto_auto_deactivated_que_reaparece(self):
        p = self._make_product('A001', is_active=False, auto_deactivated=True)
        self._call(json_data=self._json(['A001']))
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertFalse(p.auto_deactivated)

    def test_no_reactiva_producto_desactivado_manualmente(self):
        p = self._make_product('A001', is_active=False, auto_deactivated=False)
        self._call(json_data=self._json(['A001']))
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertFalse(p.auto_deactivated)

    def test_zero_guard_aborta_cuando_json_esta_vacio(self):
        p = self._make_product('A001')
        _, stderr = self._call(json_data={'products': []})
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertIn('zero', stderr.lower())

    def test_umbral_aborta_cuando_bajas_superan_el_porcentaje(self):
        for i in range(4):
            self._make_product(f'A{i:03}')
        self._call(json_data=self._json(['A000', 'A001']))
        self.assertEqual(
            Product.objects.filter(is_active=True, sku__startswith='RYL-TEST-').count(), 4
        )

    def test_force_bypasses_umbral(self):
        for i in range(4):
            self._make_product(f'A{i:03}')
        self._call('--force', json_data=self._json(['A000', 'A001']))
        self.assertEqual(
            Product.objects.filter(
                is_active=False, auto_deactivated=True, sku__startswith='RYL-TEST-'
            ).count(), 2
        )

    def test_dry_run_no_escribe_ninguna_baja(self):
        p = self._make_product('A001')
        for uid in ('A002', 'A003', 'A004'):
            self._make_product(uid)
        stdout, _ = self._call('--dry-run', json_data=self._json(['A002', 'A003', 'A004']))
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertIn('dry-run', stdout.lower())

    def test_no_toca_productos_modaverse(self):
        """Productos modaverse (sin 'yupoo' en supplier_url) no son afectados."""
        p_modaverse = self._make_product('A001', modaverse=True)
        self._call(json_data=self._json(['A999']))
        p_modaverse.refresh_from_db()
        self.assertTrue(p_modaverse.is_active)

    def test_prune_elimina_auto_desactivados(self):
        p_dead = self._make_product('A001', is_active=False, auto_deactivated=True)
        p_alive = self._make_product('A002')
        self._call('--prune', json_data=self._json([]))
        self.assertFalse(Product.objects.filter(pk=p_dead.pk).exists())
        self.assertTrue(Product.objects.filter(pk=p_alive.pk).exists())

    def test_prune_dry_run_no_borra(self):
        p_dead = self._make_product('A001', is_active=False, auto_deactivated=True)
        stdout, _ = self._call('--prune', '--dry-run', json_data=self._json([]))
        self.assertTrue(Product.objects.filter(pk=p_dead.pk).exists())
        self.assertIn('dry-run', stdout.lower())


# ── Fix C: Product.modaverse_name ────────────────────────────────────────────

from django.core.management import BaseCommand as DjBaseCmd
from catalog.management.commands.load_productos import Command as LoadCmd, _clean_name


class ProductModaverseNameFieldTest(TestCase):
    """Product.modaverse_name existe, default '', no afecta creación normal."""

    def setUp(self):
        self.cat = Category.objects.create(name='MvTest', slug='mvtest')

    def test_field_default_empty(self):
        p = Product.objects.create(
            sku='RYL-MV-001', name='Test', category=self.cat,
            base_price=Decimal('100'),
        )
        self.assertEqual(p.modaverse_name, '')

    def test_field_persisted(self):
        p = Product.objects.create(
            sku='RYL-MV-002', name='Test', category=self.cat,
            base_price=Decimal('100'), modaverse_name='9026白金',
        )
        p.refresh_from_db()
        self.assertEqual(p.modaverse_name, '9026白金')


class CreateProductModaverseNameTest(TestCase):
    """_create_product crea PendingProduct con modaverse_name (productos nuevos van a cola de revisión)."""

    def setUp(self):
        from catalog.models import PendingProduct
        self.PendingProduct = PendingProduct
        self.cat = Category.objects.create(name='MvTest2', slug='mvtest2')
        self.tag = Tag.objects.create(name='mvtest-tag')
        from io import StringIO
        self.cmd = LoadCmd()
        self.cmd.stdout = StringIO()
        self.cmd.style = DjBaseCmd().style

    def test_crea_pendiente_con_modaverse_name(self):
        created = self.cmd._create_product(
            sku='RYL-MV-010',
            name='9026',
            category=self.cat,
            base_price=Decimal('500'),
            description='',
            supplier_url='https://www.modaverse.vip/#/proinfo/pid123',
            images=[],
            tag=self.tag,
            no_images=True,
            modaverse_name='9026白金',
        )
        self.assertTrue(created)
        p = self.PendingProduct.objects.get(supplier_url__contains='pid123')
        self.assertEqual(p.modaverse_name, '9026白金')
        self.assertEqual(p.raw_data['sku'], 'RYL-MV-010')

    def test_crea_pendiente_sin_modaverse_name_usa_nombre(self):
        """Sin modaverse_name explícito, el pending usa 'name' como fallback."""
        created = self.cmd._create_product(
            sku='RYL-MV-011',
            name='Jordan 1',
            category=self.cat,
            base_price=Decimal('500'),
            description='',
            supplier_url='https://www.modaverse.vip/#/proinfo/pid124',
            images=[],
            tag=self.tag,
            no_images=True,
        )
        self.assertTrue(created)
        p = self.PendingProduct.objects.get(supplier_url__contains='pid124')
        self.assertEqual(p.display_name, 'Jordan 1')


class LoadModaverseRawNameTest(TestCase):
    """_load_modaverse encola productos nuevos como PendingProduct con nombre crudo."""

    CATEGORIES = [{'id': '__default__', 'name_es': 'General', 'subcategories': []}]

    def setUp(self):
        from catalog.models import PendingProduct
        self.PendingProduct = PendingProduct
        Category.objects.create(name='General', slug='general')
        from io import StringIO
        self.cmd = LoadCmd()
        self.cmd.stdout = StringIO()
        self.cmd.style = DjBaseCmd().style

    def _tag(self):
        return Tag.objects.get_or_create(name='nuevo')[0]

    def _run(self, products):
        data = {'products': products, 'categories': self.CATEGORIES}
        with patch.object(self.cmd, '_read_modaverse_json', return_value=data):
            self.cmd._load_modaverse(
                tag_nuevo=self._tag(), no_images=True,
                existing_urls=set(), category=None,
            )

    def test_display_name_sin_chino_modaverse_name_con_raw(self):
        """display_name recibe el nombre limpio (sin CJK); modaverse_name guarda el original."""
        raw_name = '9026 白金'
        clean = _clean_name(raw_name)
        self.assertEqual(clean, '9026')  # verifica que _clean_name eliminó el CJK

        self._run([{
            'name': raw_name, 'sku': 'MTEST001', 'price_mxn': 500,
            'category': 'General', 'category_id': '__default__',
            'images': [], 'sizes': [], 'colors': [],
        }])

        p = self.PendingProduct.objects.filter(supplier_url__contains='MTEST001').first()
        self.assertIsNotNone(p, 'PendingProduct no creado por _load_modaverse')
        self.assertEqual(p.display_name,   clean)     # nombre limpio, sin chino
        self.assertEqual(p.modaverse_name, raw_name)  # original preservado

    def test_nombre_completamente_chino_fallback_a_categoria(self):
        """Si el productName es 100% chino, display_name cae al nombre de categoría."""
        raw_name = '白金帽'
        self.assertEqual(_clean_name(raw_name), '')  # queda vacío tras eliminar CJK

        self._run([{
            'name': raw_name, 'sku': 'MTEST003', 'price_mxn': 500,
            'category': 'General', 'category_id': '__default__',
            'images': [], 'sizes': [], 'colors': [],
        }])

        p = self.PendingProduct.objects.filter(supplier_url__contains='MTEST003').first()
        self.assertIsNotNone(p)
        self.assertFalse(bool(__import__('re').search(r'[一-鿿]', p.display_name)),
                         'display_name no debe contener caracteres chinos')
        self.assertEqual(p.modaverse_name, raw_name)

    def test_raw_name_igual_cuando_clean_no_modifica(self):
        """'HELL3004' sin CJK: display_name y modaverse_name iguales al raw."""
        raw_name = 'HELL3004'
        self.assertEqual(_clean_name(raw_name), raw_name)

        self._run([{
            'name': raw_name, 'sku': 'MTEST002', 'price_mxn': 500,
            'category': 'General', 'category_id': '__default__',
            'images': [], 'sizes': [], 'colors': [],
        }])

        p = self.PendingProduct.objects.filter(supplier_url__contains='MTEST002').first()
        self.assertIsNotNone(p)
        self.assertEqual(p.display_name,   raw_name)
        self.assertEqual(p.modaverse_name, raw_name)


class AutoSyncCatalogScheduleTests(TestCase):
    """Verifica el mapa slot→categoría rotativa + Gorra siempre-activa, sin tocar BD ni load_productos."""

    def _kw(self, slot):
        from catalog.management.commands.auto_sync_catalog import category_for_slot
        entry = category_for_slot(slot)
        return entry[0] if entry else None

    def test_gorra_es_siempre_activa_fuera_de_la_rotacion(self):
        from catalog.management.commands.auto_sync_catalog import _ALWAYS
        self.assertIn('gorra', _ALWAYS[0])
        self.assertEqual(_ALWAYS[1], 'Gorra')

    def test_gorra_no_esta_en_el_schedule_rotativo(self):
        from catalog.management.commands.auto_sync_catalog import _SCHEDULE
        labels = [entry[1] for entry in _SCHEDULE.values()]
        self.assertNotIn('Gorra', labels)

    def test_todos_los_slots_rotativos_tienen_entrada(self):
        from catalog.management.commands.auto_sync_catalog import category_for_slot
        for slot in range(8):
            self.assertIsNotNone(category_for_slot(slot), f'Falta slot {slot}')

    def test_slot_0_es_deportiva(self):
        self.assertIn('deportiva', self._kw(0))

    def test_slot_1_es_1a1(self):
        self.assertIn('1:1', self._kw(1))

    def test_slot_2_es_g5(self):
        self.assertIn('g5', self._kw(2))

    def test_slot_3_es_calzado(self):
        self.assertIn('calzado', self._kw(3))

    def test_slot_4_es_van_cleef(self):
        self.assertIn('van cleef', self._kw(4))

    def test_slot_5_es_reloj(self):
        self.assertIn('reloj', self._kw(5))

    def test_slot_6_es_chrome_hearts(self):
        self.assertIn('chrome hearts', self._kw(6))

    def test_slot_7_es_bolsos(self):
        self.assertIn('bolsos', self._kw(7))

    def test_electronica_ya_no_esta_en_el_schedule(self):
        from catalog.management.commands.auto_sync_catalog import _SCHEDULE
        keywords_planas = [kw for entry in _SCHEDULE.values() for kw in entry[0]]
        self.assertNotIn('auricular', keywords_planas)

    def test_todos_los_slots_excepto_calzado_tienen_images_hint(self):
        from catalog.management.commands.auto_sync_catalog import _SCHEDULE
        for slot, entry in _SCHEDULE.items():
            label, images_hint = entry[1], entry[3]
            if label == 'Calzado':
                self.assertIsNone(images_hint, 'Calzado usa download_yupoo_images aparte')
            else:
                self.assertIsNotNone(images_hint, f'Falta images_hint en slot {slot} ({label})')

    def test_images_hint_matchea_una_choice_valida_de_import_images(self):
        from catalog.management.commands.auto_sync_catalog import _SCHEDULE, _ALWAYS
        from catalog.management.commands.import_images import _CATEGORY_SLUG_HINT
        for slot, entry in {**_SCHEDULE, 'always': _ALWAYS}.items():
            images_hint = entry[3]
            if images_hint is not None:
                self.assertIn(images_hint, _CATEGORY_SLUG_HINT, f'slot {slot}: {images_hint!r} no es --only válido')

    def test_slot_invalido_retorna_none(self):
        from catalog.management.commands.auto_sync_catalog import category_for_slot
        self.assertIsNone(category_for_slot(8))

    def test_slot_for_date_avanza_cada_2_dias(self):
        from datetime import date
        from catalog.management.commands.auto_sync_catalog import slot_for_date
        d0 = date(2026, 1, 1)
        s0 = slot_for_date(d0)
        # mismo día y el día siguiente caen en el mismo slot (bloque de 2 días)
        self.assertEqual(slot_for_date(d0), s0)
        # 2 días después ya avanzó al siguiente slot del ciclo
        from datetime import timedelta
        self.assertEqual(slot_for_date(d0 + timedelta(days=2)), (s0 + 1) % 8)


class PendingProductApproveTests(TestCase):
    def setUp(self):
        from catalog.models import PendingProduct
        self.cat = Category.objects.create(
            name='Cat Prueba', slug='cat-prueba',
            shipping_cost=50, profit_margin=100,
        )
        self.pending = PendingProduct.objects.create(
            supplier_url='https://modaverse.vip/#/proinfo/TEST001',
            display_name='Gorra Prueba',
            modaverse_name='Gorra Prueba Raw',
            category=self.cat,
            base_price=200,
            raw_data={
                'sku': 'RYL-TST-001',
                'variant_colors': ['Rojo', 'Azul'],
                'description': 'Descripción de prueba',
            },
        )

    def test_pendiente_por_defecto(self):
        self.assertEqual(self.pending.status, 'pending')

    def test_approve_crea_producto(self):
        product = self.pending.approve()
        self.assertIsNotNone(product)
        self.assertEqual(product.sku, 'RYL-TST-001')
        self.assertEqual(product.name, 'Gorra Prueba')
        self.assertEqual(product.category, self.cat)
        self.assertEqual(product.supplier_url, 'https://modaverse.vip/#/proinfo/TEST001')

    def test_approve_marca_como_aprobado(self):
        self.pending.approve()
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.assertIsNotNone(self.pending.reviewed_at)

    def test_approve_copia_variant_colors(self):
        product = self.pending.approve()
        self.assertEqual(product.variant_colors, ['Rojo', 'Azul'])

    def test_approve_idempotente_no_duplica(self):
        self.pending.approve()
        self.pending.approve()
        self.assertEqual(Product.objects.filter(sku='RYL-TST-001').count(), 1)

    def test_reject_marca_como_rechazado(self):
        self.pending.reject(notes='No aplica por ahora')
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')
        self.assertEqual(self.pending.notes, 'No aplica por ahora')
        self.assertIsNotNone(self.pending.reviewed_at)

    def test_reject_sin_notas(self):
        self.pending.reject()
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')


# ── load_productos: reconciliación automática al usar --category ──────────────

class LoadProductosReconcileTests(TestCase):
    """load_productos --category debe dar de baja productos que ya no están en el JSON."""

    TREE = [{'id': 'CAP', 'name_es': 'Gorras', 'subcategories': [{'id': 'CAP-1', 'name_es': 'Dandy y Barbas'}]}]

    def setUp(self):
        self.root = Category.objects.create(name='Gorras', slug='gorras')
        self.sub = Category.objects.create(name='Dandy y Barbas', slug='dandy-y-barbas', parent=self.root)
        # Producto que vamos a probar (ausente del JSON en test_eliminado)
        self.product = Product.objects.create(
            sku='RYL-GEN-001', name='Gorra vieja',
            category=self.sub, base_price=Decimal('150'),
            supplier_url='https://www.modaverse.vip/#/proinfo/111111111111111',
            is_active=True,
        )
        # Relleno: 3 productos más → bajar 1 de 4 = 25% < umbral 30% del guard
        for pid in ['222222222222222', '333333333333333', '444444444444444']:
            Product.objects.create(
                sku=f'RYL-GEN-{pid[:3]}', name=f'Gorra {pid}',
                category=self.sub, base_price=Decimal('150'),
                supplier_url=f'https://www.modaverse.vip/#/proinfo/{pid}',
                is_active=True,
            )

    def _json(self, pids):
        return {
            'categories': self.TREE,
            'products': [
                {'sku': pid, 'category_id': 'CAP-1', 'name_es': f'Gorra {pid}',
                 'name': f'Gorra {pid}', 'images': [], 'sizes': [], 'colors': [], 'price_mxn': 150}
                for pid in pids
            ],
        }

    def _call(self, *args, json_data=None, **kwargs):
        data = json_data if json_data is not None else self._json([])
        with patch('catalog.management.commands.load_productos.read_modaverse_json', return_value=data), \
             patch('catalog.management.commands.reconcile_catalog.read_modaverse_json', return_value=data):
            call_command('load_productos', *args, **kwargs)

    def test_product_eliminado_del_json_queda_inactivo(self):
        """Producto en BD que desaparece del JSON debe quedar is_active=False.

        El JSON tiene los otros 3 productos de la categoría pero no el '111...':
        1 baja / 4 activos = 25% < umbral 30%, así el guard no bloquea.
        """
        json_data = self._json(['222222222222222', '333333333333333', '444444444444444'])
        self._call('--category', 'gorra', '--no-images', json_data=json_data)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertTrue(self.product.auto_deactivated)

    def test_product_que_permanece_en_json_sigue_activo(self):
        """Producto que sigue en el JSON no debe tocarse."""
        self._call('--category', 'gorra', '--no-images',
                   json_data=self._json(['111111111111111', '222222222222222', '333333333333333', '444444444444444']))
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        self.assertFalse(self.product.auto_deactivated)

    def test_no_reconcile_desactiva_producto_eliminado(self):
        """Con --no-reconcile el producto eliminado del JSON sigue activo."""
        self._call('--category', 'gorra', '--no-images', '--no-reconcile',
                   json_data=self._json([]))
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_sin_category_no_reconcilia(self):
        """Sin --category no se reconcilia aunque haya productos eliminados."""
        data = self._json([])
        with patch('catalog.management.commands.load_productos.read_modaverse_json', return_value=data), \
             patch('catalog.management.commands.reconcile_catalog.read_modaverse_json', return_value=data):
            call_command('load_productos', '--no-images')
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)


class TipoArticuloMatchesTest(TestCase):
    def setUp(self):
        self.gorras = TipoArticulo.objects.create(
            nombre='Gorras', keywords='gorra,cap,ny,la,za', costo=Decimal('280')
        )

    def test_keyword_exacta_hace_match(self):
        self.assertTrue(self.gorras.matches('Gorra NY negra $450 MXN'))

    def test_keyword_case_insensitive(self):
        self.assertTrue(self.gorras.matches('GORRA NY'))

    def test_keyword_parcial_hace_match(self):
        # 'ny' está en el texto
        self.assertTrue(self.gorras.matches('Azul NY $400'))

    def test_sin_keyword_no_hace_match(self):
        self.assertFalse(self.gorras.matches('Camiseta Jordan $350'))

    def test_texto_vacio_no_hace_match(self):
        self.assertFalse(self.gorras.matches(''))

    def test_keywords_con_espacios_se_trimean(self):
        tipo = TipoArticulo(nombre='X', keywords=' camiseta , playera ', costo=Decimal('150'))
        self.assertTrue(tipo.matches('Camiseta Lakers'))


class CodigoDescuentoStrTest(TestCase):
    def test_str_global(self):
        code = CodigoDescuento(codigo='PROMO10', descuento=Decimal('50'))
        self.assertIn('global', str(code))

    def test_str_con_tipo(self):
        tipo = TipoArticulo.objects.create(nombre='Gorras', keywords='gorra', costo=Decimal('280'))
        code = CodigoDescuento.objects.create(
            codigo='GORRA10', descuento=Decimal('50'), tipo_articulo=tipo
        )
        self.assertIn('Gorras', str(code))


class BuscarTipoArticuloTest(TestCase):
    def setUp(self):
        self.gorras = TipoArticulo.objects.create(
            nombre='Gorras', keywords='gorra,cap,ny,la', costo=Decimal('280')
        )
        self.camisetas = TipoArticulo.objects.create(
            nombre='Camisetas', keywords='camiseta,playera,jersey', costo=Decimal('150')
        )

    def test_encuentra_por_keyword(self):
        result = buscar_tipo_articulo('Gorra NY negra')
        self.assertEqual(result, self.gorras)

    def test_encuentra_por_segunda_keyword(self):
        result = buscar_tipo_articulo('playera Lakers L')
        self.assertEqual(result, self.camisetas)

    def test_sin_match_retorna_none(self):
        result = buscar_tipo_articulo('Tenis Nike Air Max')
        self.assertIsNone(result)

    def test_texto_vacio_retorna_none(self):
        result = buscar_tipo_articulo('')
        self.assertIsNone(result)

    def test_case_insensitive(self):
        result = buscar_tipo_articulo('GORRA NY $450 MXN')
        self.assertEqual(result, self.gorras)


class ValidarCodigoTest(TestCase):
    def setUp(self):
        self.gorras = TipoArticulo.objects.create(
            nombre='Gorras', keywords='gorra,cap', costo=Decimal('280')
        )
        self.code_global = CodigoDescuento.objects.create(
            codigo='GLOBAL10', descuento=Decimal('100'), is_active=True
        )
        self.code_gorras = CodigoDescuento.objects.create(
            codigo='GORRA50', descuento=Decimal('50'),
            tipo_articulo=self.gorras, is_active=True
        )

    def test_codigo_global_siempre_valido(self):
        result = validar_codigo('GLOBAL10', ['Camiseta Lakers'])
        self.assertTrue(result['valido'])
        self.assertEqual(result['descuento'], 100.0)

    def test_codigo_tipo_valido_con_match(self):
        result = validar_codigo('GORRA50', ['Gorra NY negra $450', 'Camiseta Lakers'])
        self.assertTrue(result['valido'])
        self.assertEqual(result['descuento'], 50.0)

    def test_codigo_tipo_invalido_sin_match(self):
        result = validar_codigo('GORRA50', ['Camiseta Lakers', 'Tenis Nike'])
        self.assertFalse(result['valido'])
        self.assertIn('Gorras', result['mensaje'])

    def test_codigo_inexistente(self):
        result = validar_codigo('NOEXISTE', [])
        self.assertFalse(result['valido'])

    def test_codigo_inactivo(self):
        self.code_global.is_active = False
        self.code_global.save()
        result = validar_codigo('GLOBAL10', [])
        self.assertFalse(result['valido'])

    def test_codigo_expirado(self):
        self.code_global.valid_hasta = datetime.date(2020, 1, 1)
        self.code_global.save()
        result = validar_codigo('GLOBAL10', [])
        self.assertFalse(result['valido'])
        self.assertIn('expirado', result['mensaje'].lower())

    def test_codigo_agotado(self):
        self.code_global.usos_max = 5
        self.code_global.usos_actuales = 5
        self.code_global.save()
        result = validar_codigo('GLOBAL10', [])
        self.assertFalse(result['valido'])
        self.assertIn('agotado', result['mensaje'].lower())

    def test_codigo_case_insensitive(self):
        result = validar_codigo('global10', [])
        self.assertTrue(result['valido'])

    def test_retorna_codigo_id_cuando_valido(self):
        result = validar_codigo('GLOBAL10', [])
        self.assertEqual(result['codigo_id'], self.code_global.pk)

    def test_retorna_tipo_nombre_cuando_aplica(self):
        result = validar_codigo('GORRA50', ['Gorra NY'])
        self.assertEqual(result['tipo_nombre'], 'Gorras')


# ── cleanup_media: limpieza semanal de imágenes ──────────────────────────────

class CleanupMediaTests(TestCase):
    def setUp(self):
        import tempfile
        self._media = tempfile.mkdtemp(prefix='cleanup-media-test-')
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        self.cat = Category.objects.create(name='Gorras CM', slug='gorras-cm')

    def tearDown(self):
        import shutil
        self._override.disable()
        shutil.rmtree(self._media, ignore_errors=True)

    def _touch(self, relpath, age_days=30):
        import os, time
        path = os.path.join(self._media, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x' * 100)
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
        return path

    def _run(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('cleanup_media', *args, stdout=out, stderr=out)
        return out.getvalue()

    def _pending(self, status='rejected', cover='pending/cm-rechazado.jpg', url_suffix='CM1'):
        return PendingProduct.objects.create(
            supplier_url=f'https://modaverse.vip/#/proinfo/{url_suffix}',
            display_name='Gorra CM',
            category=self.cat,
            base_price=100,
            raw_data={},
            status=status,
            cover_image=cover,
        )

    def test_apply_borra_cover_de_rechazado_y_limpia_campo(self):
        import os
        pp = self._pending()
        path = self._touch('pending/cm-rechazado.jpg')
        self._run('--apply')
        pp.refresh_from_db()
        self.assertFalse(os.path.exists(path))
        self.assertEqual(pp.cover_image, '')

    def test_dry_run_no_borra_nada(self):
        import os
        pp = self._pending()
        path = self._touch('pending/cm-rechazado.jpg')
        orphan = self._touch('pending/cm-huerfano.jpg')
        self._run()
        pp.refresh_from_db()
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(orphan))
        self.assertNotEqual(pp.cover_image, '')

    def test_apply_borra_huerfano_viejo_y_respeta_referenciado(self):
        import os
        product = Product.objects.create(
            sku='RYL-CM-1', name='Gorra CM', category=self.cat, base_price=100,
        )
        ProductImage.objects.create(product=product, image='products/cm-ref.jpg')
        ref = self._touch('products/cm-ref.jpg')
        orphan = self._touch('products/cm-orphan.jpg')
        self._run('--apply')
        self.assertTrue(os.path.exists(ref))
        self.assertFalse(os.path.exists(orphan))

    def test_huerfano_reciente_se_respeta(self):
        import os
        product = Product.objects.create(
            sku='RYL-CM-2', name='Gorra CM2', category=self.cat, base_price=100,
        )
        ProductImage.objects.create(product=product, image='products/cm-ref2.jpg')
        self._touch('products/cm-ref2.jpg')
        recent = self._touch('products/cm-recien.jpg', age_days=0)
        self._run('--apply')
        self.assertTrue(os.path.exists(recent))

    def test_sin_referencias_en_bd_salta_directorio(self):
        import os
        # No hay ProductImage en BD → el paso products/ se salta por seguridad
        orphan = self._touch('products/cm-solo.jpg')
        out = self._run('--apply')
        self.assertTrue(os.path.exists(orphan))
        self.assertIn('saltado por seguridad', out)


class SubcategoryPriceOverrideTests(TestCase):
    """Subcategorías pueden fijar su propio costo de proveedor y ganancia,
    en vez de heredar siempre de la categoría raíz."""

    def setUp(self):
        self.root = Category.objects.create(
            name='Joyería', slug='joyeria-test',
            shipping_cost=Decimal('50'), profit_margin=Decimal('100'),
        )
        self.sub_sin_override = Category.objects.create(
            name='Gargantillas', slug='gargantillas-test', parent=self.root,
        )
        self.sub_con_override = Category.objects.create(
            name='Anillos', slug='anillos-test', parent=self.root,
            base_price_override=Decimal('300'),
            profit_margin_override=Decimal('150'),
        )

    def _make_product(self, category, base_price):
        return Product.objects.create(
            sku=f'RYL-JOYTEST-{category.pk}-{base_price}',
            name='Producto de prueba',
            category=category,
            base_price=Decimal(str(base_price)),
        )

    def test_sin_override_usa_base_price_propio_y_margen_de_la_raiz(self):
        """Regresión: sin overrides, el comportamiento es igual que hoy."""
        p = self._make_product(self.sub_sin_override, base_price=200)
        self.assertEqual(p.effective_base_price, Decimal('200'))
        self.assertEqual(p.effective_profit_margin, Decimal('100'))
        self.assertEqual(p.final_price, Decimal('200') + Decimal('50') + Decimal('100'))

    def test_base_price_override_reemplaza_el_base_price_individual(self):
        """Dos productos con base_price distinto en la misma subcategoría con
        override → ambos terminan con el mismo effective_base_price."""
        p1 = self._make_product(self.sub_con_override, base_price=50)
        p2 = self._make_product(self.sub_con_override, base_price=999)
        self.assertEqual(p1.effective_base_price, Decimal('300'))
        self.assertEqual(p2.effective_base_price, Decimal('300'))

    def test_profit_margin_override_reemplaza_el_margen_de_la_raiz(self):
        p = self._make_product(self.sub_con_override, base_price=300)
        self.assertEqual(p.effective_profit_margin, Decimal('150'))

    def test_ambos_overrides_juntos_en_final_price(self):
        p = self._make_product(self.sub_con_override, base_price=1)  # base_price ignorado
        # final = base_price_override(300) + shipping de la raíz(50) + profit_margin_override(150)
        self.assertEqual(p.final_price, Decimal('300') + Decimal('50') + Decimal('150'))

    def test_price_override_de_producto_sigue_ganando_a_todo(self):
        p = self._make_product(self.sub_con_override, base_price=1)
        p.price_override = Decimal('9999')
        p.save(update_fields=['price_override'])
        self.assertEqual(p.final_price, Decimal('9999'))

    def test_subcategoria_sin_override_no_se_ve_afectada_por_la_de_al_lado(self):
        """Aislamiento: que una subcategoría de la misma raíz tenga override
        no debe afectar a otra subcategoría hermana sin override."""
        p = self._make_product(self.sub_sin_override, base_price=77)
        self.assertEqual(p.effective_base_price, Decimal('77'))
        self.assertEqual(p.effective_profit_margin, Decimal('100'))

    def test_producto_colgado_directo_de_la_raiz_con_override_en_la_raiz(self):
        """Un producto sin subcategoría intermedia (category = raíz directa)
        también respeta los overrides si están puestos en esa misma raíz."""
        root_con_override = Category.objects.create(
            name='Bolsos', slug='bolsos-test',
            shipping_cost=Decimal('80'), profit_margin=Decimal('200'),
            base_price_override=Decimal('500'), profit_margin_override=Decimal('250'),
        )
        p = self._make_product(root_con_override, base_price=1)
        self.assertEqual(p.effective_base_price, Decimal('500'))
        self.assertEqual(p.effective_profit_margin, Decimal('250'))

    def test_pending_product_respeta_base_price_override(self):
        from catalog.models import PendingProduct
        pp = PendingProduct.objects.create(
            supplier_url='https://modaverse.vip/#/proinfo/PENDTEST1',
            display_name='Anillo pendiente',
            category=self.sub_con_override,
            base_price=Decimal('1'),  # debe ser ignorado por el override
        )
        # final = base_price_override(300) + shipping de la raíz(50) + profit_margin_override(150)
        self.assertEqual(pp.final_price, Decimal('300') + Decimal('50') + Decimal('150'))

    def test_pending_product_sin_override_usa_comportamiento_actual(self):
        from catalog.models import PendingProduct
        pp = PendingProduct.objects.create(
            supplier_url='https://modaverse.vip/#/proinfo/PENDTEST2',
            display_name='Gargantilla pendiente',
            category=self.sub_sin_override,
            base_price=Decimal('200'),
        )
        self.assertEqual(pp.final_price, Decimal('200') + Decimal('50') + Decimal('100'))


class ConsumirUsoTests(TestCase):
    """consumir_uso: chequeo + incremento de usos_actuales en UN solo UPDATE
    atómico — cierra la carrera de dos checkouts simultáneos que antes podían
    rebasar usos_max (leer-luego-incrementar en pasos separados)."""

    def _codigo(self, **kwargs):
        defaults = dict(codigo='PROMO10', descuento=Decimal('10'))
        defaults.update(kwargs)
        return CodigoDescuento.objects.create(**defaults)

    def test_incrementa_y_retorna_true(self):
        from catalog.services import consumir_uso
        code = self._codigo(usos_max=5, usos_actuales=0)
        self.assertTrue(consumir_uso('promo10'))   # case-insensitive
        code.refresh_from_db()
        self.assertEqual(code.usos_actuales, 1)

    def test_agotado_retorna_false_sin_incrementar(self):
        from catalog.services import consumir_uso
        code = self._codigo(usos_max=2, usos_actuales=2)
        self.assertFalse(consumir_uso('PROMO10'))
        code.refresh_from_db()
        self.assertEqual(code.usos_actuales, 2)

    def test_sin_limite_siempre_incrementa(self):
        from catalog.services import consumir_uso
        code = self._codigo(usos_max=None, usos_actuales=99)
        self.assertTrue(consumir_uso('PROMO10'))
        code.refresh_from_db()
        self.assertEqual(code.usos_actuales, 100)

    def test_por_pk(self):
        from catalog.services import consumir_uso
        code = self._codigo(usos_max=1, usos_actuales=0)
        self.assertTrue(consumir_uso(pk=code.pk))
        self.assertFalse(consumir_uso(pk=code.pk))
        code.refresh_from_db()
        self.assertEqual(code.usos_actuales, 1)


class CategoryOverrideValidationTests(TestCase):
    """Un override puesto en una categoría RAÍZ con subcategorías no aplica a
    los productos de las subcategorías (effective_* solo mira la categoría
    directa) — guardarlo ahí desde el admin era un no-op silencioso. clean()
    lo rechaza con un mensaje claro."""

    def test_override_en_raiz_con_subcategorias_no_valida(self):
        from django.core.exceptions import ValidationError
        root = Category.objects.create(name='Gorras Val', slug='gorras-val')
        Category.objects.create(name='Sub Val', slug='sub-val', parent=root)
        root.base_price_override = Decimal('100')
        with self.assertRaises(ValidationError):
            root.full_clean()

    def test_override_en_subcategoria_valida(self):
        root = Category.objects.create(name='Gorras Val2', slug='gorras-val2')
        sub = Category.objects.create(name='Sub Val2', slug='sub-val2', parent=root)
        sub.base_price_override = Decimal('100')
        sub.profit_margin_override = Decimal('50')
        sub.full_clean()  # no debe lanzar

    def test_override_en_raiz_sin_subcategorias_valida(self):
        # Raíz sin hijos = los productos cuelgan directo de ella y el override SÍ aplica
        root = Category.objects.create(name='Gorras Val3', slug='gorras-val3')
        root.profit_margin_override = Decimal('50')
        root.full_clean()  # no debe lanzar
