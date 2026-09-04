from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

import datetime

from catalog.management.commands.import_images import _pid_from_url
from catalog.modaverse import parse_specifications
from catalog.management.commands.load_productos import (
    _category_filter_ids,
    _build_existing_pids,
    _next_sku_index,
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

    def test_approve_por_defecto_deja_producto_activo(self):
        """Sin argumento, approve() sigue publicando: los llamadores viejos
        (revive_pending, comandos, el panel antes del switch) no cambian."""
        product = self.pending.approve()
        self.assertTrue(product.is_active)

    def test_approve_activate_false_deja_producto_inactivo(self):
        """Aprobar en modo oculto crea el producto fuera de la tienda, para
        los que todavía necesitan que les cambien la imagen."""
        product = self.pending.approve(activate=False)
        self.assertFalse(product.is_active)

    def test_approve_activate_false_desactiva_producto_existente(self):
        """La elección explícita gana también en la rama idempotente: si el
        Product ya existía y se aprueba oculto, se desactiva."""
        product = self.pending.approve()
        self.assertTrue(product.is_active)

        self.pending.status = 'pending'
        self.pending.save(update_fields=['status'])
        self.pending.approve(activate=False)

        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_approve_activate_true_reactiva_producto_existente(self):
        """Simétrico del anterior: aprobar activo vuelve a publicar uno que
        estaba oculto, sin tener que ir a /panel/productos/."""
        product = self.pending.approve(activate=False)
        self.assertFalse(product.is_active)

        self.pending.status = 'pending'
        self.pending.save(update_fields=['status'])
        self.pending.approve(activate=True)

        product.refresh_from_db()
        self.assertTrue(product.is_active)

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


class NextSkuIndexReservadosTests(TestCase):
    """_next_sku_index debe respetar los SKUs ya reservados por pendientes.

    Regresión: solo miraba Product, así que una segunda corrida de
    load_productos reasignaba SKUs que ya tenían pendientes sin aprobar.
    """

    def setUp(self):
        self.cat = Category.objects.create(name='Cat Sku', slug='cat-sku')

    def test_salta_skus_reservados_por_pendientes(self):
        Product.objects.create(
            sku='RYL-RES-001', name='Ya en catálogo', category=self.cat,
            base_price=Decimal('100'),
        )
        PendingProduct.objects.create(
            supplier_url='https://modaverse.vip/#/proinfo/RESERVADO',
            display_name='Reservado sin aprobar',
            category=self.cat, base_price=Decimal('100'),
            raw_data={'sku': 'RYL-RES-002'},
        )
        self.assertEqual(_next_sku_index('RES'), 3)


class RepairSkuColisionesCommandTests(TestCase):
    """repair_sku_colisiones: repara el daño ya hecho por la colisión de SKU.

    Dos reparaciones distintas:
      a) pendientes marcados 'approved' que nunca llegaron al catálogo
      b) productos cuyo nombre/precio fueron pisados por un pendiente ajeno
    """

    URL_A = 'https://modaverse.vip/#/proinfo/AAA'
    URL_B = 'https://modaverse.vip/#/proinfo/BBB'

    def setUp(self):
        self.cat = Category.objects.create(
            name='Cat Rep', slug='cat-rep', shipping_cost=0, profit_margin=100,
        )
        # Producto A, ya en catálogo, pero con nombre/precio pisados por B
        self.prod_a = Product.objects.create(
            sku='RYL-REP-001', name='Nombre De B', category=self.cat,
            base_price=Decimal('222'), supplier_url=self.URL_A,
        )
        self.pend_a = PendingProduct.objects.create(
            supplier_url=self.URL_A, display_name='Nombre Real De A',
            modaverse_name='Nombre Real De A', category=self.cat,
            base_price=Decimal('111'), status='approved',
            raw_data={'sku': 'RYL-REP-001'},
        )
        # Pendiente B: aprobado, mismo SKU reservado, nunca creó producto
        self.pend_b = PendingProduct.objects.create(
            supplier_url=self.URL_B, display_name='Nombre De B',
            category=self.cat, base_price=Decimal('222'), status='approved',
            raw_data={'sku': 'RYL-REP-001'},
        )

    def _run(self, *args):
        out = StringIO()
        call_command('repair_sku_colisiones', *args, stdout=out)
        return out.getvalue()

    def test_recupera_el_pendiente_que_nunca_llego_al_catalogo(self):
        self._run()
        prod_b = Product.objects.filter(supplier_url=self.URL_B).first()
        self.assertIsNotNone(prod_b, 'el pendiente aprobado sin producto no se recuperó')
        self.assertNotEqual(prod_b.sku, 'RYL-REP-001')

    def test_restaura_el_producto_pisado(self):
        self._run()
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.name, 'Nombre Real De A')
        self.assertEqual(self.prod_a.base_price, Decimal('111'))

    def test_dry_run_no_escribe_nada(self):
        self._run('--dry-run')
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.name, 'Nombre De B')
        self.assertFalse(Product.objects.filter(supplier_url=self.URL_B).exists())

    def test_no_duplica_si_el_producto_existe_con_el_otro_formato_de_url(self):
        """El catálogo guarda dos formatos de supplier_url para el mismo pid;
        la reconciliación es por pid, no por string exacto."""
        PendingProduct.objects.create(
            supplier_url='https://www.modaverse.vip/#/proinfo/PID777',
            display_name='Ya existe con otro formato', category=self.cat,
            base_price=Decimal('150'), status='approved',
            raw_data={'sku': 'RYL-REP-777'},
        )
        Product.objects.create(
            sku='RYL-REP-778', name='Ya existe con otro formato', category=self.cat,
            base_price=Decimal('150'),
            supplier_url='https://www.modaverse.vip/#/product/CAT9?pid=PID777',
        )
        antes = Product.objects.count()
        self._run()
        self.assertEqual(Product.objects.count(), antes + 1)  # solo el pend_b legítimo

    def test_recupera_pendiente_sin_categoria_resolviendola_del_json(self):
        """Los pendientes sin categoría son recuperables si el JSON dice a qué
        categoría del árbol pertenecen y esa categoría existe en el catálogo."""
        destino = Category.objects.create(
            name='Gorro de Pico AA05', slug='gorro-de-pico-aa05', parent=self.cat,
        )
        PendingProduct.objects.create(
            supplier_url='https://www.modaverse.vip/#/proinfo/PIDSINCAT',
            display_name='Gorra sin categoría', category=None,
            base_price=Decimal('130'), status='approved',
            raw_data={'sku': 'RYL-CAP-501'},
        )
        data = {
            'categories': [{
                'id': 'CATROOT', 'name_es': 'Gorra',
                'subcategories': [{'id': 'CATSUB', 'name_es': 'Gorro de Pico AA05'}],
            }],
            'products': [{'sku': 'PIDSINCAT', 'category_id': 'CATSUB'}],
        }
        with patch('catalog.management.commands.repair_sku_colisiones.read_modaverse_json',
                   return_value=data):
            self._run()
        prod = Product.objects.filter(
            supplier_url='https://www.modaverse.vip/#/proinfo/PIDSINCAT').first()
        self.assertIsNotNone(prod, 'no se recuperó el pendiente sin categoría')
        self.assertEqual(prod.category, destino)

    def test_no_inventa_categoria_si_el_json_no_la_tiene_en_el_arbol(self):
        """Categoría eliminada del proveedor: se reporta y se salta, no se
        inventa un destino ni se crea una categoría nueva."""
        antes_cats = Category.objects.count()
        PendingProduct.objects.create(
            supplier_url='https://www.modaverse.vip/#/proinfo/PIDHUERFANO',
            display_name='Collar huérfano', category=None,
            base_price=Decimal('320'), status='approved',
            raw_data={'sku': 'RYL-GEN-502'},
        )
        data = {
            'categories': [{'id': 'CATROOT', 'name_es': 'Gorra', 'subcategories': []}],
            'products': [{'sku': 'PIDHUERFANO', 'category_id': 'CAT-QUE-YA-NO-EXISTE'}],
        }
        with patch('catalog.management.commands.repair_sku_colisiones.read_modaverse_json',
                   return_value=data):
            self._run()
        self.assertFalse(Product.objects.filter(
            supplier_url='https://www.modaverse.vip/#/proinfo/PIDHUERFANO').exists())
        self.assertEqual(Category.objects.count(), antes_cats)

    def test_no_revierte_nombres_editados_a_mano(self):
        """Solo se restaura lo que tiene firma de colisión: un producto renombrado
        a mano difiere de su pendiente, pero nadie lo pisó — no se toca."""
        url_c = 'https://modaverse.vip/#/proinfo/CCC'
        prod_c = Product.objects.create(
            sku='RYL-REP-900', name='Nombre Editado A Mano', category=self.cat,
            base_price=Decimal('300'), supplier_url=url_c,
        )
        PendingProduct.objects.create(
            supplier_url=url_c, display_name='Nombre Crudo Del Scraper',
            category=self.cat, base_price=Decimal('300'), status='approved',
            raw_data={'sku': 'RYL-REP-900'},
        )
        self._run()
        prod_c.refresh_from_db()
        self.assertEqual(prod_c.name, 'Nombre Editado A Mano')


