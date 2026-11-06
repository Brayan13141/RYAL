from decimal import Decimal

from django.urls import reverse

from inventario.models import MovimientoPar, Par, TallaCalzado
from inventario.services.escaneo import deshacer, recibir
from inventario.services.tallas import corregir_talla
from inventario.tests.test_lotes import LotesBase


class CorregirTallaServicioTests(LotesBase):
    def setUp(self):
        super().setUp()
        self.lote = self.crear(lineas=[self.linea(cantidad=3)])
        self.p1, self.p2, self.p3 = self.lote.pares.order_by('pk')

    def test_cambia_talla_y_deja_historial(self):
        recibir(self.p2.codigo, lote=self.lote, usuario=self.user)
        cambiados = corregir_talla([self.p1, self.p2], self.t27, usuario=self.user, nota='caja dice 27')
        self.assertEqual({p.pk for p in cambiados}, {self.p1.pk, self.p2.pk})
        for p in (self.p1, self.p2):
            p.refresh_from_db()
            self.assertEqual(p.talla, self.t27)
            mov = p.movimientos.order_by('-pk').first()
            self.assertEqual((mov.de_estado, mov.a_estado, mov.origen), (p.estado, p.estado, 'manual'))
            self.assertEqual(mov.nota, 'Talla corregida: 26 → 27. caja dice 27')
        self.p3.refresh_from_db()
        self.assertEqual(self.p3.talla, self.t26)

    def test_vendido_o_baja_rechaza_todo(self):
        Par.objects.filter(pk=self.p2.pk).update(estado='vendido')
        with self.assertRaises(ValueError):
            corregir_talla([self.p1, self.p2], self.t27, usuario=self.user)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.talla, self.t26)

    def test_misma_talla_no_hace_nada(self):
        self.assertEqual(corregir_talla([self.p1], self.t26, usuario=self.user), [])
        self.assertFalse(MovimientoPar.objects.filter(origen='manual').exists())

    def test_talla_inactiva_rechaza(self):
        TallaCalzado.objects.filter(pk=self.t27.pk).update(activa=False)
        self.t27.refresh_from_db()
        with self.assertRaises(ValueError):
            corregir_talla([self.p1], self.t27, usuario=self.user)

    def test_el_estado_al_cierre_de_un_conteo_no_cambia(self):
        """El movimiento de corrección no cambia de estado: deshacer la recepción sigue
        bloqueado solo porque ya no es la última lectura, como cualquier otro movimiento."""
        mov = recibir(self.p1.codigo, lote=self.lote, usuario=self.user).movimiento_id
        corregir_talla([self.p1], self.t27, usuario=self.user)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')
        self.assertEqual(deshacer(mov, usuario=self.user, nota='x').nivel, 'rojo')


class CorregirTallaVistaTests(LotesBase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.lote = self.crear(lineas=[self.linea(cantidad=3)])
        self.p1, self.p2, self.p3 = self.lote.pares.order_by('pk')
        self.url = reverse('inventario:lote_tallas',
                           args=[self.lote.pk, self.var_a.pk, self.color.pk, self.t26.pk])

    def test_get_lista_los_pares_del_renglon(self):
        r = self.client.get(self.url, {'par': self.p2.pk})
        self.assertEqual(r.status_code, 200)
        for p in (self.p1, self.p2, self.p3):
            self.assertContains(r, p.codigo)
        self.assertContains(r, f'value="{self.p2.pk}" data-estado="por_recibir" class="form-check-input m-0 js-par" checked')
        self.assertNotContains(r, f'<option value="{self.t26.pk}">')

    def test_renglon_inexistente_404(self):
        url = reverse('inventario:lote_tallas', args=[self.lote.pk, self.var_a.pk, self.color.pk, self.t27.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_post_corrige_y_redirige_con_enlace_de_etiquetas(self):
        r = self.client.post(self.url, {'par': [self.p1.pk, self.p3.pk], 'talla': self.t27.pk}, follow=True)
        self.assertRedirects(r, reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertEqual(set(self.lote.pares.filter(talla=self.t27).values_list('pk', flat=True)),
                         {self.p1.pk, self.p3.pk})
        etiquetas = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        self.assertContains(r, f'href="{etiquetas}?codigos={self.p1.codigo},{self.p3.codigo}"')
        self.assertContains(r, '2 pares pasados de la talla 26 a la 27')

    def test_post_ignora_pares_de_otro_renglon_o_lote(self):
        otro = self.crear().pares.first()
        r = self.client.post(self.url, {'par': [otro.pk], 'talla': self.t27.pk})
        self.assertEqual(r.status_code, 200)
        otro.refresh_from_db()
        self.assertEqual(otro.talla, self.t26)

    def test_post_sin_talla_no_cambia(self):
        self.client.post(self.url, {'par': [self.p1.pk], 'talla': ''})
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.talla, self.t26)

    def test_vendido_no_tiene_check(self):
        Par.objects.filter(pk=self.p1.pk).update(estado='vendido')
        r = self.client.get(self.url)
        self.assertNotContains(r, f'name="par" value="{self.p1.pk}"')

    def test_lote_y_ficha_enlazan_la_correccion(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, f'href="{self.url}"')
        r = self.client.get(reverse('inventario:par_ficha', args=[self.p1.codigo]))
        self.assertContains(r, f'href="{self.url}?par={self.p1.pk}"')

    def test_etiquetas_por_codigos(self):
        url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        r = self.client.get(url, {'codigos': f'{self.p1.codigo},{self.p3.codigo.lower()}'})
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.p1.codigo.encode(), r.content)
        self.assertIn(self.p3.codigo.encode(), r.content)
        self.assertNotIn(self.p2.codigo.encode(), r.content)

    def test_detalle_trae_seleccionar_no_escaneados(self):
        recibir(self.p1.codigo, lote=self.lote, usuario=self.user)
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, 'id="sel-pend"')
        self.assertContains(r, 'data-pend="2"')
