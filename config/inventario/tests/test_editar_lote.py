import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import (
    Color, MovimientoPar, Par, TallaCalzado, TransicionInvalida)
from inventario.services.editar_lote import editar_lote, filas_editables, version_lote
from inventario.services.escaneo import recibir
from inventario.services.lotes import cerrar_lote, crear_lote
from inventario.services.tipos import crear_variante


class EditarBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'staff', password='x', is_staff=True)
        self.base = TipoArticulo(nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.base.full_clean()
        self.base.save()
        self.negro = Color.objects.create(nombre='negro')
        self.blanco = Color.objects.create(nombre='blanco')
        self.base.colores.add(self.negro, self.blanco)
        self.var_a = crear_variante(self.base, 'aereo', costo=Decimal('640'))
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.t27 = TallaCalzado.objects.get(mx_cm=Decimal('27'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.user, referencia='L1',
            flete_por_par=Decimal('50'),
            lineas=[{'tipo': self.var_a, 'color': self.negro, 'talla': self.t26,
                     'cantidad': 3, 'precio_proveedor': Decimal('360')}])

    def clave(self, color=None, talla=None):
        return (self.var_a.pk, (color or self.negro).pk, (talla or self.t26).pk)

    def editar(self, cantidades=None, lineas=None, version=None):
        return editar_lote(self.lote, cantidades=cantidades or {}, lineas=lineas or [],
                           usuario=self.user, version=version)

    def activos(self):
        return self.lote.pares.exclude(estado=Par.Estado.ANULADO).count()


class SubirCantidadTests(EditarBase):
    def test_subir_crea_pares_por_recibir_con_el_costo_del_renglon(self):
        res = self.editar({self.clave(): 5})
        self.assertEqual(len(res.creados), 2)
        self.assertEqual(self.activos(), 5)
        for p in res.creados:
            p.refresh_from_db()
            self.assertEqual(p.estado, Par.Estado.POR_RECIBIR)
            self.assertEqual(p.precio_proveedor, Decimal('360.00'))
            self.assertEqual(p.flete, Decimal('50.00'))
            self.assertEqual(p.costo, Decimal('410.00'))
            self.assertEqual(p.movimientos.get().origen, 'alta')

    def test_misma_cantidad_no_cambia_nada(self):
        res = self.editar({self.clave(): 3})
        self.assertEqual((res.creados, res.anulados), ([], []))

    def test_lote_inicial_usa_el_costo_del_tipo(self):
        lote = crear_lote(
            via='aereo', origen='inicial', usuario=self.user,
            lineas=[{'tipo': self.var_a, 'color': self.negro, 'talla': self.t26,
                     'cantidad': 1, 'precio_proveedor': None}])
        res = editar_lote(lote, cantidades={self.clave(): 2}, lineas=[],
                          usuario=self.user, version=None)
        p = res.creados[0]
        self.assertIsNone(p.precio_proveedor)
        self.assertIsNone(p.flete)
        self.assertEqual(p.costo, Decimal('640.00'))


class BajarCantidadTests(EditarBase):
    def test_bajar_anula_las_etiquetas_mas_recientes(self):
        pks = list(self.lote.pares.order_by('pk').values_list('pk', flat=True))
        res = self.editar({self.clave(): 1})
        self.assertEqual(sorted(p.pk for p in res.anulados), pks[1:])
        self.assertEqual(self.activos(), 1)
        p = Par.objects.get(pk=pks[2])
        self.assertEqual(p.estado, Par.Estado.ANULADO)
        mov = p.movimientos.order_by('-pk').first()
        self.assertEqual((mov.de_estado, mov.a_estado, mov.origen),
                         ('por_recibir', 'anulado', 'edicion'))
        self.assertEqual(mov.usuario, self.user)

    def test_bajar_a_cero_quita_el_renglon(self):
        self.editar({self.clave(): 0})
        self.assertEqual(self.activos(), 0)
        self.assertEqual(filas_editables(self.lote), [])

    def test_no_anula_pares_ya_escaneados(self):
        primero = self.lote.pares.order_by('pk').first()
        ultimo = self.lote.pares.order_by('pk').last()
        recibir(ultimo.codigo, lote=self.lote, usuario=self.user)
        res = self.editar({self.clave(): 1})
        self.assertEqual({p.pk for p in res.anulados},
                         set(self.lote.pares.exclude(pk__in=[ultimo.pk]).values_list('pk', flat=True)))
        ultimo.refresh_from_db()
        self.assertEqual(ultimo.estado, Par.Estado.EN_STOCK)
        self.assertIn(primero.pk, {p.pk for p in res.anulados})

    def test_no_baja_por_debajo_de_los_escaneados(self):
        for p in self.lote.pares.order_by('pk')[:2]:
            recibir(p.codigo, lote=self.lote, usuario=self.user)
        with self.assertRaisesMessage(ValueError, 'ya hay 2 escaneados'):
            self.editar({self.clave(): 1})
        self.assertEqual(self.activos(), 3)

    def test_anulados_no_cuentan_al_cerrar_el_lote(self):
        self.editar({self.clave(): 1})
        self.assertEqual(cerrar_lote(self.lote, usuario=self.user), 1)
        self.assertEqual(self.lote.pares.filter(estado=Par.Estado.ANULADO).count(), 2)


class LineasNuevasTests(EditarBase):
    def linea(self, color=None, talla=None, cantidad=2, precio='300'):
        return {'tipo': self.var_a, 'color': color or self.blanco, 'talla': talla or self.t27,
                'cantidad': cantidad, 'precio_proveedor': None if precio is None else Decimal(precio)}

    def test_agrega_una_combinacion_nueva(self):
        res = self.editar(lineas=[self.linea()])
        self.assertEqual(len(res.creados), 2)
        p = res.creados[0]
        self.assertEqual((p.color, p.talla, p.lote), (self.blanco, self.t27, self.lote))
        self.assertEqual(p.costo, Decimal('350.00'))

    def test_linea_sobre_un_renglon_existente_se_suma(self):
        res = self.editar(lineas=[self.linea(color=self.negro, talla=self.t26, precio='360')])
        self.assertEqual(len(res.creados), 2)
        self.assertEqual(self.activos(), 5)

    def test_linea_sin_precio_en_lote_manual_falla(self):
        with self.assertRaisesMessage(ValueError, 'falta el precio'):
            self.editar(lineas=[self.linea(precio=None)])

    def test_todo_o_nada(self):
        with self.assertRaises(ValueError):
            self.editar({self.clave(): 1}, lineas=[self.linea(precio=None)])
        self.assertEqual(self.activos(), 3)
        self.assertFalse(self.lote.pares.filter(estado=Par.Estado.ANULADO).exists())


class ValidacionesTests(EditarBase):
    def test_lote_cerrado_no_se_edita(self):
        cerrar_lote(self.lote, usuario=self.user)
        with self.assertRaisesMessage(ValueError, 'cerrado'):
            self.editar({self.clave(): 5})

    def test_renglon_que_no_es_del_lote(self):
        with self.assertRaisesMessage(ValueError, 'no está en el lote'):
            self.editar({self.clave(color=self.blanco): 2})

    def test_cantidad_invalida(self):
        for mala in (-1, 1.5, True, '3', 501):
            with self.subTest(mala=mala), self.assertRaises(ValueError):
                self.editar({self.clave(): mala})

    def test_version_vieja_rechaza(self):
        v = version_lote(self.lote)
        recibir(self.lote.pares.first().codigo, lote=self.lote, usuario=self.user)
        with self.assertRaisesMessage(ValueError, 'cambió'):
            self.editar({self.clave(): 5}, version=v)
        self.assertEqual(self.activos(), 3)

    def test_escaneo_entre_lectura_y_candado_da_error_claro(self):
        from unittest import mock
        original = Par.transicionar

        def escanea_antes(par, *a, **kw):
            Par.objects.filter(pk=par.pk).update(estado=Par.Estado.EN_STOCK)
            return original(par, *a, **kw)
        with mock.patch.object(Par, 'transicionar', escanea_antes),                 self.assertRaisesMessage(ValueError, 'cambió'):
            self.editar({self.clave(): 2})

    def test_version_vigente_acepta_y_luego_cambia(self):
        v = version_lote(self.lote)
        self.editar({self.clave(): 2}, version=v)
        self.assertNotEqual(version_lote(self.lote), v)

    def test_filas_editables_marcan_el_minimo(self):
        recibir(self.lote.pares.first().codigo, lote=self.lote, usuario=self.user)
        [fila] = filas_editables(self.lote)
        self.assertEqual((fila['activos'], fila['minimo']), (3, 1))


class AnuladoTests(EditarBase):
    def setUp(self):
        super().setUp()
        self.editar({self.clave(): 2})
        self.anulado = self.lote.pares.get(estado=Par.Estado.ANULADO)

    def test_escanear_una_etiqueta_anulada_avisa(self):
        res = recibir(self.anulado.codigo, lote=self.lote, usuario=self.user)
        self.assertEqual(res.nivel, 'rojo')
        self.assertIn('anulada', res.mensaje)
        self.anulado.refresh_from_db()
        self.assertEqual(self.anulado.estado, Par.Estado.ANULADO)

    def test_anulado_no_vuelve_a_ningun_estado(self):
        for destino in ('por_recibir', 'en_stock', 'baja', 'vendido'):
            with self.subTest(destino=destino), self.assertRaises(TransicionInvalida):
                self.anulado.transicionar(destino, usuario=self.user, origen='manual',
                                          motivo='faltante', nota='x')

    def test_solo_la_edicion_puede_anular(self):
        par = self.lote.pares.filter(estado=Par.Estado.POR_RECIBIR).first()
        with self.assertRaises(TransicionInvalida):
            par.transicionar('anulado', usuario=self.user, origen='manual')


class VistasEditarTests(EditarBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('inventario:lote_editar', args=[self.lote.pk])

    def post(self, **datos):
        datos.setdefault('version', version_lote(self.lote))
        return self.client.post(self.url, json.dumps(datos), content_type='application/json')

    def campo(self, color=None, talla=None):
        return '{}_{}_{}'.format(*self.clave(color, talla))

    def test_get_muestra_la_cuadricula(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['grupos']), 1)
        self.assertContains(r, f'data-clave="{self.campo()}"')

    def test_requiere_staff(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_lote_cerrado_redirige_al_detalle(self):
        cerrar_lote(self.lote, usuario=self.user)
        r = self.client.get(self.url)
        self.assertRedirects(r, reverse('inventario:lote_detalle', args=[self.lote.pk]))

    def test_post_cambia_cantidades_y_agrega_lineas(self):
        r = self.post(cantidades={self.campo(): 1}, lineas=[{
            'tipo_id': self.base.pk, 'color_id': self.blanco.pk,
            'cantidades': {'27': 2}, 'precio_proveedor': '300'}])
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['url'], reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertEqual(self.activos(), 3)
        self.assertEqual(self.lote.pares.filter(estado=Par.Estado.ANULADO).count(), 2)
        self.assertEqual(self.lote.pares.filter(color=self.blanco).count(), 2)
        detalle = self.client.get(d['url'])
        texto = ' '.join(str(m) for m in detalle.context['messages'])
        self.assertIn('2 pares agregados', texto)
        self.assertIn('2 etiquetas anuladas', texto)
        nuevos = ','.join(self.lote.pares.filter(color=self.blanco).order_by('pk')
                          .values_list('codigo', flat=True))
        self.assertIn(f'?codigos={nuevos}', texto)

    def test_post_con_error_no_cambia_nada(self):
        r = self.post(cantidades={self.campo(): 1}, lineas=[{
            'tipo_id': self.base.pk, 'color_id': self.blanco.pk,
            'cantidades': {'27': 2}, 'precio_proveedor': ''}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('precio', r.json()['error'])
        self.assertEqual(self.activos(), 3)

    def test_post_version_vieja(self):
        r = self.post(cantidades={self.campo(): 1}, version=-1)
        self.assertEqual(r.status_code, 400)
        self.assertIn('cambió', r.json()['error'])

    def test_post_clave_mal_formada(self):
        r = self.post(cantidades={'basura': 1})
        self.assertEqual(r.status_code, 400)

    def test_post_sin_cambios(self):
        r = self.post(cantidades={self.campo(): 3})
        self.assertEqual(r.status_code, 200)
        detalle = self.client.get(r.json()['url'])
        self.assertIn('Sin cambios', ' '.join(str(m) for m in detalle.context['messages']))


class AnuladosFueraDelDetalleTests(EditarBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        editar_lote(self.lote, cantidades={self.clave(): 1}, lineas=[],
                    usuario=self.user, version=None)

    def test_detalle_no_cuenta_anulados(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertEqual(r.context['total'], 1)
        self.assertEqual(r.context['n_anulados'], 2)
        self.assertEqual(sum(f['esperados'] for f in r.context['filas']), 1)
        self.assertEqual(len(r.context['no_escaneados']), 1)
        self.assertContains(r, reverse('inventario:lote_editar', args=[self.lote.pk]))

    def test_lista_de_lotes_no_cuenta_anulados(self):
        r = self.client.get(reverse('inventario:lotes'))
        self.assertEqual(r.context['lotes'][0].total, 1)

    def test_etiquetas_del_lote_excluyen_anulados(self):
        r = self.client.get(reverse('inventario:etiquetas_lote', args=[self.lote.pk])
                            + '?desde=2&hasta=3')
        self.assertEqual(r.status_code, 404)
