from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.services import cantidad as svc
from negocio.models import Pedido, PedidoItem


def _tipo(nombre='Gorra Barbas'):
    t = TipoArticulo(nombre=nombre, keywords=nombre.lower(), costo=Decimal('240'))
    t.full_clean()
    t.save()
    return t


class CantidadBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.tipo = _tipo()

    def activo(self, n=100):
        svc.activar(self.tipo, n, usuario=self.staff)
        return self.tipo

    def mensajes(self, r):
        return [str(m) for m in get_messages(r.wsgi_request)]

    def niveles(self, r):
        return [m.level_tag for m in get_messages(r.wsgi_request)]


class CantidadAccesoTests(CantidadBase):
    def test_anonimo_redirige_al_login(self):
        self.activo()
        self.client.logout()
        for nombre, kw, metodo in (
                ('inventario:cantidad_activar', {}, 'post'),
                ('inventario:cantidad_ajustar', {'pk': self.tipo.pk}, 'post'),
                ('inventario:cantidad_historial', {'pk': self.tipo.pk}, 'get'),
                ('inventario:cantidad_etiqueta', {'pk': self.tipo.pk}, 'get')):
            with self.subTest(nombre=nombre):
                r = getattr(self.client, metodo)(reverse(nombre, kwargs=kw))
                self.assertEqual(r.status_code, 302)
                self.assertIn('login', r['Location'])


class CantidadAccionesTests(CantidadBase):
    def test_activar_desde_pantalla(self):
        r = self.client.post(reverse('inventario:cantidad_activar'),
                             {'tipo_id': self.tipo.pk, 'cantidad': '999'})
        self.assertRedirects(r, reverse('inventario:stock'))
        self.tipo.refresh_from_db()
        self.assertTrue(self.tipo.stock_por_cantidad)
        self.assertEqual(svc.stock_actual(self.tipo), 999)

    def test_activar_invalido_da_error(self):
        r = self.client.post(reverse('inventario:cantidad_activar'),
                             {'tipo_id': self.tipo.pk, 'cantidad': 'abc'})
        self.assertRedirects(r, reverse('inventario:stock'))
        self.tipo.refresh_from_db()
        self.assertFalse(self.tipo.stock_por_cantidad)
        self.assertEqual(self.niveles(r), ['error'])

    def test_ajustar_con_motivo(self):
        self.activo()
        r = self.client.post(reverse('inventario:cantidad_ajustar', args=[self.tipo.pk]),
                             {'cantidad': '40', 'motivo': 'conté el exhibidor'})
        self.assertRedirects(r, reverse('inventario:stock'))
        self.assertEqual(svc.stock_actual(self.tipo), 40)
        self.assertEqual(self.niveles(r), ['success'])

    def test_ajustar_sin_motivo_no_cambia(self):
        self.activo()
        r = self.client.post(reverse('inventario:cantidad_ajustar', args=[self.tipo.pk]),
                             {'cantidad': '40', 'motivo': ''})
        self.assertEqual(svc.stock_actual(self.tipo), 100)
        self.assertEqual(self.mensajes(r), ['El motivo es obligatorio.'])

    def test_ajustar_cantidad_invalida(self):
        self.activo()
        r = self.client.post(reverse('inventario:cantidad_ajustar', args=[self.tipo.pk]),
                             {'cantidad': 'x', 'motivo': 'm'})
        self.assertEqual(svc.stock_actual(self.tipo), 100)
        self.assertEqual(self.niveles(r), ['error'])

    def test_get_no_permitido(self):
        self.activo()
        r = self.client.get(reverse('inventario:cantidad_ajustar', args=[self.tipo.pk]))
        self.assertEqual(r.status_code, 405)


