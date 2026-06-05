"""
Arma el carrito en modaverse.vip para un pedido de ryalsneackers.

Flujo:
1. Bootstrapping: navega a homepage → primera cat → primer sub → llega a /product/ con bar_box.
2. Agrupa los ítems del pedido por supplier_url (= mismo producto físico) para manejar
   correctamente los productos con múltiples tallas.
3. Por cada grupo de ítems (mismo producto):
   - Navega al listado de subcategoría vía bar_box (CLICK, no hover).
   - Encuentra la tarjeta del producto por SKU o nombre.
   - btn_1 (sin tallas): un solo click agrega al carrito.
   - btn_2 (con tallas): abre el dialog UNA SOLA VEZ, selecciona todas las tallas necesarias,
     ajusta cantidades, y hace click en "Agregar" una sola vez.
     Esto evita que Agregar repetitivo sobreescriba el ítem del mismo producto.
4. Para ítems btn_1 con qty > 1: actualiza directamente localStorage tras el click.
5. Exporta el localStorage SIN abrir el carrito (abrirlo dispara sync con el servidor
   que sobreescribe shopCarList con solo el último ítem actualizado).
6. Guarda todos los resultados DESPUÉS de cerrar Playwright (no ORM dentro del bloque).

Sin login — modaverse es acceso público.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from orders.models import SupplierOrder, SupplierOrderItem

MODAVERSE_BASE = 'https://www.modaverse.vip'
TIMEOUT   = 30_000   # ms
CART_WAIT = 1_500    # ms tras agregar un ítem
MAX_PAGES = 8        # páginas máximas a buscar en un listado


class Command(BaseCommand):
    help = 'Arma el carrito en modaverse.vip para el pedido dado'

    def add_arguments(self, parser):
        parser.add_argument('order_id', type=int)
        parser.add_argument('--headless', action='store_true', default=False,
                            help='Ejecutar Chromium sin ventana visible')

    def handle(self, *args, **options):
        order_id = options['order_id']
        headless  = options['headless']

        # ── CARGA INICIAL (Django ORM — fuera de Playwright) ─────────────────
        try:
            supplier_order = (
                SupplierOrder.objects
                .select_related('order')
                .prefetch_related('items__order_item__product__category__parent')
                .get(order_id=order_id)
            )
        except SupplierOrder.DoesNotExist:
            raise CommandError(
                f'No existe SupplierOrder para el pedido #{order_id}. '
                'Usa el botón "Armar en modaverse" desde el panel primero.'
            )

        pending_items = [i for i in supplier_order.items.all() if i.status == 'pending']
        if not pending_items:
            self.stdout.write(self.style.WARNING('No hay ítems pendientes. Nada que hacer.'))
            return

        supplier_order.status = 'running'
        supplier_order.save(update_fields=['status'])

        # Extraer todo a dicts planos — no se toca ORM dentro de Playwright
        def _cat_path(item):
            try:
                cat = item.order_item.product.category
                if cat:
                    parent = cat.parent.name if cat.parent else ''
                    return parent, cat.name
            except Exception:
                pass
            return '', ''

        items_data = []
        for item in pending_items:
            parent_cat, category = _cat_path(item)
            items_data.append({
                'id':           item.id,
                'sku':          item.order_item.sku_snapshot,
                'name':         item.order_item.name_snapshot,
                'supplier_url': item.supplier_url,
                'variant':      item.variant_target,
                'quantity':     item.order_item.quantity,
                'parent_cat':   parent_cat,
                'category':     category,
            })

        # Agrupar por supplier_url: mismo producto físico va en un solo grupo
        # (preserva el orden de primer aparición)
        item_groups = {}
        for data in items_data:
            key = data['supplier_url'] or f'__nurl_{data["id"]}'
            item_groups.setdefault(key, []).append(data)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError(
                'playwright no está instalado.\n'
                'Ejecuta: pip install playwright && playwright install chromium'
            )

        # ── PLAYWRIGHT (sin ORM) ──────────────────────────────────────────────
        results     = {}   # {item_id: {'status': str, 'notes': str}}
        cart_script = ''

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
            )
            ctx = browser.new_context(
                viewport={'width': 1280, 'height': 900},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
            )
            # Ocultar navigator.webdriver para evitar detección headless
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()

            self.stdout.write(f'Abriendo {MODAVERSE_BASE}...')
            self._bootstrap_to_product_page(page)

            try:
                for group in item_groups.values():
                    try:
                        group_results = self._add_item_group(page, group)
                    except Exception as exc:
                        first = group[0]
                        self.stdout.write(self.style.WARNING(f'  [{first["sku"]}] Error inesperado: {exc}'))
                        group_results = {
                            d['id']: {'status': 'variant_not_found', 'notes': f'Error inesperado: {exc}'}
                            for d in group
                        }
                    results.update(group_results)
                    self._debug_cart_size(page, group[0]['sku'])

                self.stdout.write('\n[pre-export] Estado final del carrito:')
                self._debug_cart_size(page, 'FINAL')
                cart_script = self._extract_cart_script(page)
                if cart_script:
                    self.stdout.write(self.style.SUCCESS('Script de carrito generado OK'))
                else:
                    self.stdout.write(self.style.WARNING('No se pudo exportar el carrito'))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'Error inesperado en Playwright: {exc}'))

            browser.close()

        # ── GUARDAR RESULTADOS (Django ORM — fuera de Playwright) ────────────
        for item in pending_items:
            r = results.get(item.id)
            if r:
                item.status = r['status']
                item.notes  = r['notes']
                item.save(update_fields=['status', 'notes'])

        statuses = {results[i.id]['status'] for i in pending_items if i.id in results}
        if statuses <= {'added', 'no_url'}:
            supplier_order.status = 'done'
        elif 'added' in statuses:
            supplier_order.status = 'partial'
        else:
            supplier_order.status = 'failed'

        supplier_order.cart_script = cart_script
        supplier_order.save(update_fields=['status', 'cart_script', 'updated_at'])

        items_final = list(supplier_order.items.select_related('order_item').all())
        self._print_summary(items_final, cart_script)

    # ── ITEM GROUP (punto de entrada por grupo de producto) ───────────────────

    def _add_item_group(self, page, group: list) -> dict:
        """
        Procesa todos los ítems del mismo producto en una sola navegación.
        Retorna {item_id: {'status', 'notes'}} para cada ítem del grupo.

        Siempre usa bar_box (navegar por categoría + buscar tarjeta).
        La navegación directa a #/proinfo/ fue descartada: la página del SPA
        renderiza pero btn_1/btn_2 son exclusivos de las tarjetas de listado.
        """
        first = group[0]
        sku   = first['sku']

        self.stdout.write(f'\n[{sku}] Buscando en categoría "{first["category"]}"...')
        navigated = self._navigate_to_category(page, first)
        if not navigated:
            note = f'No se pudo navegar a la categoría "{first["category"]}"'
            return {d['id']: {'status': 'variant_not_found', 'notes': note} for d in group}
        card = self._find_product_with_pagination(page, first)
        if card is None:
            note = f'Producto no encontrado (hasta {MAX_PAGES} páginas)'
            return {d['id']: {'status': 'variant_not_found', 'notes': note} for d in group}
        root = card
        try:
            root.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        except Exception:
            pass

        btn_simple = root.locator('button.btn_1').first
        btn_specs  = root.locator('button.btn_2').first

        # Usar count() en lugar de is_visible(): la tarjeta puede estar fuera
        # del viewport inicialmente y Playwright's click() ya hace scroll automático.
        if root.locator('button.btn_1').count() > 0:
            try:
                before = self._cart_len(page)
                btn_simple.click()
                page.wait_for_timeout(CART_WAIT)
                after = self._cart_len(page)
                grew = '' if after > before else '  <-- NO CRECIO'
                self.stdout.write(f'  [add btn_1 {sku}] cart {before} -> {after}{grew}')
                qty = group[0]['quantity']
                if qty > 1:
                    self._set_last_cart_item_qty(page, qty)
                self.stdout.write(self.style.SUCCESS('  [OK] Agregado (sin especificaciones)'))
                return {d['id']: {'status': 'added', 'notes': ''} for d in group}
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  btn_1 encontrado pero click falló: {exc}'))

        if root.locator('button.btn_2').count() > 0:
            return self._add_with_specs_multi(page, root, group)

        # Diagnóstico: esperar 3s más y listar todos los elementos con clase *btn*
        page.wait_for_timeout(3000)
        try:
            info = page.evaluate("""
                () => ({
                    url: location.href,
                    buttons: Array.from(document.querySelectorAll('button')).map(
                        b => b.className + ' | ' + b.innerText.trim().slice(0, 30)
                    ),
                    btn_divs: Array.from(document.querySelectorAll('[class*="btn"]')).slice(0, 10).map(
                        el => el.tagName + '.' + el.className + ' | ' + el.innerText.trim().slice(0, 30)
                    ),
                    body_len: document.body.innerHTML.length
                })
            """)
            self.stdout.write(f'  [diag] url={info["url"]}')
            self.stdout.write(f'  [diag] body_len={info["body_len"]}')
            self.stdout.write(f'  [diag] buttons={info["buttons"]}')
            self.stdout.write(f'  [diag] btn_divs={info["btn_divs"]}')
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  [diag] Error: {exc}'))
        note = 'Sin botón de compra visible'
        return {d['id']: {'status': 'variant_not_found', 'notes': note} for d in group}

    def _add_with_specs_multi(self, page, card, group: list) -> dict:
        """
        Abre el dialog de tallas y agrega cada talla con un click en "Agregar" separado.

        El selector de tallas es radio (un solo seleccionado a la vez): cada click
        en una .zhi_i reemplaza la selección anterior. Por eso se hace click en
        "Agregar" una vez POR TALLA, no una vez al final.

        La cantidad no se puede fijar con fill+Tab (Vue no reactiva), así que se
        actualiza directamente en localStorage con _set_last_cart_item_qty tras
        cada Agregar.
        """
        card.locator('button.btn_2').first.click()
        page.wait_for_timeout(900)

        dialog = page.locator('.el-dialog.dialog_box')
        if not dialog.is_visible():
            note = 'Diálogo de especificaciones no apareció'
            return {d['id']: {'status': 'variant_not_found', 'notes': note} for d in group}

        top_options = dialog.locator('.zhi_box .zhi_i')
        available   = [opt.locator('span').first.inner_text().strip() for opt in top_options.all()]

        item_results = {}
        added_parts  = []

        for data in group:
            variant    = data['variant'].strip()
            size_value = variant.removeprefix('Talla').strip() if variant.startswith('Talla') else variant
            qty        = data['quantity']
            item_id    = data['id']

            if not size_value:
                all_opts = top_options.all()
                if not all_opts:
                    item_results[item_id] = {'status': 'variant_not_found', 'notes': 'Sin opciones en el diálogo'}
                    continue
                all_opts[0].click()
                page.wait_for_timeout(400)
                before = self._cart_len(page)
                add_btns = dialog.locator('.btn_box button.btn')
                self.stdout.write(f'  [add btn_2 {data["sku"]} (1ra opcion)] botones_agregar={add_btns.count()} cart_antes={before}')
                add_btns.first.click()
                page.wait_for_timeout(CART_WAIT)
                after = self._cart_len(page)
                grew = '' if after > before else '  <-- NO CRECIO'
                self.stdout.write(f'  [add btn_2 {data["sku"]} (1ra opcion)] cart {before} -> {after}{grew}')
                if qty > 1:
                    self._set_last_cart_item_qty(page, qty)
                label = available[0] if available else '?'
                self.stdout.write(self.style.WARNING(f'  Sin variante — seleccionando primera: "{label}"'))
                item_results[item_id] = {'status': 'added', 'notes': f'Sin variante — seleccionado: "{label}"'}
                added_parts.append(f'?×{qty}')
                continue

            matched = self._match_size_option(top_options, size_value)
            if not matched:
                note = f'Talla "{size_value}" no encontrada. Disponibles: {", ".join(available)}'
                item_results[item_id] = {'status': 'variant_not_found', 'notes': note}
                self.stdout.write(self.style.WARNING(f'  {note}'))
                continue

            matched.click()
            page.wait_for_timeout(400)
            before = self._cart_len(page)
            add_btns = dialog.locator('.btn_box button.btn')
            self.stdout.write(f'  [add btn_2 {data["sku"]} talla "{size_value}"] botones_agregar={add_btns.count()} cart_antes={before}')
            add_btns.first.click()
            page.wait_for_timeout(CART_WAIT)
            after = self._cart_len(page)
            grew = '' if after > before else '  <-- NO CRECIO'
            self.stdout.write(f'  [add btn_2 {data["sku"]} talla "{size_value}"] cart {before} -> {after}{grew}')
            if qty > 1:
                self._set_last_cart_item_qty(page, qty)
            item_results[item_id] = {'status': 'added', 'notes': ''}
            added_parts.append(f'{size_value}×{qty}')

        # Cerrar dialog
        try:
            if dialog.is_visible():
                dialog.locator('.el-dialog__headerbtn').click()
                page.wait_for_timeout(400)
        except Exception:
            pass

        if added_parts:
            self.stdout.write(self.style.SUCCESS(f'  [OK] Tallas agregadas: {", ".join(added_parts)}'))

        return item_results

    def _match_size_option(self, options, size_value: str):
        """Busca un .zhi_i que coincida con size_value (exacto, luego parcial)."""
        for opt in options.all():
            if opt.locator('span').first.inner_text().strip().lower() == size_value.lower():
                return opt
        for opt in options.all():
            txt = opt.locator('span').first.inner_text().strip().lower()
            if size_value.lower() in txt or txt in size_value.lower():
                return opt
        return None

    # ── NAVEGACIÓN ────────────────────────────────────────────────────────────

    def _navigate_to_category(self, page, data: dict) -> bool:
        """
        Navega al listado de subcategoría usando el bar_box (click, no hover).
        Si no estamos en una página /product/, hace bootstrap primero.
        """
        parent_cat = data.get('parent_cat', '')
        category   = data.get('category', '')

        if not category:
            return False

        try:
            if '/product/' not in page.url:
                if not self._bootstrap_to_product_page(page):
                    self.stdout.write(self.style.WARNING('  Bootstrap fallido — no se pudo llegar a /product/'))
                    return False

            return self._navigate_via_bar_box(page, parent_cat, category)

        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  Error navegando: {exc}'))
            return False

    def _bootstrap_to_product_page(self, page) -> bool:
        """
        Desde el homepage, hace 2 clicks para llegar a una página /product/{id}
        que tiene el bar_box con el menú de categorías.
        Siempre navega al homepage primero para garantizar estado limpio.
        """
        try:
            page.goto(MODAVERSE_BASE, timeout=TIMEOUT)
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(1500)

            first_cat = page.locator('.product_item').first
            if not first_cat.is_visible():
                return False
            first_cat.click()
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(1200)

            if '/zifenlei/' in page.url:
                first_sub = page.locator('.product_item').first
                if not first_sub.is_visible():
                    return False
                first_sub.click()
                page.wait_for_load_state('networkidle', timeout=TIMEOUT)
                page.wait_for_timeout(1200)

            ok = '/product/' in page.url
            if ok:
                self.stdout.write(f'  Bootstrap OK: {page.url}')
            return ok

        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  Bootstrap error: {exc}'))
            return False

    def _navigate_via_bar_box(self, page, parent_cat: str, category: str) -> bool:
        """
        Usa el bar_box (click para abrir) para navegar a la subcategoría correcta.
        Estructura: .bar_box .body_box (trigger) → .info_box .mu_i (padre) → .info_box .option (sub)
        """
        trigger = page.locator('.bar_box .body_box').first
        if not trigger.is_visible():
            self.stdout.write(self.style.WARNING('  bar_box trigger no visible'))
            return False

        trigger.click()
        page.wait_for_timeout(800)

        if parent_cat:
            mu_i = page.locator('.info_box .mu_i').filter(has_text=parent_cat).first
            if mu_i.is_visible():
                mu_i.click()
                page.wait_for_timeout(500)

        option = page.locator('.info_box .option').filter(has_text=category).first
        if not option.is_visible():
            self.stdout.write(self.style.WARNING(f'  Subcategoría "{category}" no encontrada en el menú'))
            return False

        option.click()
        page.wait_for_load_state('networkidle', timeout=TIMEOUT)
        # Esperar a que Vue renderice al menos un producto antes de buscar
        try:
            page.wait_for_selector('.product_item', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        self.stdout.write(f'  Navegado a "{category}"')
        return True

    # ── BÚSQUEDA DE PRODUCTO ──────────────────────────────────────────────────

    def _find_product_with_pagination(self, page, data: dict):
        """
        Busca .product_item por SKU o nombre (has-text, no exact match) con paginación.
        """
        for page_num in range(1, MAX_PAGES + 1):
            card = self._find_product_card(page, data)
            if card is not None:
                return card

            next_btn = page.locator('.el-pagination .btn-next').first
            if not next_btn.is_visible() or next_btn.get_attribute('disabled') is not None:
                break

            next_btn.click()
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(800)
            self.stdout.write(f'  Pagina {page_num + 1}...')

        # No encontrado — imprimir qué nombres hay en la página para diagnóstico
        try:
            names = page.evaluate("""
                () => Array.from(document.querySelectorAll('.pro_name'))
                      .slice(0, 8)
                      .map(el => el.innerText.trim())
            """)
            buscando = (data.get('name') or '')[:50]
            self.stdout.write(f'  [no encontrado] buscando: "{buscando}"')
            for n in names:
                try:
                    self.stdout.write(f'    - {n[:60]}')
                except UnicodeEncodeError:
                    self.stdout.write(f'    - (nombre con chars especiales)')
        except Exception:
            pass

        return None

    def _find_product_card(self, page, data: dict):
        """
        Busca .product_item por varias estrategias (orden de prioridad):
        1. Nombre completo (has-text substring) — basta con count()>0; click() ya hace scroll
        2. Primeras 3 palabras del nombre (útil si el nombre está truncado)
        3. Única card en la página → asumir que es el producto correcto
        """
        name = (data.get('name') or '').strip()

        if name:
            card = page.locator(f'.product_item:has(.pro_name:has-text("{name}"))').first
            if card.count() > 0:
                return card

        words = name.split()
        if len(words) >= 3:
            short = ' '.join(words[:3])
            card = page.locator(f'.product_item:has(.pro_name:has-text("{short}"))').first
            if card.count() > 0:
                return card

        all_cards = page.locator('.product_item').all()
        if len(all_cards) == 1:
            return page.locator('.product_item').first

        return None

    # ── CARRITO ────────────────────────────────────────────────────────────────

    def _cart_len(self, page) -> int:
        """Devuelve la cantidad de ítems en shopCarList (o -1 si falla la lectura)."""
        try:
            return page.evaluate("""
                () => {
                    try {
                        const u = JSON.parse(localStorage.getItem('user') || '{}');
                        return (u.shopCarList || []).length;
                    } catch(e) { return -1; }
                }
            """)
        except Exception:
            return -1

    def _debug_cart_size(self, page, sku: str):
        """Imprime el contenido completo del shopCarList tras cada grupo."""
        try:
            info = page.evaluate("""
                () => {
                    try {
                        const u = JSON.parse(localStorage.getItem('user') || '{}');
                        const cart = u.shopCarList || [];
                        return {
                            count: cart.length,
                            items: cart.map(function(i) {
                                return {
                                    name: (i.productName || i.name || i.productId || '?').slice(0, 40),
                                    qty:   i.num || i.quantity || 1,
                                    specs: i.specsValue || ''
                                };
                            })
                        };
                    } catch(e) { return {count: -1, items: []}; }
                }
            """)
            self.stdout.write(f'  [cart tras {sku}] {info["count"]} ítem(s):')
            for item in (info.get('items') or []):
                spec = f' [{item["specs"]}]' if item.get('specs') else ''
                try:
                    self.stdout.write(f'    · {item["name"]} ×{item["qty"]}{spec}')
                except UnicodeEncodeError:
                    self.stdout.write(f'    · (nombre especial) ×{item["qty"]}{spec}')
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  [cart] Error: {exc}'))

    def _set_last_cart_item_qty(self, page, qty: int):
        """
        Actualiza el campo `num` del último ítem de shopCarList en localStorage.
        Llamar inmediatamente después de btn_1.click() cuando qty > 1.
        No abre el carrito (abrir el carrito dispara un sync con el servidor
        que sobreescribe shopCarList con solo el ítem actualizado).
        """
        page.evaluate(f"""
            () => {{
                try {{
                    const u = JSON.parse(localStorage.getItem('user') || '{{}}');
                    if (u.shopCarList && u.shopCarList.length > 0) {{
                        u.shopCarList[u.shopCarList.length - 1].num = {qty};
                        localStorage.setItem('user', JSON.stringify(u));
                    }}
                }} catch(e) {{}}
            }}
        """)

    def _extract_cart_script(self, page) -> str:
        """
        Extrae el shopCarList de modaverse y genera un script JS que el usuario
        pega en la consola de su navegador.

        El script fusiona shopCarList en el objeto 'user' existente del navegador
        del usuario, preservando su sesión (userToken) si está logueado.
        """
        try:
            shop_car_list = page.evaluate("""
                () => {
                    const raw = localStorage.getItem('user');
                    if (!raw) return null;
                    try {
                        const parsed = JSON.parse(raw);
                        return parsed.shopCarList || null;
                    } catch(e) {
                        return null;
                    }
                }
            """)

            if not shop_car_list:
                self.stdout.write(self.style.WARNING('  shopCarList vacío — el carrito no tiene ítems'))
                return ''

            self.stdout.write(f'  shopCarList extraído: {len(shop_car_list)} ítem(s)')
            for entry in shop_car_list:
                name  = str(entry.get('productName') or entry.get('name') or entry.get('productId') or '?')[:50]
                qty   = entry.get('num') or entry.get('quantity') or 1
                specs = entry.get('specsValue', '')
                spec_str = f' [{specs}]' if specs else ''
                try:
                    self.stdout.write(f'    · {name} ×{qty}{spec_str}')
                except UnicodeEncodeError:
                    self.stdout.write(f'    · (nombre especial) ×{qty}{spec_str}')

            cart_json = json.dumps(shop_car_list, ensure_ascii=False)
            # Fusiona shopCarList en el user existente → preserva userToken/sesión.
            # location.reload() fuerza recarga completa para que Vue reinicialice
            # su store desde localStorage; window.location.hash no sirve porque
            # el router SPA de Vue cambia la ruta sin recargar la página y el
            # store en memoria no se actualiza.
            return (
                "(function(){"
                f"var c={cart_json};"
                "console.log('[ryal] Inyectando', c.length, 'ítem(s):', c.map(function(i){return (i.productName||i.name||'?')+'(×'+(i.num||1)+')';}));"
                "var u=JSON.parse(localStorage.getItem('user')||'{}');"
                "u.shopCarList=c;"
                "localStorage.setItem('user',JSON.stringify(u));"
                "console.log('[ryal] ✅ Carrito inyectado. Recargando página...');"
                "location.reload();"
                "})()"
            )
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  Error exportando carrito: {exc}'))
            return ''

    def _print_summary(self, items, cart_script):
        self.stdout.write('\n--- Resumen ---')
        for item in items:
            icon = {'added': 'OK', 'variant_not_found': '!!', 'no_url': '--', 'pending': '??'}.get(item.status, '??')
            try:
                self.stdout.write(f'  [{icon}] {item.order_item.sku_snapshot} -- {item.get_status_display()}')
                if item.notes:
                    self.stdout.write(f'      {item.notes[:120]}')
            except UnicodeEncodeError:
                self.stdout.write(f'  [{icon}] {item.order_item.sku_snapshot}')

        if cart_script:
            self.stdout.write('\n  Para importar el carrito en tu navegador:')
            self.stdout.write(f'  1. Abre {MODAVERSE_BASE} (cualquier página, NO la del carrito)')
            self.stdout.write('  2. F12 → Console → escribe "allow pasting" → Enter')
            self.stdout.write('  3. Pega el script del panel → Enter')
            self.stdout.write('  4. La página se recarga automáticamente con el carrito cargado')
            self.stdout.write('  5. Navega al carrito desde el ícono en la UI de modaverse')
        self.stdout.write('')
