from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from inventario.models import Color, TallaCalzado, TarifaFlete


class CatalogosTests(TestCase):
    def test_siembra_13_tallas_con_25_5_y_27_5(self):
        self.assertEqual(TallaCalzado.objects.count(), 13)
        self.assertIsNone(TallaCalzado.objects.get(mx_cm=Decimal('25.5')).eu)
        self.assertIsNone(TallaCalzado.objects.get(mx_cm=Decimal('27.5')).eu)
        t = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.assertEqual(t.eu, Decimal('41'))
        self.assertEqual(t.us_hombre, Decimal('8.5'))
        self.assertEqual(t.cn_mm, 260)
        mx = list(TallaCalzado.objects.order_by('orden').values_list('mx_cm', flat=True))
        self.assertEqual(mx, sorted(mx))

    def test_mx_cm_es_unico(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TallaCalzado.objects.create(mx_cm=Decimal('26'))

    def test_color_normaliza_nombre_y_alias(self):
        c = Color.objects.create(nombre='  Café  Oscuro', alias='Brown, MARRÓN')
        self.assertEqual(c.nombre, 'cafe oscuro')
        self.assertEqual(c.alias_list, ['brown', 'marron'])

    def test_color_unico_tras_normalizar(self):
        Color.objects.create(nombre='Negro')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Color.objects.create(nombre='NEGRO')

    def test_tarifas_sembradas(self):
        self.assertEqual(TarifaFlete.objects.get(via='aereo').monto_por_par, Decimal('280'))
        self.assertEqual(TarifaFlete.objects.get(via='maritimo').monto_por_par, Decimal('180'))