class ApproveSkuColisionTests(TestCase):
    """Aprobar un pendiente cuyo SKU ya pertenece a OTRO producto.

    Regresión: dos corridas de load_productos antes de aprobar reservaban el
    mismo SKU. approve() hacía get_or_create(sku=...), encontraba el producto
    ajeno y le sobrescribía nombre/precio, sin crear nunca el producto nuevo.
    """

    URL_ORIGINAL = 'https://modaverse.vip/#/proinfo/ORIGINAL'
    URL_NUEVO    = 'https://modaverse.vip/#/proinfo/NUEVO'

    def setUp(self):
        self.cat = Category.objects.create(
            name='Cat Colisión', slug='cat-colision',
            shipping_cost=50, profit_margin=100,
        )
        self.original = Product.objects.create(
            sku='RYL-COL-001', name='Producto Original', category=self.cat,
            base_price=Decimal('111'), supplier_url=self.URL_ORIGINAL,
        )
        self.pending = PendingProduct.objects.create(
            supplier_url=self.URL_NUEVO,
            display_name='Producto Nuevo',
            category=self.cat,
            base_price=Decimal('222'),
            raw_data={'sku': 'RYL-COL-001'},
        )

    def test_no_sobrescribe_el_producto_ajeno(self):
        self.pending.approve()
        self.original.refresh_from_db()
        self.assertEqual(self.original.name, 'Producto Original')
        self.assertEqual(self.original.base_price, Decimal('111'))
        self.assertEqual(self.original.supplier_url, self.URL_ORIGINAL)

    def test_crea_el_producto_nuevo_con_sku_libre(self):
        product = self.pending.approve()
        self.assertIsNotNone(product)
        self.assertEqual(product.supplier_url, self.URL_NUEVO)
        self.assertNotEqual(product.sku, 'RYL-COL-001')
        self.assertTrue(Product.objects.filter(supplier_url=self.URL_NUEVO).exists())


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


