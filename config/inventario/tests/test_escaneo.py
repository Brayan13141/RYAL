from decimal import Decimal

from inventario.models import MovimientoPar
from inventario.services.escaneo import Resultado, deshacer, extraer_codigo, recibir
from inventario.services.lotes import cerrar_lote
from inventario.tests.test_lotes import LotesBase


class ExtraerCodigoTests(LotesBase):
    def test_extraer_codigo_de_url_minusculas_y_espacios(self):
        self.assertEqual(
            extraer_codigo('  https://ryalsneackers.com/panel/inventario/p/jd4a-k7m3/ '),
            'JD4A-K7M3')

    def test_codigo_suelto_y_url_sin_slash_final(self):
        self.assertEqual(extraer_codigo(' jd4a-k7m3 '), 'JD4A-K7M3')
        self.assertEqual(
            extraer_codigo('http://localhost:8000/panel/inventario/p/jd4a-k7m3'), 'JD4A-K7M3')

    def test_vacio(self):
        self.assertEqual(extraer_codigo('   '), '')
        self.assertEqual(extraer_codigo(''), '')


class RecibirTests(LotesBase):
    def setUp(self):
        super().setUp()
        self.lote = self.crear()
        self.par = self.lote.pares.first()

    def test_por_recibir_es_verde(self):
        r = recibir(self.par.codigo.lower(), lote=self.lote, usuario=self.user)
        self.assertIsInstance(r, Resultado)
        self.assertEqual(r.nivel, 'verde')
        self.assertEqual(r.par, self.par)
        self.assertEqual(r.par.estado, 'en_stock')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')
        mov = self.par.movimientos.get(a_estado='en_stock')
        self.assertEqual(r.movimiento_id, mov.pk)
        self.assertEqual(mov.origen, 'escaner')

    def test_lectura_concurrente_con_par_viejo_da_amarillo(self):
        # Otra lectura ya recibió el par; la instancia en memoria quedó vieja.
        vieja = type(self.par).objects.get(pk=self.par.pk)
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual(vieja.estado, 'por_recibir')
        r = recibir(vieja.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual((r.nivel, r.mensaje), ('amarillo', 'Ya estaba recibido'))
        self.assertEqual(self.par.movimientos.filter(a_estado='en_stock').count(), 1)

    def test_codigo_inexistente_rojo(self):
        r = recibir('ZZZZ-AAAA', lote=self.lote, usuario=self.user)
        self.assertEqual((r.nivel, r.mensaje), ('rojo', 'Código no existe'))
        self.assertIsNone(r.par)
        self.assertIsNone(r.movimiento_id)

    def test_vacio_rojo(self):
        self.assertEqual(recibir('  ', lote=self.lote, usuario=self.user).nivel, 'rojo')

    def test_otro_lote_rojo(self):
        otro = self.crear()
        r = recibir(self.par.codigo, lote=otro, usuario=self.user)
        self.assertEqual(r.nivel, 'rojo')
        self.assertEqual(r.mensaje, f'Este par es del lote {self.lote.referencia}')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'por_recibir')

    def test_segunda_lectura_amarillo_y_un_solo_movimiento(self):
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual((r.nivel, r.mensaje), ('amarillo', 'Ya estaba recibido'))
        self.assertIsNone(r.movimiento_id)
        self.assertEqual(self.par.movimientos.filter(a_estado='en_stock').count(), 1)

    def test_aparecio_faltante_en_lote_cerrado(self):
        cerrar_lote(self.lote, usuario=self.user)
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'baja')
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual((r.nivel, r.mensaje), ('verde', 'Apareció'))
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')

    def test_baja_danado_es_rojo(self):
        self.par.transicionar('en_stock', usuario=self.user, origen='escaner')
        self.par.transicionar(
            'baja', usuario=self.user, origen='manual', motivo='danado', nota='roto')
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual(r.nivel, 'rojo')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'baja')

    def test_vendido_es_rojo(self):
        self.par.transicionar('en_stock', usuario=self.user, origen='escaner')
        self.par.transicionar('vendido', usuario=self.user, origen='escaner')
        self.assertEqual(
            recibir(self.par.codigo, lote=self.lote, usuario=self.user).nivel, 'rojo')


class DeshacerTests(LotesBase):
    def setUp(self):
        super().setUp()
        self.lote = self.crear()
        self.par = self.lote.pares.first()

    def test_deshace_recepcion(self):
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        d = deshacer(r.movimiento_id, usuario=self.user, nota='Lectura por error')
        self.assertEqual(d.nivel, 'verde')
        self.assertEqual(d.par.estado, 'por_recibir')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'por_recibir')
        ultimo = self.par.movimientos.last()
        self.assertEqual((ultimo.origen, ultimo.nota), ('deshacer', 'Lectura por error'))
        self.assertEqual(d.movimiento_id, ultimo.pk)

    def test_sin_nota_rojo(self):
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        d = deshacer(r.movimiento_id, usuario=self.user, nota='  ')
        self.assertEqual(d.nivel, 'rojo')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')

    def test_no_es_el_ultimo_rojo(self):
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.par.transicionar('vendido', usuario=self.user, origen='escaner')
        d = deshacer(r.movimiento_id, usuario=self.user, nota='x')
        self.assertEqual(d.nivel, 'rojo')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'vendido')

    def test_movimiento_inexistente_rojo(self):
        self.assertEqual(deshacer(999999, usuario=self.user, nota='x').nivel, 'rojo')

    def test_movimiento_de_venta_no_se_deshace_aqui(self):
        self.par.transicionar('en_stock', usuario=self.user, origen='escaner')
        mov = self.par.transicionar('vendido', usuario=self.user, origen='escaner')
        d = deshacer(mov.pk, usuario=self.user, nota='x')
        self.assertEqual((d.nivel, d.mensaje), ('rojo', 'Esta lectura no se deshace aquí'))

    def test_aparecio_no_se_puede_deshacer(self):
        cerrar_lote(self.lote, usuario=self.user)
        r = recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        d = deshacer(r.movimiento_id, usuario=self.user, nota='x')
        self.assertEqual((d.nivel, d.mensaje), ('rojo', 'No se puede deshacer esta lectura'))
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')
