"""Ola final tras la revisión de toda la rama (I1-I4, M1, M2, M5, M8, M10)."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.forms import TarifaFleteForm
from inventario.models import Color, Lote, Par, TallaCalzado, TarifaFlete
from inventario.services.bot import parsear_venta_tenis
from inventario.tests.test_venta_bot import VentaBotBase
from inventario.tests.test_vistas_escaner import EscanerBase
from inventario.tests.test_vistas_importar import ImportarVistaBase, _firmar
from inventario.tests.test_vistas_lotes import LotesVistaBase
from negocio.models import Pedido


def _sin_ver_el_token(llamadas):
    """`Lote.objects.filter` que no ve el lote en la primera consulta por token_sha
    (simula que el otro POST todavía no había confirmado)."""
    original = Lote.objects.filter

    def filtro(*a, **kw):
        llamadas['n'] += 1
        if llamadas['n'] == 1 and 'token_sha' in kw:
            return Lote.objects.none()
        return original(*a, **kw)
    return filtro


class I1VarianteSinTallasTests(TestCase):
    def test_variante_con_via_sin_tallas_no_valida(self):
        base = TipoArticulo.objects.create(nombre='Jordan 4', keywords='jordan 4', costo=1)
        v = TipoArticulo(nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=1,
                         padre=base, via='aereo', usa_tallas=False, prefijo='JD4A')
        with self.assertRaises(ValidationError) as cm:
            v.full_clean()
        self.assertIn('usa_tallas', cm.exception.message_dict)

    def test_selector_manual_excluye_variantes_sin_tallas(self):
        from inventario.views.lotes import _modelos
        base = TipoArticulo.objects.create(nombre='Jordan 4', keywords='jordan 4', costo=1)
        TipoArticulo.objects.create(
            nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=1, padre=base,
            via='aereo', usa_tallas=False, prefijo='JD4A')
        self.assertEqual([m['variantes'] for m in _modelos()], [{}])


class I2ImportarDobleClicTests(ImportarVistaBase):
    def confirmar(self, token):
        return self.client.post(reverse('inventario:importar_confirmar'), {'token': token})

    def test_confirmar_dos_veces_un_solo_lote(self):
        token = _firmar(self.bueno())
        r1 = self.confirmar(token)
        self.assertEqual(r1.status_code, 302)
        r2 = self.confirmar(token)
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r1.url, r2.url)
        self.assertEqual(Lote.objects.count(), 1)
        self.assertEqual(Par.objects.count(), 2)

    def test_carrera_integrity_error_no_es_500(self):
        token = _firmar(self.bueno())
        self.confirmar(token)
        lote = Lote.objects.get()
        with mock.patch.object(
                Lote.objects, 'filter', side_effect=_sin_ver_el_token({'n': 0})):
            r = self.confirmar(token)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('inventario:lote_detalle', args=[lote.pk]))
        self.assertEqual(Lote.objects.count(), 1)
        self.assertEqual(Par.objects.count(), 2)

    def test_boton_se_deshabilita_al_enviar(self):
        r = self.subir(self.bueno())
        self.assertContains(r, 'disabled = true')


class I2AltaManualDobleClicTests(LotesVistaBase):
    def test_mismo_client_token_dos_veces_un_lote(self):
        d1 = self.post_alta(client_token='abc-123').json()
        d2 = self.post_alta(client_token='abc-123').json()
        self.assertTrue(d1['ok'] and d2['ok'])
        self.assertEqual(d1['lote_id'], d2['lote_id'])
        self.assertTrue(d2.get('duplicado'))
        self.assertEqual(Lote.objects.count(), 1)
        self.assertEqual(Par.objects.count(), 3)

    def test_distinto_client_token_dos_lotes(self):
        self.post_alta(client_token='a')
        self.post_alta(client_token='b')
        self.assertEqual(Lote.objects.count(), 2)

    def test_sin_client_token_sigue_funcionando(self):
        self.assertTrue(self.post_alta().json()['ok'])
        self.assertTrue(self.post_alta().json()['ok'])
        self.assertEqual(Lote.objects.count(), 2)

    def test_carrera_integrity_error_no_es_500(self):
        d1 = self.post_alta(client_token='zzz').json()
        with mock.patch.object(
                Lote.objects, 'filter', side_effect=_sin_ver_el_token({'n': 0})):
            r = self.post_alta(client_token='zzz')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['lote_id'], d1['lote_id'])
        self.assertTrue(r.json()['duplicado'])
        self.assertEqual(Lote.objects.count(), 1)

    def test_token_en_la_plantilla(self):
        r = self.client.get(reverse('inventario:alta_manual'))
        self.assertContains(r, 'client_token')

    def test_referencia_se_trunca_a_100(self):
        d = self.post_alta(referencia='x' * 150).json()
        self.assertEqual(len(Lote.objects.get(pk=d['lote_id']).referencia), 100)


class I3VentaNuevaVsBotTests(EscanerBase):
    def setUp(self):
        super().setUp()
        for p in self.lote.pares.all():
            p.transicionar('en_stock', usuario=self.user, origen='escaner')
        self.p1 = self.lote.pares.first()

    def vender_bot(self):
        """Venta del bot de 3 pares con 2 en stock (queda 1 libre); luego llega un par nuevo."""
        from negocio.services import crear_pedido_tienda_bot
        crear_pedido_tienda_bot(
            items=[{'description': 'jordan 4 aereo negro t26', 'price': 900, 'qty': 3}])
        nuevo = self.crear().pares.first()
        nuevo.transicionar('en_stock', usuario=self.user, origen='escaner')
        self.p1 = nuevo

    def cerrar(self, **extra):
        datos = {'lineas': [{'codigo': self.p1.codigo, 'precio': '900'}],
                 'metodo_pago': 'efectivo', 'tipo_venta': 'menudeo'}
        datos.update(extra)
        return self.post_json('inventario:vender_cerrar', datos)

    def test_sin_venta_del_bot_vende_directo(self):
        r = self.cerrar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Pedido.objects.filter(origen=Pedido.TIENDA).count(), 1)

    def test_con_venta_del_bot_pide_confirmacion(self):
        self.vender_bot()
        antes = Pedido.objects.count()
        r = self.cerrar()
        self.assertEqual(r.status_code, 409)
        d = r.json()
        self.assertFalse(d['ok'])
        self.assertTrue(d['requiere_confirmacion'])
        self.assertIn('El bot ya registró una venta de', d['error'])
        self.assertEqual(Pedido.objects.count(), antes)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')

    def test_confirmada_vende(self):
        self.vender_bot()
        antes = Pedido.objects.count()
        r = self.cerrar(confirmar_venta_nueva=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Pedido.objects.count(), antes + 1)


class I4ColorFueraDelNombreTests(VentaBotBase):
    def test_color_del_modelo_no_cuenta(self):
        base = TipoArticulo.objects.create(
            nombre='Jordan 4 Black Cat', keywords='jordan 4 black cat', costo=Decimal('100'))
        aereo = TipoArticulo.objects.create(
            nombre='Jordan 4 Black Cat A', keywords='jordan 4 black cat aereo',
            costo=Decimal('650'), padre=base, via='aereo', usa_tallas=True, prefijo='BCA')
        rojo = Color.objects.create(nombre='rojo')
        base.colores.add(self.negro, rojo)
        color, _talla, faltas = parsear_venta_tenis('jordan 4 black cat aereo rojo t27', aereo)
        self.assertEqual(color, rojo)
        self.assertEqual(faltas, [])
        color, _talla, faltas = parsear_venta_tenis('jordan 4 black cat aereo t27', aereo)
        self.assertIsNone(color)
        self.assertEqual(len(faltas), 1)
        self.assertIn('Falta el color', faltas[0])

    def test_sigue_funcionando_el_caso_normal(self):
        color, _talla, _faltas = parsear_venta_tenis('jordan 4 aereo black t27', self.aereo)
        self.assertEqual(color, self.negro)


class M10M8Tests(TestCase):
    def test_tipo_con_variantes_no_puede_volverse_variante(self):
        base = TipoArticulo.objects.create(nombre='Jordan 4', keywords='jordan 4', costo=1)
        otro = TipoArticulo.objects.create(nombre='Jordan 1', keywords='jordan 1', costo=1)
        TipoArticulo.objects.create(
            nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=1, padre=base,
            via='aereo', usa_tallas=True, prefijo='JD4A')
        base.padre = otro
        base.via = 'maritimo'
        base.usa_tallas = True
        base.keywords = 'jordan 4 maritimo'
        with self.assertRaises(ValidationError) as cm:
            base.full_clean()
        self.assertIn('padre', cm.exception.message_dict)

    def test_tarifa_negativa_no_valida(self):
        t = TarifaFlete.objects.get(via='aereo')
        f = TarifaFleteForm({'monto_por_par': '-5'}, instance=t)
        self.assertFalse(f.is_valid())
        self.assertIn('monto_por_par', f.errors)
        f = TarifaFleteForm({'monto_por_par': '0'}, instance=t)
        self.assertTrue(f.is_valid())


class M5TipoDeleteTests(TestCase):
    def test_borrar_tipo_con_variantes_no_500(self):
        staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(staff)
        base = TipoArticulo.objects.create(nombre='Jordan 4', keywords='jordan 4', costo=1)
        TipoArticulo.objects.create(
            nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=1, padre=base,
            via='aereo', usa_tallas=True, prefijo='JD4A')
        r = self.client.post(reverse('negocio:tipo_delete', args=[base.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(TipoArticulo.objects.filter(pk=base.pk).exists())
        self.assertContains(r, 'No se puede borrar')