class BuscarTipoArticuloEspecificidadTest(TestCase):
    """'gorras' es keyword genérica de dos tipos a la vez; debe ganar el match
    más específico (keyword más larga), no el primero por orden alfabético."""

    def setUp(self):
        self.barbas = TipoArticulo.objects.create(
            nombre='Gorra Barbas y Dandy', keywords='gorras,barbas,dandy', costo=Decimal('240')
        )
        self.new_era = TipoArticulo.objects.create(
            nombre='Gorras New Era', keywords='gorras,new era', costo=Decimal('150')
        )

    def test_keyword_especifica_gana_sobre_generica(self):
        result = buscar_tipo_articulo('Gorras New Era negra')
        self.assertEqual(result, self.new_era)

    def test_solo_keyword_generica_usa_orden_alfabetico(self):
        result = buscar_tipo_articulo('gorras')
        self.assertEqual(result, self.barbas)


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


@override_settings(META_PIXEL_ID='TESTPIXEL')
class MetaPixelSearchTests(TestCase):
    """Evento estándar Search del píxel de Meta en los resultados de búsqueda."""

    def test_busqueda_con_q_dispara_search(self):
        res = self.client.get(reverse('catalog:search'), {'q': 'gorra new era'})
        self.assertContains(res, "fbq('track', 'Search'")
        self.assertContains(res, 'gorra new era')

    def test_busqueda_sin_q_no_dispara_search(self):
        res = self.client.get(reverse('catalog:search'))
        self.assertNotContains(res, "fbq('track', 'Search'")


