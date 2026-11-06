from django.urls import reverse

from inventario.models import LecturaRepetida
from inventario.services.escaneo import deshacer, recibir
from inventario.tests.test_lotes import LotesBase


class LecturaRepetidaTests(LotesBase):
    """La segunda lectura de un par ya recibido queda registrada y no toca su estado."""

    def setUp(self):
        super().setUp()
        self.lote = self.crear()
        self.par, self.otro = self.lote.pares.order_by('pk')[:2]

    def test_primera_lectura_no_registra(self):
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertFalse(LecturaRepetida.objects.exists())

    def test_segunda_y_tercera_lectura_registran(self):
        for _ in range(3):
            recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        lecturas = LecturaRepetida.objects.filter(par=self.par)
        self.assertEqual(lecturas.count(), 2)
        self.assertEqual({(l.lote_id, l.usuario_id) for l in lecturas}, {(self.lote.pk, self.user.pk)})
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')

    def test_lectura_rechazada_no_registra(self):
        recibir('ZZZZ-0000', lote=self.lote, usuario=self.user)
        recibir(self.par.codigo, lote=self.crear(), usuario=self.user)  # otro lote: rojo
        self.assertFalse(LecturaRepetida.objects.exists())

    def test_repetida_no_bloquea_deshacer_la_recepcion(self):
        mov = recibir(self.par.codigo, lote=self.lote, usuario=self.user).movimiento_id
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual(deshacer(mov, usuario=self.user, nota='error').nivel, 'verde')


class PantallaRepetidosTests(LotesBase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.lote = self.crear()
        self.lote2 = self.crear()
        self.par, self.limpio = self.lote.pares.order_by('pk')[:2]
        self.par2 = self.lote2.pares.first()
        for par, lote in ((self.par, self.lote), (self.limpio, self.lote), (self.par2, self.lote2)):
            recibir(par.codigo, lote=lote, usuario=self.user)
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        recibir(self.par.codigo, lote=self.lote, usuario=self.user)
        recibir(self.par2.codigo, lote=self.lote2, usuario=self.user)
        self.url = reverse('inventario:repetidos')

    def test_lista_solo_los_repetidos_con_sus_veces(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        filas = {f['par'].codigo: f['veces'] for f in r.context['filas']}
        self.assertEqual(filas, {self.par.codigo: 3, self.par2.codigo: 2})
        self.assertNotContains(r, self.limpio.codigo)
        self.assertContains(r, f'href="{reverse("inventario:par_ficha", args=[self.par.codigo])}"')

    def test_filtro_por_lote(self):
        r = self.client.get(self.url, {'lote': self.lote2.pk})
        self.assertEqual([f['par'].codigo for f in r.context['filas']], [self.par2.codigo])
        self.assertEqual(r.context['filas'][0]['recepcion'].a_estado, 'en_stock')

    def test_lote_invalido_muestra_todos(self):
        r = self.client.get(self.url, {'lote': 'x'})
        self.assertEqual(len(r.context['filas']), 2)

    def test_vacio(self):
        LecturaRepetida.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'Ningún par se ha escaneado dos veces')

    def test_detalle_del_lote_enlaza_con_su_conteo(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, f'href="{self.url}?lote={self.lote.pk}"')
        self.assertContains(r, '1 par escaneado 2 veces')

    def test_detalle_sin_repetidos_no_muestra_el_enlace(self):
        lote3 = self.crear()
        r = self.client.get(reverse('inventario:lote_detalle', args=[lote3.pk]))
        self.assertNotContains(r, f'{self.url}?lote=')

    def test_solo_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
