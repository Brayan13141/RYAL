"""Pedido real #36 (RY2609120001) de producción como caso de regresión del carrito.

19 renglones, 20 piezas, todo gorras sin variantes. Con la versión de Playwright
el pedido quedó 'partial': 4 renglones "no encontrados" y un cart_script con solo
2 productos — uno de ellos, YAA0329, ni siquiera estaba en el pedido — aunque 15
renglones figuraban como 'added'.

Fixtures en test_data/pedido_36/, capturados el 2026-09-16:
  pedido.json                         renglones del pedido tal como están en prod
  api_getProductById.json             respuesta real de la API para cada pid
  entrada_generada_por_el_sitio.json  una entrada de shopCarList del carrito viejo

Los tests no tocan la red. El último sí, y solo con MODAVERSE_LIVE=1.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils.text import slugify

from catalog.models import Category, Product
from orders.models import Order, OrderItem, SupplierOrder, SupplierOrderItem

MOD  = 'orders.management.commands.sync_modaverse_order'
DATA = Path(__file__).resolve().parent / 'test_data' / 'pedido_36'


def _cargar(nombre):
    with open(DATA / nombre, encoding='utf-8') as f:
        return json.load(f)


def _pid(url):
    return re.search(r'/proinfo/(\w+)', url).group(1)


def _entradas(script):
    return json.loads(script[script.find('var c=') + 6:script.find(';console.log')])


class Pedido36Base(TestCase):

    def setUp(self):
        self.renglones = _cargar('pedido.json')
        self.api       = _cargar('api_getProductById.json')
        self.order     = Order.objects.create(customer_name='Pedido 36', customer_phone='4450000036')
        self.so        = SupplierOrder.objects.create(order=self.order)
        cats = {}
        for i, r in enumerate(self.renglones):
            if r['category'] not in cats:
                cats[r['category']] = Category.objects.create(
                    name=r['category'], slug=f'{slugify(r["category"])}-{i}')
            prod = Product.objects.create(
                sku=r['sku'], name=r['name'], category=cats[r['category']],
                base_price=100, supplier_url=r['supplier_url'],
            )
            oi = OrderItem.objects.create(
                order=self.order, product=prod, quantity=r['quantity'],
                price_snapshot=200, sku_snapshot=r['sku'], name_snapshot=r['name'],
                variant_snapshot=r['variant'],
            )
            SupplierOrderItem.objects.create(
                supplier_order=self.so, order_item=oi, supplier_url=r['supplier_url'],
                variant_target=r['variant_target'], status='pending',
            )

    def _correr(self, get_product=None):
        fake = get_product or (lambda pid, client=None: self.api.get(pid))
        with patch(f'{MOD}.get_product', side_effect=fake):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.so.refresh_from_db()
        return _entradas(self.so.cart_script)


class Pedido36CarritoTests(Pedido36Base):

    def test_todos_los_renglones_quedan_added_y_el_pedido_done(self):
        self._correr()
        estados = Counter(self.so.items.values_list('status', flat=True))
        self.assertEqual(estados, {'added': 19})
        self.assertEqual(self.so.status, 'done')

    def test_el_carrito_trae_exactamente_los_productos_del_pedido(self):
        """El viejo metía YAA0329, que no estaba en el pedido, y dejaba fuera 17."""
        arr = self._correr()
        esperados = {_pid(r['supplier_url']) for r in self.renglones}
        self.assertEqual(len(arr), 19)
        self.assertEqual({e['productId'] for e in arr}, esperados)
        self.assertNotIn('YAA0329', {e['productName'] for e in arr})

    def test_cantidades_por_producto(self):
        arr = self._correr()
        por_pid = {e['productId']: e['num'] for e in arr}
        for r in self.renglones:
            self.assertEqual(por_pid[_pid(r['supplier_url'])], r['quantity'], r['sku'])
        self.assertEqual(por_pid['2018896726101979138'], 2)   # ZA-070 ×2
        self.assertEqual(sum(por_pid.values()), 20)

    def test_el_nombre_de_modaverse_coincide_con_el_del_pedido(self):
        """El pid sale de supplier_url: si apuntara a otro producto, se vería acá."""
        arr = self._correr()
        nombre = {e['productId']: e['productName'] for e in arr}
        for r in self.renglones:
            self.assertEqual(nombre[_pid(r['supplier_url'])], r['name'])

    def test_despublicados_entran_con_aviso(self):
        self._correr()
        notas = dict(self.so.items.values_list('order_item__sku_snapshot', 'notes'))
        self.assertIn('despublicado', notas['RYL-CAP-8977'])   # ZA-179
        self.assertIn('despublicado', notas['RYL-CAP-8766'])   # ZA-259
        self.assertEqual(notas['RYL-CAP-054'], '')             # ZA-070, publicado y con stock

    def test_cada_entrada_tiene_los_campos_de_una_entrada_real_del_sitio(self):
        real = _cargar('entrada_generada_por_el_sitio.json')
        for e in self._correr():
            self.assertEqual(set(e), set(real), e['productName'])
            self.assertEqual(e['orderSpecificationsList'], [])
            self.assertEqual(e['guige'], [])

    def test_api_caida_a_mitad_del_pedido_no_deja_carrito(self):
        from catalog.modaverse_api import ModaverseUnavailable
        llamadas = []

        def cae_en_la_decima(pid, client=None):
            llamadas.append(pid)
            if len(llamadas) == 10:
                raise ModaverseUnavailable('429')
            return self.api.get(pid)

        with patch(f'{MOD}.get_product', side_effect=cae_en_la_decima):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.so.refresh_from_db()
        self.assertEqual(self.so.status, 'failed')
        self.assertEqual(self.so.cart_script, '')


@unittest.skipUnless(shutil.which('node'), 'node no está instalado')
class Pedido36ScriptEnNavegadorTests(Pedido36Base):
    """Ejecuta el cart_script con node y un localStorage simulado.

    Es lo más cerca del navegador que se puede llegar sin sesión en modaverse:
    prueba que el snippet es JS válido, que deja shopCarList con los 19 ítems,
    que no pisa el userToken de la sesión y que recarga la página.
    """

    ARNES = r"""