class TipoArticuloKeywordsValidationTests(TestCase):
    """Las keywords DEBEN ir separadas por comas: matches() parte solo por
    coma, así que un bloque con saltos de línea se vuelve una sola keyword
    gigante que nunca matchea → costo $0 silencioso en ventas (caso real
    2026-07-14: tipo 'SUDADERA G5' → ítem 's G5' con ganancia inflada $420)."""

    def test_keywords_con_saltos_de_linea_no_validan(self):
        from django.core.exceptions import ValidationError
        tipo = TipoArticulo(
            nombre='Sudadera G5',
            keywords='sudadera G5\r\nSud G5\r\ns G5',
            costo=Decimal('420'),
        )
        with self.assertRaises(ValidationError) as ctx:
            tipo.full_clean()
        self.assertIn('keywords', ctx.exception.message_dict)

    def test_keywords_separadas_por_comas_validan(self):
        tipo = TipoArticulo(
            nombre='Sudadera G5',
            keywords='sudadera G5, Sud G5, s G5',
            costo=Decimal('420'),
        )
        tipo.full_clean()  # no debe lanzar

    def test_form_del_panel_muestra_el_mensaje(self):
        from negocio.forms import TipoArticuloForm
        form = TipoArticuloForm(data={
            'nombre': 'Sudadera G5',
            'keywords': 'sudadera G5\ns G5',
            'costo': '420',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('keywords', form.errors)


class TipoArticuloKeywordDuplicadaValidationTests(TestCase):
    """Dos tipos NO pueden compartir la misma keyword exacta: crear_pedido_tienda_bot
    hace match por 'la primera keyword que aparece' sin resolver especificidad, así
    que una keyword duplicada entre tipos asigna el costo del tipo equivocado sin
    avisar (caso real 2026-07-24: 'new' en 'Gorras New Era' y 'New balance' a la vez
    → pedido #39 registró una gorra de $150 con el costo de un tenis de $680)."""

    def setUp(self):
        self.new_balance = TipoArticulo.objects.create(
            nombre='New balance', keywords='NB,new balance,new', costo=Decimal('680'),
        )

    def test_keyword_duplicada_en_otro_tipo_no_valida(self):
        from django.core.exceptions import ValidationError
        tipo = TipoArticulo(
            nombre='Gorras New Era', keywords='new era, new', costo=Decimal('150'),
        )
        with self.assertRaises(ValidationError) as ctx:
            tipo.full_clean()
        self.assertIn('keywords', ctx.exception.message_dict)
        self.assertIn('New balance', ctx.exception.message_dict['keywords'][0])

    def test_keyword_duplicada_case_insensitive_y_con_espacios(self):
        from django.core.exceptions import ValidationError
        tipo = TipoArticulo(
            nombre='Gorras New Era', keywords='New  Balance', costo=Decimal('150'),
        )
        with self.assertRaises(ValidationError):
            tipo.full_clean()

    def test_keywords_sin_duplicar_valida(self):
        tipo = TipoArticulo(
            nombre='Gorras New Era', keywords='new era, gorra new', costo=Decimal('150'),
        )
        tipo.full_clean()  # no debe lanzar

    def test_editar_mismo_tipo_no_duplica_consigo_mismo(self):
        self.new_balance.costo = Decimal('700')
        self.new_balance.full_clean()  # no debe lanzar contra sí mismo

    def test_form_del_panel_muestra_el_mensaje_de_duplicado(self):
        from negocio.forms import TipoArticuloForm
        form = TipoArticuloForm(data={
            'nombre': 'Gorras New Era',
            'keywords': 'new era, new',
            'costo': '150',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('keywords', form.errors)


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


from catalog.management.commands.sync_precios_modaverse import Command as SyncCommand


class SyncPreciosModaverseTests(TestCase):
    """El precio del proveedor cambia con el tiempo y load_productos nunca lo
    actualizaba en productos ya existentes: base_price quedaba congelado en el
    precio del día que se cargó. `modaverse_price` guarda el último precio visto
    del proveedor, y sirve para distinguir un precio automático (base_price ==
    modaverse_price) de uno editado a mano en el panel."""

    def setUp(self):
        self.cat = Category.objects.create(name='Gorra', slug='gorra')

    def _json(self, price, pid='PR001', cat_id='C1', cat_name='Gorra'):
        return {
            'products': [{
                'sku': pid, 'name': 'ZA-179', 'category_id': cat_id,
                'category': cat_name,
                'url': f'https://www.modaverse.vip/#/product/{cat_id}?pid={pid}',
                'images': [], 'sizes': [], 'colors': [], 'price_mxn': price,
            }],
            'categories': [{'id': cat_id, 'name_es': cat_name, 'subcategories': []}],
        }

    def _producto(self, base, marca, pid='PR001', cat=None):
        return Product.objects.create(
            sku='RYL-CAP-001', name='ZA-179', category=cat or self.cat,
            base_price=Decimal(base), modaverse_price=None if marca is None else Decimal(marca),
            supplier_url=f'https://www.modaverse.vip/#/proinfo/{pid}',
        )

    def test_actualiza_precio_de_producto_no_editado_a_mano(self):
        prod = self._producto(base='250', marca='250')
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('sync_precios_modaverse')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('220.00'))
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_respeta_el_precio_editado_a_mano(self):
        """base_price != modaverse_price = alguien lo cambió en el panel: no se pisa."""
        prod = self._producto(base='400', marca='250')
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('sync_precios_modaverse')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('400.00'))
        # la marca sí avanza, para seguir al proveedor sin tocar el precio manual
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_producto_sin_marca_la_adopta_sin_tocar_el_precio(self):
        prod = self._producto(base='250', marca=None)
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('sync_precios_modaverse')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('250.00'))
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_dry_run_no_escribe_nada(self):
        prod = self._producto(base='250', marca='250')
        out = StringIO()
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('sync_precios_modaverse', '--dry-run', stdout=out)
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('250.00'))
        self.assertEqual(prod.modaverse_price, Decimal('250.00'))
        self.assertIn('250', out.getvalue())
        self.assertIn('220', out.getvalue())

    def test_precio_cero_del_json_no_pisa_el_precio_bueno(self):
        """El JSON trae price_mxn 0 para algunos productos; load_productos cae a un
        default por categoría al crearlos. Sincronizar contra 0 sería destructivo."""
        prod = self._producto(base='250', marca='250')
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=self._json(0)):
            call_command('sync_precios_modaverse')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('250.00'))

    def test_category_limita_el_alcance(self):
        otra = Category.objects.create(name='Reloj', slug='reloj')
        fuera = Product.objects.create(
            sku='RYL-REL-001', name='Rolex', category=otra,
            base_price=Decimal('900'), modaverse_price=Decimal('900'),
            supplier_url='https://www.modaverse.vip/#/proinfo/PR999',
        )
        dentro = self._producto(base='250', marca='250')
        data = self._json(220)
        data['products'].append({
            'sku': 'PR999', 'name': 'Rolex', 'category_id': 'C2', 'category': 'Reloj',
            'url': 'https://www.modaverse.vip/#/product/C2?pid=PR999',
            'images': [], 'sizes': [], 'colors': [], 'price_mxn': 700,
        })
        data['categories'].append({'id': 'C2', 'name_es': 'Reloj', 'subcategories': []})
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=data):
            call_command('sync_precios_modaverse', '--category', 'Gorra')
        dentro.refresh_from_db()
        fuera.refresh_from_db()
        self.assertEqual(dentro.base_price, Decimal('220.00'))
        self.assertEqual(fuera.base_price, Decimal('900.00'))

    def test_load_productos_tambien_sincroniza_el_precio(self):
        """La causa raíz: una corrida normal de load_productos debe dejar el precio
        al día, no solo el nombre y las variantes."""
        prod = self._producto(base='250', marca='250')
        with patch.object(LoadCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('load_productos', '--category', 'Gorra', '--no-images')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('220.00'))
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_load_productos_no_pisa_el_precio_editado_a_mano(self):
        prod = self._producto(base='400', marca='250')
        with patch.object(LoadCommand, '_read_modaverse_json', return_value=self._json(220)):
            call_command('load_productos', '--category', 'Gorra', '--no-images')
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('400.00'))

    def test_aprobar_un_pendiente_deja_el_producto_marcado_como_automatico(self):
        """Si el producto nace sin marca, la primera sincronización lo trataría
        como 'editado a mano' y su precio nunca se actualizaría."""
        pend = PendingProduct.objects.create(
            display_name='ZA-500', modaverse_name='ZA-500', category=self.cat,
            base_price=Decimal('220'),
            supplier_url='https://www.modaverse.vip/#/proinfo/PR500',
        )
        prod = pend.approve()
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_reaprobar_sobre_producto_existente_reajusta_la_marca(self):
        prod = self._producto(base='250', marca='250', pid='PR600')
        pend = PendingProduct.objects.create(
            display_name='ZA-600', modaverse_name='ZA-600', category=self.cat,
            base_price=Decimal('220'),
            supplier_url='https://www.modaverse.vip/#/proinfo/PR600',
        )
        pend.approve()
        prod.refresh_from_db()
        self.assertEqual(prod.base_price, Decimal('220.00'))
        self.assertEqual(prod.modaverse_price, Decimal('220.00'))

    def test_no_toca_productos_de_yupoo(self):
        """Calzado viene de otro proveedor (putianshoefactory.x.yupoo.com) y su
        supplier_url no tiene pid de Modaverse. Un match espurio le reescribiría
        el precio con el de un producto ajeno."""
        calzado = Category.objects.create(name='Calzado', slug='calzado')
        tenis = Product.objects.create(
            sku='RYL-TN2-389', name='AMIRI', category=calzado,
            base_price=Decimal('380'), modaverse_price=Decimal('380'),
            supplier_url='https://putianshoefactory.x.yupoo.com/albums/171878819?uid=1',
        )
        data = self._json(220)
        # una entrada del JSON sin url reconocible: no debe colapsar contra el tenis
        data['products'].append({
            'sku': '', 'name': 'YAA3065', 'category_id': 'C1', 'category': 'Gorra',
            'url': 'https://www.modaverse.vip/#/index', 'images': [],
            'sizes': [], 'colors': [], 'price_mxn': 0,
        })
        with patch.object(SyncCommand, '_read_modaverse_json', return_value=data):
            call_command('sync_precios_modaverse')
        tenis.refresh_from_db()
        self.assertEqual(tenis.base_price, Decimal('380.00'))
        self.assertEqual(tenis.modaverse_price, Decimal('380.00'))