class CantidadPantallasTests(CantidadBase):
    def test_historial_lista_ajustes_y_ventas(self):
        self.activo(100)
        svc.ajustar(self.tipo, 80, motivo='conté el exhibidor', usuario=self.staff)
        pedido = Pedido.objects.create(
            costo_producto=Decimal('240'), precio_venta=Decimal('250'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA)
        item = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot=svc.codigo_etiqueta(self.tipo),
            nombre_snapshot=self.tipo.nombre, cantidad=2,
            costo_unitario=Decimal('240'), precio_unitario=Decimal('250'))
        svc.registrar_salida(item, self.tipo)
        r = self.client.get(reverse('inventario:cantidad_historial', args=[self.tipo.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'conté el exhibidor')
        self.assertContains(r, '100 → 80')
        self.assertContains(r, f'#{pedido.pk}')

    def test_historial_de_tipo_no_activo_404(self):
        r = self.client.get(reverse('inventario:cantidad_historial', args=[self.tipo.pk]))
        self.assertEqual(r.status_code, 404)

    def test_stock_muestra_seccion(self):
        self.activo(77)
        otro = _tipo('Gorra Libre')
        r = self.client.get(reverse('inventario:stock'))
        self.assertContains(r, 'Gorra Barbas')
        self.assertContains(r, '77')
        self.assertEqual([c['tipo'] for c in r.context['cantidades']], [self.tipo])
        self.assertEqual(r.context['cantidades'][0]['stock'], 77)
        self.assertEqual(list(r.context['activables']), [otro])

    def test_activables_excluye_con_variantes(self):
        base = _tipo('Base')
        var = TipoArticulo(nombre='Base A', keywords='base aereo', costo=Decimal('1'),
                           padre=base, via='aereo', usa_tallas=True)
        var.full_clean()
        var.save()
        r = self.client.get(reverse('inventario:stock'))
        activables = list(r.context['activables'])
        self.assertIn(self.tipo, activables)
        self.assertNotIn(base, activables)
        self.assertNotIn(var, activables)

    def test_etiqueta_pdf(self):
        self.activo()
        url = reverse('inventario:cantidad_etiqueta', args=[self.tipo.pk])
        r = self.client.get(url, {'copias': 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn(f'TIPO.{self.tipo.pk}'.encode(), r.content)
        self.assertEqual(r.content.count(b'/Type /Page\n'), 3)
        for malo in ('0', 'abc', '101'):
            with self.subTest(copias=malo):
                self.assertEqual(self.client.get(url, {'copias': malo}).status_code, 400)

    def test_etiqueta_copias_por_defecto_una(self):
        self.activo()
        r = self.client.get(reverse('inventario:cantidad_etiqueta', args=[self.tipo.pk]))
        self.assertEqual(r.content.count(b'/Type /Page\n'), 1)

    def test_etiqueta_tipo_no_activo_404(self):
        r = self.client.get(reverse('inventario:cantidad_etiqueta', args=[self.tipo.pk]))
        self.assertEqual(r.status_code, 404)


class CantidadRondaFinalTests(CantidadBase):
    def test_activar_tipo_invalido_pide_elegir_tipo(self):
        for tipo_id in ('', 'abc', '999999'):
            with self.subTest(tipo_id=tipo_id):
                r = self.client.post(reverse('inventario:cantidad_activar'),
                                     {'tipo_id': tipo_id, 'cantidad': '5'}, follow=True)
                self.assertEqual(self.mensajes(r), ['Elige un tipo de artículo.'])

    def test_stock_select_arranca_sin_tipo_y_pide_confirmar(self):
        r = self.client.get(reverse('inventario:stock'))
        self.assertContains(r, '<option value="" selected disabled>Elige un tipo…</option>',
                            html=False)
        self.assertContains(r, 'Activar stock por cantidad no se puede deshacer desde la '
                               'pantalla. ¿Continuar?')

    def test_historial_marca_venta_cancelada(self):
        self.activo(100)
        pedido = Pedido.objects.create(
            costo_producto=Decimal('240'), precio_venta=Decimal('250'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA)
        item = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot=svc.codigo_etiqueta(self.tipo),
            nombre_snapshot=self.tipo.nombre, cantidad=1,
            costo_unitario=Decimal('240'), precio_unitario=Decimal('250'))
        svc.registrar_salida(item, self.tipo)
        url = reverse('inventario:cantidad_historial', args=[self.tipo.pk])
        self.assertNotContains(self.client.get(url), 'cancelada')
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.CANCELADO)
        r = self.client.get(url)
        self.assertContains(r, 'cancelada')
        filas = [f.split('</tr>')[0] for f in r.content.decode().split('<tr')
                 if f'Pedido #{pedido.pk}<' in f.split('</tr>')[0]]
        self.assertEqual(len(filas), 1)
        self.assertIn('cancelada', filas[0])

    def test_borrar_tipo_con_movimientos_explica(self):
        self.activo()
        r = self.client.post(reverse('negocio:tipo_delete', args=[self.tipo.pk]))
        self.assertTrue(TipoArticulo.objects.filter(pk=self.tipo.pk).exists())
        self.assertEqual(self.mensajes(r),
                         ['No se puede borrar: tiene movimientos de stock por cantidad.'])

    def test_borrar_tipo_con_variantes_mensaje_actual(self):
        TipoArticulo.objects.create(nombre='Gorra Barbas A', keywords='gorra barbas aereo',
                                    costo=Decimal('1'), padre=self.tipo, via='aereo',
                                    usa_tallas=True)
        r = self.client.post(reverse('negocio:tipo_delete', args=[self.tipo.pk]))
        self.assertEqual(self.mensajes(r), ['No se puede borrar: tiene variantes o pares.'])
