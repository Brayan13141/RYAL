from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from inventario.models import Conteo
from inventario.services import escaneo, ventas
from inventario.services.conteos import corregir, resumen_conteo
from inventario.tests.test_vistas_escaner import EscanerBase


class ConteoBase(EscanerBase):
    def setUp(self):
        super().setUp()
        self.p1, self.p2 = list(self.lote.pares.order_by('pk'))
        for p in (self.p1, self.p2):
            p.transicionar('en_stock', usuario=self.staff, origen='escaner')
        self.conteo = Conteo.objects.create(creado_por=self.staff)

    def par_extra(self):
        """Un par más (otra talla), por_recibir, de otro lote."""
        return self.crear(lineas=[self.linea(talla=self.t27, cantidad=1)]).pares.get()


class ContarServicioTests(ConteoBase):
    def test_verde_no_cambia_estado_y_registra_lectura(self):
        antes = self.p1.movimientos.count()
        r = escaneo.contar(self.p1.codigo.lower(), conteo=self.conteo, usuario=self.staff)
        self.assertEqual(r.nivel, 'verde')
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')
        self.assertEqual(self.p1.movimientos.count(), antes)
        self.assertEqual(self.conteo.lecturas.filter(par=self.p1).count(), 1)

    def test_repetido_es_amarillo_sin_segunda_lectura(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        r = escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        self.assertEqual(r.nivel, 'amarillo')
        self.assertEqual(self.conteo.lecturas.count(), 1)

    def test_otro_estado_es_amarillo_con_estado(self):
        self.p2.transicionar('baja', usuario=self.staff, origen='manual',
                             motivo='danado', nota='roto')
        r = escaneo.contar(self.p2.codigo, conteo=self.conteo, usuario=self.staff)
        self.assertEqual(r.nivel, 'amarillo')
        self.assertTrue(r.mensaje.startswith('No debería estar aquí'))
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.estado, 'baja')
        self.assertEqual(self.conteo.lecturas.filter(par=self.p2).count(), 1)

    def test_desconocido_es_rojo_y_se_registra_sin_par(self):
        r = escaneo.contar('NOEXISTE-1', conteo=self.conteo, usuario=self.staff)
        self.assertEqual(r.nivel, 'rojo')
        lec = self.conteo.lecturas.get()
        self.assertIsNone(lec.par)
        self.assertEqual(lec.codigo_leido, 'NOEXISTE-1')
        self.assertEqual(
            escaneo.contar('noexiste-1', conteo=self.conteo, usuario=self.staff).nivel,
            'amarillo')
        self.assertEqual(self.conteo.lecturas.count(), 1)

    def test_codigo_demasiado_largo_es_rojo_sin_registrar(self):
        r = escaneo.contar('https://ejemplo.com/' + 'x' * 100, conteo=self.conteo,
                           usuario=self.staff)
        self.assertEqual(r.nivel, 'rojo')
        self.assertEqual(self.conteo.lecturas.count(), 0)

    def test_conteo_cerrado_es_rojo(self):
        self.conteo.cerrado_at = timezone.now()
        self.conteo.save()
        r = escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        self.assertEqual(r.nivel, 'rojo')
        self.assertEqual(self.conteo.lecturas.count(), 0)