from catalog.modaverse import merge_scraped_products


class MergeScrapedProductsTests(TestCase):
    """El scraper fusiona lo recién bajado con el JSON previo. Lo que la API
    acaba de devolver para una categoría del filtro manda sobre la copia
    guardada, esté archivada donde esté.

    Sin esto, una entrada con `category_id` malo (None → 'General', por una
    respuesta degradada de la API) queda *fuera* del filtro, entra a la lista de
    preservados y se vuelve inmune al mecanismo que debería repararla: el
    scraper la baja completa y la tira. Caso real 2026-08-17: `Gorro de Pico
    AA10` (203 productos) y `Sombrero de ala plana AA4` (36) llevaban semanas
    en 'General' con precio 0 y sin imágenes, y cada corrida `--category gorra`
    los descargaba y los descartaba.
    """

    FILTER = {"AA10", "AA4", "GORRA"}

    def _prod(self, sku, cat_id, **extra):
        p = {"sku": sku, "category_id": cat_id, "name": sku, "price_mxn": 150.0}
        p.update(extra)
        return p

    def test_producto_de_otra_categoria_se_preserva(self):
        existentes = [self._prod("P1", "ZAPATO")]
        out = merge_scraped_products(existentes, [], self.FILTER)
        self.assertEqual(out, existentes)

    def test_producto_dentro_del_filtro_lo_reemplaza_lo_scrapeado(self):
        existentes = [self._prod("P1", "AA10", price_mxn=0.0)]
        nuevos = [self._prod("P1", "AA10", price_mxn=150.0)]
        out = merge_scraped_products(existentes, nuevos, self.FILTER)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["price_mxn"], 150.0)

    def test_carcasa_con_category_id_none_se_repara(self):
        """El bug exacto: guardado en 'General' con category_id None, la API lo
        devuelve bajo AA10. Debe quedar UNA sola entrada, la buena."""
        carcasa = self._prod("P1", None, category="General",
                             price_mxn=0.0, images=[], status="out_of_stock")
        bueno = self._prod("P1", "AA10", category="Gorro de Pico AA10",
                           price_mxn=150.0, images=["http://x/1.jpg"],
                           status="available")
        out = merge_scraped_products([carcasa], [bueno], self.FILTER)
        self.assertEqual(len(out), 1, "la carcasa sobrevivió al lado del bueno")
        self.assertEqual(out[0]["category_id"], "AA10")
        self.assertEqual(out[0]["price_mxn"], 150.0)
        self.assertEqual(out[0]["images"], ["http://x/1.jpg"])

    def test_no_duplica_cuando_el_producto_cambia_de_categoria(self):
        """Si el proveedor lo movió de una categoría fuera del filtro a una de
        adentro, la copia vieja no puede quedar colgada: sería un sku duplicado."""
        viejo = self._prod("P1", "ZAPATO")
        nuevo = self._prod("P1", "AA10")
        out = merge_scraped_products([viejo], [nuevo], self.FILTER)
        self.assertEqual([p["sku"] for p in out], ["P1"])
        self.assertEqual(out[0]["category_id"], "AA10")

    def test_producto_sin_sku_no_arrastra_a_los_demas(self):
        """Una entrada corrupta sin sku no debe hacer que todos los productos
        sin sku del JSON se consideren 'ya scrapeados' y desaparezcan."""
        sin_sku = self._prod("", "ZAPATO")
        otro_sin_sku = self._prod("", "RELOJ")
        out = merge_scraped_products([sin_sku, otro_sin_sku], [self._prod("", "AA10")],
                                     self.FILTER)
        self.assertIn(sin_sku, out)
        self.assertIn(otro_sin_sku, out)

    def test_sin_filtro_lo_scrapeado_es_el_json_completo(self):
        """Corrida sin --category: no hay nada que preservar."""
        nuevos = [self._prod("P1", "AA10"), self._prod("P2", "ZAPATO")]
        out = merge_scraped_products([self._prod("P9", "VIEJO")], nuevos, set())
        self.assertEqual([p["sku"] for p in out], ["P1", "P2"])


