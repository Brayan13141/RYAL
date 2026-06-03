from decimal import Decimal

from django.test import TestCase

from catalog.management.commands.import_images import _pid_from_url
from catalog.modaverse import parse_specifications
from catalog.management.commands.load_productos import (
    _category_filter_ids,
    _build_existing_pids,
)
from catalog.models import Category, Product
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
