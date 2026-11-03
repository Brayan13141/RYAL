from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import TipoArticulo
from inventario.models import Color, Par, TallaCalzado, TarifaFlete
from inventario.services.lotes import cerrar_lote, crear_lote
from inventario.services.tipos import crear_variante


class LotesBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('staff', password='x')
        self.base = TipoArticulo(
            nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.base.full_clean()
        self.base.save()
        self.color = Color.objects.create(nombre='negro')
        self.base.colores.add(self.color)
        self.var_a = crear_variante(self.base, 'aereo', costo=Decimal('640'))
        self.var_m = crear_variante(self.base, 'maritimo', costo=Decimal('540'))
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.t27 = TallaCalzado.objects.get(mx_cm=Decimal('27'))

    def linea(self, tipo=None, color=None, talla=None, cantidad=2, precio='360'):
        return {
            'tipo': tipo or self.var_a, 'color': color or self.color,
            'talla': talla or self.t26, 'cantidad': cantidad,
            'precio_proveedor': None if precio is None else Decimal(precio)}

    def crear(self, via='aereo', origen='manual', lineas=None):
        return crear_lote(
            via=via, origen=origen, lineas=lineas or [self.linea()],
            usuario=self.user, referencia='L1')


class CrearLoteTests(LotesBase):
    def test_costo_manual_aereo(self):
        lote = self.crear()
        self.assertEqual(lote.flete_por_par, Decimal('280'))
        self.assertEqual(lote.pares.count(), 2)
        p = lote.pares.first()
        self.assertEqual(p.costo, Decimal('640.00'))
        self.assertEqual(p.precio_proveedor, Decimal('360.00'))
        self.assertEqual(p.flete, Decimal('280.00'))
        self.assertEqual(p.estado, 'por_recibir')
        self.assertEqual(lote.creado_por, self.user)

    def test_costo_manual_maritimo(self):
        lote = self.crear(via='maritimo', lineas=[self.linea(tipo=self.var_m)])
        self.assertEqual(lote.pares.first().costo, Decimal('540.00'))

    def test_flete_del_lote_no_cambia_si_cambia_la_tarifa(self):
        lote = self.crear()
        TarifaFlete.objects.filter(via='aereo').update(monto_por_par=Decimal('999'))
        lote.refresh_from_db()
        self.assertEqual(lote.flete_por_par, Decimal('280'))
        self.assertEqual(lote.pares.first().flete, Decimal('280'))

    def test_inicial_toma_costo_del_tipo_sin_desglose(self):
        lote = self.crear(origen='inicial', lineas=[self.linea(precio=None)])
        p = lote.pares.first()
        self.assertEqual(p.costo, Decimal('640.00'))
        self.assertIsNone(p.precio_proveedor)
        self.assertIsNone(p.flete)

    def test_movimiento_de_alta_por_par(self):
        lote = self.crear()
        for p in lote.pares.all():
            m = p.movimientos.get()
            self.assertEqual((m.de_estado, m.a_estado, m.origen), ('', 'por_recibir', 'alta'))
            self.assertEqual(m.usuario, self.user)

    def test_codigos_unicos_con_prefijo_de_la_variante(self):
        lote = self.crear(lineas=[self.linea(cantidad=3), self.linea(talla=self.t27)])
        codigos = list(lote.pares.values_list('codigo', flat=True))
        self.assertEqual(len(set(codigos)), 5)
        for c in codigos:
            self.assertTrue(c.startswith(self.var_a.prefijo + '-'))

    def test_variante_de_otra_via_falla_y_no_crea_nada(self):
        with self.assertRaisesMessage(ValueError, self.var_m.nombre):
            self.crear(lineas=[self.linea(), self.linea(tipo=self.var_m)])
        self.assertEqual(Par.objects.count(), 0)

    def test_tipo_base_falla(self):
        with self.assertRaises(ValueError):
            self.crear(lineas=[self.linea(tipo=self.base)])

    def test_color_no_asignado_falla(self):
        otro = Color.objects.create(nombre='rojo')
        with self.assertRaises(ValueError):
            self.crear(lineas=[self.linea(color=otro)])
        self.assertEqual(Par.objects.count(), 0)

    def test_cantidad_invalida_falla(self):
        for c in (0, -1):
            with self.assertRaises(ValueError):
                self.crear(lineas=[self.linea(cantidad=c)])

    def test_precio_obligatorio_salvo_inicial(self):
        with self.assertRaises(ValueError):
            self.crear(lineas=[self.linea(precio=None)])

    def test_sin_lineas_falla(self):
        with self.assertRaises(ValueError):
            crear_lote(via='aereo', origen='manual', lineas=[], usuario=self.user)


class CerrarLoteTests(LotesBase):
    def test_marca_faltantes_y_es_idempotente(self):
        lote = self.crear(lineas=[self.linea(cantidad=3)])
        recibido = lote.pares.first()
        recibido.transicionar('en_stock', usuario=self.user, origen='escaner')
        self.assertEqual(cerrar_lote(lote, usuario=self.user), 2)
        lote.refresh_from_db()
        self.assertIsNotNone(lote.cerrado_at)
        faltantes = lote.pares.filter(estado='baja')
        self.assertEqual(faltantes.count(), 2)
        for p in faltantes:
            self.assertEqual(p.motivo_baja, 'faltante')
            self.assertEqual(p.movimientos.last().origen, 'cierre_lote')
        recibido.refresh_from_db()
        self.assertEqual(recibido.estado, 'en_stock')
        primero = lote.cerrado_at
        self.assertEqual(cerrar_lote(lote, usuario=self.user), 0)
        lote.refresh_from_db()
        self.assertEqual(lote.cerrado_at, primero)
