from django.test import TestCase

from catalog.management.commands.import_images import _pid_from_url
from catalog.management.commands.load_productos import (
    _category_filter_ids,
    _build_existing_pids,
)


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
