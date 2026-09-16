from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from catalog.modaverse import esta_agotado, registro_sano, status_proveedor
from catalog.models import Category, Product


class EstaAgotadoTests(SimpleTestCase):

    def test_stock_cero_agota(self):
        self.assertTrue(esta_agotado(0, '0'))

    def test_stock_negativo_agota(self):
        """Modaverse manda -1 cuando está sobrevendido (ZA-266)."""
        self.assertTrue(esta_agotado(-1, '0'))

    def test_se_vende_sin_stock_no_agota(self):
        """ynStockForZero '1' es "散货": se vende aunque no haya stock."""
        self.assertFalse(esta_agotado(-1, '1'))

    def test_sin_el_campo_ynStockForZero_agota(self):
        self.assertTrue(esta_agotado(0, None))

    def test_con_stock_no_agota(self):
        self.assertFalse(esta_agotado(14, '0'))

    def test_sin_dato_de_stock_no_agota(self):
        self.assertFalse(esta_agotado(None, '0'))

    def test_stock_como_texto_numerico(self):
        self.assertTrue(esta_agotado('0', '0'))

    def test_cantidad_mayor_al_stock_falta(self):
        self.assertTrue(esta_agotado(3, '0', 5))
        self.assertFalse(esta_agotado(5, '0', 5))


class StatusProveedorTests(SimpleTestCase):

    def test_despublicado_manda_sobre_el_stock(self):
        self.assertEqual(status_proveedor('0', -1, '0'), 'unlaunched')
        self.assertEqual(status_proveedor('0', 14, '0'), 'unlaunched')

    def test_publicado_y_agotado(self):
        self.assertEqual(status_proveedor('1', 0, '0'), 'out_of_stock')

    def test_publicado_con_stock(self):
        self.assertEqual(status_proveedor('1', 14, '0'), 'available')

    def test_publicado_que_se_vende_sin_stock(self):
        self.assertEqual(status_proveedor('1', -1, '1'), 'available')


class RegistroSanoTests(SimpleTestCase):
    SANO = {'sku': 'P1', 'category_id': 'CAP-1', 'price_mxn': 220}

    def test_registro_completo_es_sano(self):
        self.assertTrue(registro_sano(self.SANO))

    def test_sin_categoria_es_carcasa(self):
        self.assertFalse(registro_sano(dict(self.SANO, category_id='')))

    def test_precio_cero_es_carcasa(self):
        self.assertFalse(registro_sano(dict(self.SANO, price_mxn=0)))

    def test_precio_nulo_es_carcasa(self):
        self.assertFalse(registro_sano(dict(self.SANO, price_mxn=None)))

    def test_sin_sku_es_carcasa(self):
        self.assertFalse(registro_sano(dict(self.SANO, sku='')))


