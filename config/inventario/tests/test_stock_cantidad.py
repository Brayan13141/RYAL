from decimal import Decimal

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import TestCase

from catalog.models import TipoArticulo
from negocio.forms import TipoArticuloForm

MENSAJE = 'Un tipo con stock por cantidad no puede tener vía, tallas ni variantes.'


def _tipo(nombre, keywords, **kw):
    return TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal('100'), **kw)


class TipoStockCantidadCleanTests(TestCase):
    def assertErrorEn(self, tipo, campo):
        with self.assertRaises(ValidationError) as cm:
            tipo.full_clean()
        self.assertIn(campo, cm.exception.message_dict)
        self.assertIn(MENSAJE, cm.exception.message_dict[campo])

    def test_no_puede_ser_variante(self):
        base = _tipo('Jordan 4', 'jordan 4')
        base.full_clean()
        base.save()
        tipo = _tipo('Gorra A', 'gorra aereo', padre=base, via='aereo',
                     usa_tallas=True, stock_por_cantidad=True)
        with self.assertRaises(ValidationError) as cm:
            tipo.full_clean()
        self.assertIn('padre', cm.exception.message_dict)
        self.assertIn('via', cm.exception.message_dict)
        self.assertIn(MENSAJE, cm.exception.message_dict['padre'])
        self.assertIn(MENSAJE, cm.exception.message_dict['via'])

    def test_no_puede_usar_tallas(self):
        self.assertErrorEn(
            _tipo('Gorra', 'gorra', usa_tallas=True, stock_por_cantidad=True),
            'usa_tallas')

    def test_no_puede_tener_variantes(self):
        base = _tipo('Gorra', 'gorra')
        base.full_clean()
        base.save()
        var = _tipo('Gorra A', 'gorra aereo', padre=base, via='aereo', usa_tallas=True)
        var.full_clean()
        var.save()
        base.stock_por_cantidad = True
        self.assertErrorEn(base, NON_FIELD_ERRORS)

    def test_tipo_simple_valido(self):
        _tipo('Gorra', 'gorra', stock_por_cantidad=True).full_clean()

    def test_form_con_usa_tallas_es_invalido_sin_lanzar(self):
        tipo = _tipo('Gorra', 'gorra', stock_por_cantidad=True)
        tipo.full_clean()
        tipo.save()
        form = TipoArticuloForm(
            data={'nombre': 'Gorra', 'keywords': 'gorra', 'costo': '100',
                  'usa_tallas': 'on'},
            instance=tipo)
        self.assertFalse(form.is_valid())
        self.assertIn('usa_tallas', form.errors)


# --- Servicio de stock por cantidad (Task 2) ---------------------------------
from datetime import timedelta  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from inventario.models import Color, Lote, MovimientoCantidad, Par, TallaCalzado  # noqa: E402
from inventario.services import cantidad as svc  # noqa: E402
from negocio.models import Cliente, Pedido, PedidoItem  # noqa: E402
from negocio.services import MOSTRADOR_TELEFONO  # noqa: E402


class CantidadServicioTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user('vend', password='x')
        self.t = _tipo('Gorra', 'gorra')
        self.t.full_clean()
        self.t.save()
        self.cliente = Cliente.objects.create(telefono=MOSTRADOR_TELEFONO, nombre='Mostrador')

    def _item(self, qty=1, sku='GORRA'):
        pedido = Pedido.objects.create(
            cliente=self.cliente, descripcion='', costo_producto=Decimal('0'),
            precio_venta=Decimal('0'), envio=Decimal('0'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA)
        return PedidoItem.objects.create(
            pedido=pedido, sku_snapshot=sku, nombre_snapshot='Gorra', cantidad=qty,
            costo_unitario=Decimal('100'), precio_unitario=Decimal('200'))

    def _activo(self, n=999):
        svc.activar(self.t, n, usuario=self.u)

    def test_activar_arranca_en_999(self):
        mov = svc.activar(self.t, 999, usuario=self.u)
        self.assertEqual(svc.stock_actual(self.t), 999)
        self.assertEqual(mov.clase, MovimientoCantidad.Clase.AJUSTE)
        self.assertEqual(mov.motivo, svc.MOTIVO_ALTA)
        self.assertEqual(mov.cantidad_anterior, 0)

    def test_activar_rechaza_tipo_con_pares(self):
        Par.objects.create(
            codigo='GORRA-0001', tipo=self.t, color=Color.objects.create(nombre='negro'),
            talla=TallaCalzado.objects.get_or_create(mx_cm=Decimal('27.0'))[0],
            lote=Lote.objects.create(
                referencia='L1', origen='manual', via='aereo', flete_por_par=Decimal('280')),
            costo=Decimal('1'))
        with self.assertRaises(ValueError):
            svc.activar(self.t, 999, usuario=self.u)
        self.t.refresh_from_db()
        self.assertFalse(self.t.stock_por_cantidad)

    def test_activar_dos_veces_falla(self):
        self._activo()
        with self.assertRaises(ValueError):
            svc.activar(self.t, 5, usuario=self.u)

    def test_venta_descuenta(self):
        self._activo()
        svc.registrar_salida(self._item(3), self.t)
        self.assertEqual(svc.stock_actual(self.t), 996)

    def test_stock_puede_ser_negativo(self):
        self._activo(0)
        svc.registrar_salida(self._item(1), self.t)
        self.assertEqual(svc.stock_actual(self.t), -1)

    def test_cancelar_devuelve(self):
        self._activo()
        item = self._item(3)
        svc.registrar_salida(item, self.t)
        item.pedido.estado = Pedido.CANCELADO
        item.pedido.save()
        self.assertEqual(svc.stock_actual(self.t), 999)

    def test_ajuste_fija_total_y_guarda_anterior(self):
        self._activo()
        svc.registrar_salida(self._item(4), self.t)
        mov = svc.ajustar(self.t, 40, motivo='conté', usuario=self.u)
        self.assertEqual(svc.stock_actual(self.t), 40)
        self.assertEqual(mov.cantidad_anterior, 995)
        self.assertEqual(mov.cantidad_nueva, 40)
        self.assertEqual(mov.usuario, self.u)

    def test_ventas_previas_al_ajuste_no_cuentan(self):
        self._activo()
        item = self._item(4)
        svc.registrar_salida(item, self.t)
        svc.ajustar(self.t, 40, motivo='conté', usuario=self.u)
        self.assertEqual(svc.stock_actual(self.t), 40)
        item.pedido.estado = Pedido.CANCELADO
        item.pedido.save()
        self.assertEqual(svc.stock_actual(self.t), 40)

    def test_ajuste_sin_motivo(self):
        self._activo()
        with self.assertRaises(ValueError):
            svc.ajustar(self.t, 10, motivo='  ', usuario=self.u)
        self.assertEqual(svc.stock_actual(self.t), 999)

    def test_ajuste_negativo(self):
        self._activo()
        with self.assertRaises(ValueError):
            svc.ajustar(self.t, -1, motivo='x', usuario=self.u)
        self.assertEqual(svc.stock_actual(self.t), 999)

    def test_ajuste_tipo_no_activo(self):
        with self.assertRaises(ValueError):
            svc.ajustar(self.t, 5, motivo='x', usuario=self.u)

    def test_tipo_no_activo_no_registra_salida(self):
        self.assertIsNone(svc.registrar_salida(self._item(1), self.t))
        self.assertFalse(MovimientoCantidad.objects.exists())

    def test_cambiar_keyword_no_mueve_stock(self):
        self._activo()
        svc.registrar_salida(self._item(2), self.t)
        self.t.keywords = 'gorra nueva'
        self.t.save()
        self.assertEqual(svc.stock_actual(self.t), 997)

    def test_sincronizar_salida(self):
        self._activo()
        item = self._item(2)
        svc.registrar_salida(item, self.t)
        self.assertEqual(svc.stock_actual(self.t), 997)
        item.cantidad = 1
        item.save()
        svc.sincronizar_salida(item)
        self.assertEqual(svc.stock_actual(self.t), 998)

    def test_borrar_item_devuelve(self):
        self._activo()
        item = self._item(2)
        svc.registrar_salida(item, self.t)
        item.delete()
        self.assertEqual(svc.stock_actual(self.t), 999)

    def test_codigo_y_lectura(self):
        self._activo()
        self.assertEqual(svc.tipo_de_codigo(svc.codigo_etiqueta(self.t)), self.t)
        self.assertIsNone(svc.tipo_de_codigo('TIPO.999999'))
        self.assertIsNone(svc.tipo_de_codigo('GORRA-AAAA'))
        otro = _tipo('Otro', 'otro')
        otro.full_clean()
        otro.save()
        self.assertIsNone(svc.tipo_de_codigo(svc.codigo_etiqueta(otro)))

    def test_venta_bot_reciente(self):
        self._activo()
        bot = self._item(1, sku='TIENDA-BOT')
        svc.registrar_salida(bot, self.t)
        self.assertTrue(svc.venta_bot_reciente(self.t))
        MovimientoCantidad.objects.filter(pedido_item=bot).update(
            creado_at=timezone.now() - timedelta(minutes=16))
        self.assertFalse(svc.venta_bot_reciente(self.t))
        svc.registrar_salida(self._item(1, sku='GORRA'), self.t)
        self.assertFalse(svc.venta_bot_reciente(self.t))


class VentaBotCantidadTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user('vend_bot', password='x')
        self.t = _tipo('Gorra Barbas', 'gorra barbas')
        self.t.full_clean()
        self.t.save()
        self.items = [{'description': 'gorra barbas', 'price': 250, 'qty': 2}]

    def test_bot_descuenta(self):
        from negocio.services import crear_pedido_tienda_bot
        svc.activar(self.t, 999, usuario=self.u)
        pedido = crear_pedido_tienda_bot(items=self.items)
        self.assertEqual(svc.stock_actual(self.t), 997)
        item = pedido.items.get()
        self.assertEqual(item.salida_cantidad.unidades, 2)

    def test_bot_tipo_no_activo_no_crea_movimiento(self):
        from negocio.services import crear_pedido_tienda_bot
        crear_pedido_tienda_bot(items=self.items)
        self.assertEqual(MovimientoCantidad.objects.count(), 0)

    def test_venta_rechazada_no_descuenta(self):
        from negocio.services import VentaSinTipo, crear_pedido_tienda_bot
        svc.activar(self.t, 999, usuario=self.u)
        with self.assertRaises(VentaSinTipo):
            crear_pedido_tienda_bot(items=self.items + [
                {'description': 'zzz inexistente qqq', 'price': 10, 'qty': 1}])
        self.assertEqual(svc.stock_actual(self.t), 999)


class EditarItemCantidadTests(TestCase):
    def setUp(self):
        from django.urls import reverse
        from negocio.services import crear_pedido_tienda_bot
        self.staff = get_user_model().objects.create_user(
            'staff_edit', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.t = _tipo('Gorra Barbas', 'gorra barbas')
        self.t.full_clean()
        self.t.save()
        svc.activar(self.t, 999, usuario=self.staff)
        pedido = crear_pedido_tienda_bot(
            items=[{'description': 'gorra barbas', 'price': 250, 'qty': 2}])
        self.item = pedido.items.get()
        self.url = reverse('negocio:pedido_item_edit', args=[self.item.pk])

    def _datos(self, **kw):
        datos = {
            'nombre_snapshot': self.item.nombre_snapshot,
            'cantidad': self.item.cantidad,
            'costo_unitario': self.item.costo_unitario,
            'precio_unitario': self.item.precio_unitario,
        }
        datos.update(kw)
        return datos

    def test_editar_cantidad_devuelve(self):
        self.assertEqual(svc.stock_actual(self.t), 997)
        resp = self.client.post(self.url, self._datos(cantidad=1))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(svc.stock_actual(self.t), 998)

    def test_editar_nombre_no_cambia_tipo(self):
        resp = self.client.post(
            self.url, self._datos(nombre_snapshot='gorra new era'))
        self.assertEqual(resp.status_code, 302)
        mov = MovimientoCantidad.objects.get(pedido_item=self.item)
        self.assertEqual(mov.tipo_id, self.t.pk)
        self.assertEqual(svc.stock_actual(self.t), 997)


# --- Ronda final de correcciones ---------------------------------------------
from django.urls import reverse  # noqa: E402

from inventario.services.tipos import crear_variante  # noqa: E402


class TipoActivoNoRecibeVariantesTests(TestCase):
    def setUp(self):
        self.gorra = _tipo('Gorra', 'gorra', stock_por_cantidad=True)
        self.gorra.full_clean()
        self.gorra.save()

    def test_form_variante_con_padre_activo_es_invalido(self):
        form = TipoArticuloForm(data={
            'nombre': 'Gorra A', 'keywords': 'gorra aereo', 'costo': '100',
            'padre': self.gorra.pk, 'via': 'aereo', 'usa_tallas': 'on'})
        self.assertFalse(form.is_valid())
        self.assertIn(MENSAJE, form.errors['padre'])

    def test_convertir_no_lista_ni_acepta_base_activa(self):
        staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(staff)
        url = reverse('inventario:tipos_convertir')
        r = self.client.get(url)
        self.assertNotIn(self.gorra, [f['base'] for f in r.context['filas']])
        self.client.post(url, {'crear': [f'{self.gorra.pk}:aereo']})
        self.assertFalse(TipoArticulo.objects.filter(padre=self.gorra).exists())
        self.gorra.refresh_from_db()
        self.assertFalse(self.gorra.usa_tallas)

    def test_crear_variante_de_base_activa_falla(self):
        with self.assertRaises(ValidationError):
            crear_variante(self.gorra, 'aereo')
        self.gorra.refresh_from_db()
        self.assertFalse(self.gorra.usa_tallas)


class TipoDeCodigoLargoTests(TestCase):
    def test_mas_de_nueve_digitos_es_none_sin_consultar(self):
        # Sin tope, un pk enorme llega a la BD (OverflowError/DataError según el motor).
        with self.assertNumQueries(0):
            self.assertIsNone(svc.tipo_de_codigo('TIPO.' + '9' * 10))
            self.assertIsNone(svc.tipo_de_codigo('TIPO.' + '9' * 30))