class ResumenTests(ConteoBase):
    def test_no_leidos_fuera_de_stock_y_combinacion(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        r = resumen_conteo(self.conteo)
        self.assertEqual([p.pk for p in r['no_leidos']], [self.p2.pk])
        self.assertEqual(r['fuera_de_stock'], [])
        self.assertEqual(len(r['por_combinacion']), 1)
        c = r['por_combinacion'][0]
        self.assertEqual((c['esperados'], c['leidos']), (2, 1))
        self.assertEqual(c['via'], 'aereo')
        self.assertEqual(c['base'], self.base)

    def test_fuera_de_stock_y_desconocidos(self):
        self.p2.transicionar('baja', usuario=self.staff, origen='manual',
                             motivo='perdido', nota='x')
        escaneo.contar(self.p2.codigo, conteo=self.conteo, usuario=self.staff)
        escaneo.contar('ZZZ-9', conteo=self.conteo, usuario=self.staff)
        r = resumen_conteo(self.conteo)
        self.assertEqual([p.pk for p in r['fuera_de_stock']], [self.p2.pk])
        self.assertEqual(r['desconocidos'], ['ZZZ-9'])
        self.assertEqual(r['por_combinacion'][0]['esperados'], 1)

    def test_esperados_se_congelan_al_cerrar(self):
        self.conteo.cerrado_at = timezone.now()
        self.conteo.save()
        # Un par recibido después del cierre no cuenta como esperado.
        otro = self.par_extra()
        otro.transicionar('en_stock', usuario=self.staff, origen='escaner')
        r = resumen_conteo(self.conteo)
        self.assertEqual({p.pk for p in r['no_leidos']}, {self.p1.pk, self.p2.pk})
        self.assertEqual(r['por_combinacion'][0]['esperados'], 2)
        self.assertEqual(len(r['por_combinacion']), 1)


class CorregirTests(ConteoBase):
    def test_exige_nota(self):
        res = corregir(self.conteo, self.p2.codigo, 'perdido', '  ', usuario=self.staff)
        self.assertEqual(res.nivel, 'rojo')
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.estado, 'en_stock')

    def test_marcar_perdido(self):
        res = corregir(self.conteo, self.p2.codigo, 'perdido', 'no está', usuario=self.staff)
        self.assertEqual(res.nivel, 'verde')
        self.p2.refresh_from_db()
        self.assertEqual((self.p2.estado, self.p2.motivo_baja), ('baja', 'perdido'))

    def test_perdido_no_aplica_a_uno_leido(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        res = corregir(self.conteo, self.p1.codigo, 'perdido', 'n', usuario=self.staff)
        self.assertEqual(res.nivel, 'rojo')
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')

    def test_recibir_leido_por_recibir(self):
        otro = self.par_extra()
        escaneo.contar(otro.codigo, conteo=self.conteo, usuario=self.staff)
        res = corregir(self.conteo, otro.codigo, 'recibir', 'llegó', usuario=self.staff)
        self.assertEqual(res.nivel, 'verde')
        otro.refresh_from_db()
        self.assertEqual(otro.estado, 'en_stock')
        self.assertEqual(otro.movimientos.last().origen, 'manual')

    def test_aparecio_faltante(self):
        otro = self.par_extra()
        otro.transicionar('baja', usuario=self.staff, origen='cierre_lote', motivo='faltante')
        escaneo.contar(otro.codigo, conteo=self.conteo, usuario=self.staff)
        res = corregir(self.conteo, otro.codigo, 'aparecio', 'estaba', usuario=self.staff)
        self.assertEqual(res.nivel, 'verde')
        otro.refresh_from_db()
        self.assertEqual(otro.estado, 'en_stock')

    def test_vendido_no_se_corrige_directo(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        ventas.vender_pares(
            [(self.p1, Decimal('900'))], metodo_pago='efectivo', usuario=self.staff)
        for accion in ('perdido', 'recibir', 'aparecio'):
            res = corregir(self.conteo, self.p1.codigo, accion, 'n', usuario=self.staff)
            self.assertEqual(res.nivel, 'rojo')
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'vendido')

    def test_casos_invalidos_no_dan_500(self):
        res = corregir(self.conteo, self.p1.codigo, 'aparecio', 'n', usuario=self.staff)
        self.assertEqual(res.nivel, 'rojo')
        res = corregir(self.conteo, 'NOPE', 'perdido', 'n', usuario=self.staff)
        self.assertEqual(res.nivel, 'rojo')
        res = corregir(self.conteo, self.p1.codigo, 'xx', 'n', usuario=self.staff)
        self.assertEqual(res.nivel, 'rojo')


class ConteoVistasTests(ConteoBase):
    def test_crear_redirige_y_reusa_abierto(self):
        Conteo.objects.all().delete()
        r = self.client.post(reverse('inventario:conteo_crear'))
        c = Conteo.objects.get()
        self.assertRedirects(r, reverse('inventario:escanear') + f'?conteo={c.pk}',
                             fetch_redirect_response=False)
        self.client.post(reverse('inventario:conteo_crear'))
        self.assertEqual(Conteo.objects.count(), 1)

    def test_contar_endpoint(self):
        d = self.post_json('inventario:escanear_contar', {
            'lectura': self.p1.codigo, 'conteo_id': self.conteo.pk}).json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(d['codigo'], self.p1.codigo)

    def test_contar_conteo_invalido_400(self):
        r = self.post_json('inventario:escanear_contar', {'lectura': 'x', 'conteo_id': 9999})
        self.assertEqual(r.status_code, 400)

    def test_cerrar_y_resumen(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        r = self.client.post(reverse('inventario:conteo_cerrar', args=[self.conteo.pk]))
        self.assertEqual(r.status_code, 302)
        self.conteo.refresh_from_db()
        self.assertIsNotNone(self.conteo.cerrado_at)
        r = self.client.get(reverse('inventario:conteo_resumen', args=[self.conteo.pk]))
        self.assertContains(r, self.p2.codigo)
        self.assertContains(r, 'Marcar perdido')

    def test_resumen_vendido_enlaza_pedido_sin_boton_directo(self):
        escaneo.contar(self.p1.codigo, conteo=self.conteo, usuario=self.staff)
        pedido = ventas.vender_pares(
            [(self.p1, Decimal('900'))], metodo_pago='efectivo', usuario=self.staff)
        r = self.client.get(reverse('inventario:conteo_resumen', args=[self.conteo.pk]))
        self.assertContains(r, reverse('negocio:pedido_detail', args=[pedido.pk]))

    def test_corregir_endpoint_exige_nota_y_aplica(self):
        url = reverse('inventario:conteo_corregir', args=[self.conteo.pk])
        r = self.client.post(url, {'codigo': self.p2.codigo, 'accion': 'perdido', 'nota': ''})
        self.assertEqual(r.status_code, 302)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.estado, 'en_stock')
        self.client.post(url, {'codigo': self.p2.codigo, 'accion': 'perdido', 'nota': 'n'})
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.estado, 'baja')

    def test_lista_conteos_y_escaner_con_tab(self):
        r = self.client.get(reverse('inventario:conteos'))
        self.assertContains(r, reverse('inventario:conteo_resumen', args=[self.conteo.pk]))
        r = self.client.get(reverse('inventario:escanear'), {'conteo': self.conteo.pk})
        self.assertEqual(r.context['conteo_sel'], self.conteo)