const fs = require('fs');
const store = {user: JSON.stringify({userToken: 'TOKEN-DE-SESION', userName: 'ryal', shopCarList: [{productId: 'VIEJO'}]})};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
let recargo = false;
global.location = {reload: () => { recargo = true; }};
console.log = () => {};
// eval a propósito: reproduce pegar el snippet en la consola. El script lo
// genera nuestro propio comando dentro del test, no viene de afuera.
eval(fs.readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify({user: JSON.parse(store.user), recargo}));
"""

    def test_el_script_deja_el_carrito_en_localstorage(self):
        self._correr()
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'cart.js'
            arnes  = Path(tmp) / 'arnes.js'
            script.write_text(self.so.cart_script, encoding='utf-8')
            arnes.write_text(self.ARNES, encoding='utf-8')
            proc = subprocess.run(
                ['node', str(arnes), str(script)],
                capture_output=True, text=True, encoding='utf-8', timeout=30,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        res = json.loads(proc.stdout)
        user = res['user']
        self.assertTrue(res['recargo'])
        self.assertEqual(user['userToken'], 'TOKEN-DE-SESION')
        self.assertEqual(user['userName'], 'ryal')
        self.assertEqual(len(user['shopCarList']), 19)
        self.assertNotIn('VIEJO', {e['productId'] for e in user['shopCarList']})
        self.assertEqual(sum(e['num'] for e in user['shopCarList']), 20)


@unittest.skipUnless(os.environ.get('MODAVERSE_LIVE') == '1',
                     'pega a la API real; correr con MODAVERSE_LIVE=1')
class Pedido36ApiEnVivoTests(Pedido36Base):
    """Mismo pedido contra la API real: detecta si Modaverse cambió algo."""

    def test_api_real_arma_los_19(self):
        from catalog.modaverse_api import get_product
        arr = self._correr(get_product=get_product)
        self.assertEqual(Counter(self.so.items.values_list('status', flat=True)), {'added': 19})
        self.assertEqual(len(arr), 19)
        real = _cargar('entrada_generada_por_el_sitio.json')
        for e in arr:
            self.assertEqual(set(e), set(real), e['productName'])