class RevivePendingCommandTests(TestCase):
    """revive_pending: devuelve a la cola los pendientes que se rechazaron
    porque el scraper los había guardado rotos.

    Contexto (2026-08-17): un `category_id` nulo en scraped_modaverse.json dejaba
    productos en 'General', sin imágenes y con precio 0. Se encolaron así, se
    rechazaron a mano por inservibles, y la fila 'rejected' los volvió inmunes a
    `load_productos` — que mete en `existing_urls` todos los pendientes sin mirar
    su estado. Con el JSON ya reparado hay que desbloquearlos explícitamente.

    Solo revive lo que HOY está sano en el JSON: un rechazo sobre un producto que
    sigue roto era una decisión correcta y se respeta.
    """

    URL_OK = 'https://www.modaverse.vip/#/proinfo/PRTESTOK'
    URL_ROTO = 'https://www.modaverse.vip/#/proinfo/PRTESTROTO'
    URL_FUERA = 'https://www.modaverse.vip/#/proinfo/PRTESTFUERA'

    def setUp(self):
        self.general = Category.objects.create(
            name='General', slug='general-rev', shipping_cost=0, profit_margin=100)
        self.gorra = Category.objects.create(
            name='Gorra', slug='gorra-rev', shipping_cost=0, profit_margin=100)
        self.aa10 = Category.objects.create(
            name='Gorro de Pico AA10', slug='aa10-rev', parent=self.gorra,
            shipping_cost=0, profit_margin=100)

        self.pend_ok = PendingProduct.objects.create(
            supplier_url=self.URL_OK, display_name='YAA2825', category=self.general,
            base_price=Decimal('0'), status='rejected',
            reviewed_at=datetime.datetime(2026, 7, 4, tzinfo=datetime.timezone.utc),
            raw_data={'sku': 'RYL-GEN-1', 'image_url': ''},
        )
        self.pend_roto = PendingProduct.objects.create(
            supplier_url=self.URL_ROTO, display_name='ROTO', category=self.general,
            base_price=Decimal('0'), status='rejected',
            raw_data={'sku': 'RYL-GEN-2', 'image_url': ''},
        )
        self.pend_fuera = PendingProduct.objects.create(
            supplier_url=self.URL_FUERA, display_name='FUERA', category=self.general,
            base_price=Decimal('0'), status='rejected',
            raw_data={'sku': 'RYL-GEN-3', 'image_url': ''},
        )

    def _json(self):
        return {
            'categories': [{
                'id': 'CAP', 'name_es': 'Gorra',
                'subcategories': [
                    {'id': 'AA10', 'name_es': 'Gorro de Pico AA10'},
                    {'id': 'AA11', 'name_es': 'Gorro de Pico AA11'},
                ],
            }, {
                'id': 'ZAP', 'name_es': 'zapato', 'subcategories': [],
            }],
            'products': [
                {'sku': 'PRTESTOK', 'category_id': 'AA10', 'category': 'Gorro de Pico AA10',
                 'price_mxn': 150.0, 'images': ['http://x/1.jpg'], 'yn_launch': '1'},
                # sigue roto: sin categoría, sin precio, sin imágenes
                {'sku': 'PRTESTROTO', 'category_id': None, 'category': 'General',
                 'price_mxn': 0.0, 'images': [], 'yn_launch': '1'},
                # sano, pero de otra categoría: fuera del filtro
                {'sku': 'PRTESTFUERA', 'category_id': 'ZAP', 'category': 'zapato',
                 'price_mxn': 300.0, 'images': ['http://x/2.jpg'], 'yn_launch': '1'},
            ],
        }

    def _run(self, *args):
        out = StringIO()
        with patch('catalog.management.commands.revive_pending.read_modaverse_json',
                   return_value=self._json()):
            call_command('revive_pending', '--category', 'gorra', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_por_defecto_no_escribe(self):
        self._run()
        self.pend_ok.refresh_from_db()
        self.assertEqual(self.pend_ok.status, 'rejected')

    def test_revive_el_que_ya_esta_sano(self):
        self._run('--apply')
        self.pend_ok.refresh_from_db()
        self.assertEqual(self.pend_ok.status, 'pending')
        self.assertEqual(self.pend_ok.category, self.aa10)
        self.assertEqual(self.pend_ok.base_price, Decimal('150'))
        self.assertEqual(self.pend_ok.raw_data['image_url'], 'http://x/1.jpg')
        self.assertIsNone(self.pend_ok.reviewed_at)

    def test_no_revive_el_que_sigue_roto(self):
        """Rechazar un producto sin imágenes ni precio fue correcto; se respeta."""
        self._run('--apply')
        self.pend_roto.refresh_from_db()
        self.assertEqual(self.pend_roto.status, 'rejected')

    def test_no_toca_lo_de_otra_categoria(self):
        self._run('--apply')
        self.pend_fuera.refresh_from_db()
        self.assertEqual(self.pend_fuera.status, 'rejected')

    def test_no_toca_aprobados_ni_pendientes(self):
        self.pend_ok.status = 'approved'
        self.pend_ok.save(update_fields=['status'])
        self._run('--apply')
        self.pend_ok.refresh_from_db()
        self.assertEqual(self.pend_ok.status, 'approved')

    def test_no_revive_si_el_producto_ya_esta_en_el_catalogo(self):
        """Si ya existe el Product, revivir el pendiente lo duplicaría."""
        Product.objects.create(sku='RYL-AA10-1', name='YAA2825', category=self.aa10,
                               base_price=Decimal('150'), supplier_url=self.URL_OK)
        self._run('--apply')
        self.pend_ok.refresh_from_db()
        self.assertEqual(self.pend_ok.status, 'rejected')

    def test_reporta_cuantos_reviviria(self):
        salida = self._run()
        self.assertIn('1', salida)


class OverrideMargenVsTierTests(TestCase):
    """No se puede dejar la ganancia de una subcategoría por debajo del mayor
    descuento por volumen de su raíz: a esa cantidad el precio caería por
    debajo del costo.

    Es la configuración que produjo los 2 pedidos de alfileres en $0 el
    2026-08-16 (`Alfileres` margen 20 bajo `Gorra` con tier −$70). El piso de
    `_price_with_volume_tier` ya impide el daño; esto lo ataja antes, en el
    panel, donde se comete el error.
    """

    def setUp(self):
        from catalog.models import VolumeTier
        self.raiz = Category.objects.create(
            name='Gorra Val', slug='gorra-val', profit_margin=Decimal('100'))
        VolumeTier.objects.create(
            category=self.raiz, min_qty=50, discount_amount=Decimal('50'))
        VolumeTier.objects.create(
            category=self.raiz, min_qty=100, discount_amount=Decimal('70'))

    def _sub(self, margen):
        return Category(
            name='Alfileres Val', slug='alfileres-val', parent=self.raiz,
            base_price_override=Decimal('55'), profit_margin_override=margen,
        )

    def test_rechaza_margen_menor_al_mayor_tier(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._sub(Decimal('20')).full_clean()
        self.assertIn('profit_margin_override', ctx.exception.error_dict)

    def test_el_mensaje_dice_las_dos_cifras_y_la_raiz(self):
        from django.core.exceptions import ValidationError
        try:
            self._sub(Decimal('20')).full_clean()
            self.fail('debió rechazar')
        except ValidationError as e:
            msg = ' '.join(e.message_dict['profit_margin_override'])
        self.assertIn('20', msg)
        self.assertIn('70', msg)
        self.assertIn('Gorra Val', msg)

    def test_acepta_margen_igual_al_mayor_tier(self):
        """Justo en el límite el precio queda al costo, no por debajo."""
        self._sub(Decimal('70')).full_clean()

    def test_acepta_margen_mayor(self):
        self._sub(Decimal('120')).full_clean()

    def test_sin_tiers_en_la_raiz_cualquier_margen_vale(self):
        otra = Category.objects.create(name='Reloj Val', slug='reloj-val')
        Category(name='Sub Reloj', slug='sub-reloj', parent=otra,
                 profit_margin_override=Decimal('5')).full_clean()

    def test_no_estorba_cuando_no_hay_override_de_margen(self):
        """Un override solo de precio base no toca la ganancia: no se valida."""
        Category(name='Solo Base', slug='solo-base', parent=self.raiz,
                 base_price_override=Decimal('55')).full_clean()

    def test_raiz_sin_subcategorias_se_compara_con_sus_propios_tiers(self):
        from catalog.models import VolumeTier
        suelta = Category.objects.create(name='Suelta', slug='suelta')
        VolumeTier.objects.create(
            category=suelta, min_qty=10, discount_amount=Decimal('80'))
        suelta.profit_margin_override = Decimal('30')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            suelta.full_clean()


from catalog.models import AliasTexto
from catalog.services import normalizar_texto


class NormalizarTextoTests(TestCase):
    def test_minusculas_y_espacios_colapsados(self):
        self.assertEqual(normalizar_texto('  Jordan   4 '), 'jordan 4')

    def test_vacio_y_none(self):
        self.assertEqual(normalizar_texto(''), '')
        self.assertEqual(normalizar_texto(None), '')


class AliasTextoTests(TestCase):
    def setUp(self):
        self.tipo = TipoArticulo.objects.create(
            nombre='JORDAN 4', keywords='jordan 4', costo=Decimal('680'))

    def test_normaliza_el_texto_al_guardar(self):
        alias = AliasTexto.objects.create(texto='  Jordan  ', tipo=self.tipo)
        alias.refresh_from_db()
        self.assertEqual(alias.texto, 'jordan')

    def test_el_texto_es_unico_despues_de_normalizar(self):
        AliasTexto.objects.create(texto='Jordan', tipo=self.tipo)
        # `atomic` es obligatorio: sin él la IntegrityError deja la transacción
        # del TestCase rota y el siguiente query de la clase revienta.
        with self.assertRaises(IntegrityError), transaction.atomic():
            AliasTexto.objects.create(texto='  JORDAN ', tipo=self.tipo)

from catalog.services import sugerencias_de_tipo


class SugerenciasDeTipoTests(TestCase):
    def setUp(self):
        self.j4 = TipoArticulo.objects.create(
            nombre='JORDAN 4', keywords='jordan 4', costo=Decimal('680'))
        self.j1 = TipoArticulo.objects.create(
            nombre='Jordan 1', keywords='jordan 1, J1', costo=Decimal('620'))
        self.yeezy = TipoArticulo.objects.create(
            nombre='Tenis yeezy', keywords='yeezy', costo=Decimal('550'))
        self.sud = TipoArticulo.objects.create(
            nombre='Sudadera 1:1', keywords='sudadera 1:1, s 1:1', costo=Decimal('320'))

    def test_jordan_suelto_ofrece_los_dos_jordan(self):
        nombres = [t.nombre for t in sugerencias_de_tipo('Jordan')]
        self.assertIn('JORDAN 4', nombres)
        self.assertIn('Jordan 1', nombres)

    def test_puntua_contra_las_keywords_no_solo_contra_el_nombre(self):
        # '1:1' contra el NOMBRE «Sudadera 1:1» da 0.40 — debajo del piso 0.45,
        # así que una implementación que puntuara solo contra el nombre
        # devolvería vacío. Contra la keyword 's 1:1' da 0.75. Este caso es el
        # que separa la implementación correcta de la rota; con 'yezzy' no se
        # separaban, porque su nombre ya puntuaba 0.5 y pasaba igual.
        nombres = [t.nombre for t in sugerencias_de_tipo('1:1')]
        self.assertEqual(nombres, ['Sudadera 1:1'])

    def test_sin_parecido_devuelve_vacio(self):
        self.assertEqual(sugerencias_de_tipo('qwxzpl'), [])

    def test_un_typo_encuentra_su_tipo(self):
        nombres = [t.nombre for t in sugerencias_de_tipo('yezzy')]
        self.assertEqual(nombres[0], 'Tenis yeezy')

    def test_respeta_el_limite(self):
        self.assertEqual(len(sugerencias_de_tipo('jordan', limite=1)), 1)

    def test_texto_vacio_devuelve_vacio(self):
        self.assertEqual(sugerencias_de_tipo('   '), [])