class SyncStockModaverseTests(TestCase):
    """JSON inyectado con mock de read_modaverse_json; nunca toca el JSON real."""

    TREE = [{'id': 'CAP', 'name_es': 'Gorras',
             'subcategories': [{'id': 'CAP-1', 'name_es': 'Dandy y Barbas'}]}]

    def setUp(self):
        self.root = Category.objects.create(name='Gorras', slug='gorras')
        self.sub = Category.objects.create(name='Dandy y Barbas', slug='dandy-y-barbas', parent=self.root)

    def _make(self, pid, status='available', *, category=None, url=None, is_active=True):
        return Product.objects.create(
            sku=f'RYL-TEST-{pid}', name=f'Gorra {pid}',
            category=category or self.sub, base_price=Decimal('200'),
            supplier_url=url if url is not None else f'https://www.modaverse.vip/#/proinfo/{pid}',
            status=status, is_active=is_active,
        )

    def _rec(self, pid, status, **over):
        return dict({'sku': pid, 'category_id': 'CAP-1', 'price_mxn': 220, 'status': status}, **over)

    def _relleno(self, n=20):
        """n gorras activas y disponibles en ambos lados: 1 agotado nuevo de 21 = 4.8% < 5%."""
        recs = []
        for i in range(n):
            pid = f'R{i:03}'
            self._make(pid)
            recs.append(self._rec(pid, 'available'))
        return recs

    def _call(self, *args, products):
        out, err = StringIO(), StringIO()
        with patch('catalog.management.commands.sync_stock_modaverse.read_modaverse_json',
                   return_value={'categories': self.TREE, 'products': products}):
            call_command('sync_stock_modaverse', '--category', 'gorra', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def _status(self, product):
        product.refresh_from_db()
        return product.status

    def test_disponible_pasa_a_agotado(self):
        p = self._make('P1')
        out, _ = self._call(products=self._relleno() + [self._rec('P1', 'out_of_stock')])
        self.assertEqual(self._status(p), 'sold_out')
        self.assertIn('a_agotado=1', out)

    def test_agotado_vuelve_a_disponible(self):
        p = self._make('P1', 'sold_out')
        out, _ = self._call(products=[self._rec('P1', 'available')])
        self.assertEqual(self._status(p), 'available')
        self.assertIn('a_disponible=1', out)

    def test_url_de_formato_viejo_tambien_se_sincroniza(self):
        p = self._make('P1', 'sold_out', url='https://www.modaverse.vip/#/product/CAP-1?pid=P1')
        self._call(products=[self._rec('P1', 'available')])
        self.assertEqual(self._status(p), 'available')

    def test_proximamente_nunca_se_toca(self):
        p = self._make('P1', 'coming_soon')
        self._call(products=self._relleno() + [self._rec('P1', 'out_of_stock')])
        self.assertEqual(self._status(p), 'coming_soon')

    def test_productos_sin_url_de_modaverse_no_cambian(self):
        yupoo = self._make('P1', url='https://putianshoefactory.x.yupoo.com/albums/P1')
        manual = self._make('P2', url='')
        self._call(products=self._relleno() + [self._rec('P1', 'out_of_stock'),
                                               self._rec('P2', 'out_of_stock')])
        self.assertEqual(self._status(yupoo), 'available')
        self.assertEqual(self._status(manual), 'available')

    def test_producto_de_otra_categoria_no_cambia(self):
        ropa = Category.objects.create(name='Ropa', slug='ropa')
        p = self._make('P1', category=ropa)
        self._call(products=self._relleno() + [self._rec('P1', 'out_of_stock')])
        self.assertEqual(self._status(p), 'available')

    def test_carcasa_agotada_no_marca_agotado(self):
        p = self._make('P1')
        out, _ = self._call(products=self._relleno() + [self._rec('P1', 'out_of_stock', price_mxn=0)])
        self.assertEqual(self._status(p), 'available')
        self.assertIn('carcasas_ignoradas=1', out)

    def test_carcasa_disponible_si_devuelve_a_disponible(self):
        """El error posible al devolver es dejar vendible algo que existe."""
        p = self._make('P1', 'sold_out')
        self._call(products=[self._rec('P1', 'available', price_mxn=0)])
        self.assertEqual(self._status(p), 'available')

    def test_umbral_superado_no_aplica_ningun_cambio(self):
        """4 de 5 activos pasarían a agotado (80% > 5%): tampoco vuelve el agotado."""
        nuevos = [self._make(f'A{i}') for i in range(4)]
        vuelve = self._make('S1', 'sold_out')
        recs = [self._rec(f'A{i}', 'out_of_stock') for i in range(4)] + [self._rec('S1', 'available')]
        _, err = self._call(products=recs)
        self.assertEqual([self._status(p) for p in nuevos], ['available'] * 4)
        self.assertEqual(self._status(vuelve), 'sold_out')
        self.assertIn('umbral', err.lower())

    def test_force_aplica_aunque_supere_el_umbral(self):
        nuevos = [self._make(f'A{i}') for i in range(4)]
        vuelve = self._make('S1', 'sold_out')
        recs = [self._rec(f'A{i}', 'out_of_stock') for i in range(4)] + [self._rec('S1', 'available')]
        self._call('--force', products=recs)
        self.assertEqual([self._status(p) for p in nuevos], ['sold_out'] * 4)
        self.assertEqual(self._status(vuelve), 'available')

    def test_zero_guard_json_sin_productos_del_alcance(self):
        p = self._make('P1', 'sold_out')
        _, err = self._call(products=[])
        self.assertEqual(self._status(p), 'sold_out')
        self.assertIn('zero', err.lower())

    def test_dry_run_no_escribe(self):
        p = self._make('P1', 'sold_out')
        out, _ = self._call('--dry-run', products=[self._rec('P1', 'available')])
        self.assertEqual(self._status(p), 'sold_out')
        self.assertIn('[dry-run]', out)
        self.assertIn('a_disponible=1', out)

    def test_pid_ausente_del_json_no_cambia(self):
        agotado = self._make('P1', 'sold_out')
        disponible = self._make('P3')
        self._call(products=[self._rec('P2', 'available')])
        self.assertEqual(self._status(agotado), 'sold_out')
        self.assertEqual(self._status(disponible), 'available')

    def test_despublicado_no_cambia(self):
        """Ocultar lo despublicado es trabajo de reconcile_catalog."""
        agotado = self._make('P1', 'sold_out')
        disponible = self._make('P2')
        self._call(products=[self._rec('P1', 'unlaunched'), self._rec('P2', 'unlaunched')])
        self.assertEqual(self._status(agotado), 'sold_out')
        self.assertEqual(self._status(disponible), 'available')


class AutoSyncStockTests(TestCase):
    """subprocess.run y call_command parcheados: no se scrapea ni se carga nada."""

    MOD = 'catalog.management.commands.auto_sync_catalog'

    def _run(self, *args, returncode=0):
        orden, out = [], StringIO()

        def fake_run(cmd, **kwargs):
            orden.append(('scrape', cmd))
            return MagicMock(returncode=returncode)

        def fake_call(name, **kwargs):
            orden.append((name, kwargs))

        with patch(f'{self.MOD}.subprocess.run', side_effect=fake_run), \
             patch(f'{self.MOD}.call_command', side_effect=fake_call):
            call_command('auto_sync_catalog', *args, stdout=out)
        return orden, out.getvalue()

    def test_stock_only_scrapea_reconcilia_y_sincroniza_en_ese_orden(self):
        orden, _ = self._run('--stock-only')
        self.assertEqual([n for n, _ in orden], ['scrape', 'reconcile_catalog', 'sync_stock_modaverse'])
        cmd = orden[0][1]
        self.assertIn('--category', cmd)
        self.assertEqual(cmd[cmd.index('--category') + 1], 'gorra')
        self.assertIn('--no-browser', cmd)
        self.assertEqual(orden[1][1]['category'], ['gorra'])
        self.assertEqual(orden[2][1]['category'], ['gorra'])

    def test_stock_only_no_carga_productos_ni_imagenes(self):
        orden, _ = self._run('--stock-only')
        nombres = [n for n, _ in orden]
        for cmd in ('load_productos', 'import_pending_images', 'import_images'):
            self.assertNotIn(cmd, nombres)

    def test_stock_only_con_scrape_fallido_no_toca_el_catalogo(self):
        """Con el JSON viejo no se reconcilia ni se sincroniza."""
        orden, out = self._run('--stock-only', returncode=1)
        self.assertEqual([n for n, _ in orden], ['scrape'])
        self.assertIn('falló', out)

    def test_modo_normal_sincroniza_stock_y_no_completa_galerias(self):
        orden, _ = self._run('--day', '0')
        self.assertEqual(
            [n for n, _ in orden],
            ['scrape', 'load_productos', 'import_pending_images', 'sync_stock_modaverse'] * 2,
        )
        self.assertEqual(orden[3][1]['category'], ['gorra'])
        self.assertEqual(orden[7][1]['category'], ['deportiva'])

    def test_modo_normal_con_scrape_fallido_no_sincroniza_stock(self):
        orden, _ = self._run('--skip-gorra', '--day', '0', returncode=1)
        self.assertEqual([n for n, _ in orden], ['scrape', 'load_productos', 'import_pending_images'])

    def test_calzado_no_sincroniza_stock(self):
        """Calzado viene de yupoo, no de Modaverse."""
        orden, _ = self._run('--skip-gorra', '--day', '3')
        self.assertEqual([n for n, _ in orden], ['load_productos', 'import_pending_images'])
