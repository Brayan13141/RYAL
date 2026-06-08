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
            # Preferir el nombre actual del producto (actualizado por load_productos)
            # sobre el snapshot congelado al crear el pedido
            try:
                current_name = item.order_item.product.name or ''
            except Exception:
                current_name = ''
            name = current_name or item.order_item.name_snapshot
            items_data.append({
                'id':           item.id,
                'sku':          item.order_item.sku_snapshot,
                'name':         name,
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
            # Fallback: probar todas las opciones del bar_box con este nombre de categoría
            pid = self._extract_pid(first.get('supplier_url', ''))
            if pid and self._try_other_category_options(page, first['category'], pid):
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
        Abre el dialog de specs (btn_2) y agrega cada ítem del grupo.

        El diálogo puede tener una o dos filas (.zhi_box):
          - Solo tallas: una fila con las tallas disponibles.
          - Tallas + colores: dos filas (una por dimensión).
        variant_target puede ser "Talla L", "Talla L / Rojo burdeos" o un color solo.

        Seleccionamos color primero (si aplica), luego talla. Hacemos click en
        "Agregar" una vez por ítem (el diálogo permanece abierto entre adds del
        mismo producto, ya que las tallas son radio-buttons).
        """
        card.locator('button.btn_2').first.click()
        page.wait_for_timeout(900)

        dialog = page.locator('.el-dialog.dialog_box')
        if not dialog.is_visible():
            note = 'Diálogo de especificaciones no apareció'
            return {d['id']: {'status': 'variant_not_found', 'notes': note} for d in group}

        item_results = {}
        added_parts  = []

        for data in group:
            variant     = data['variant'].strip()
            size_value, color_value = self._parse_variant(variant)
            qty         = data['quantity']
            item_id     = data['id']
            sku         = data['sku']

            self.stdout.write(f'  [specs {sku}] variant="{variant}" → size="{size_value}" color="{color_value}"')

            selected_label = ''

            if color_value or size_value:
                # Seleccionar color primero (puede actualizar las opciones de talla)
                if color_value:
                    opt = self._find_option_in_dialog(dialog, color_value, skip_value=size_value)
                    if opt:
                        opt.click()
                        page.wait_for_timeout(400)
                        self.stdout.write(f'  [specs {sku}] color "{color_value}" OK')
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'  [specs {sku}] color "{color_value}" no encontrado — '
                            f'opciones: {self._list_dialog_options(dialog)}'
                        ))

                # Seleccionar talla
                if size_value:
                    opt = self._find_option_in_dialog(dialog, size_value, skip_value=color_value)
                    if opt:
                        opt.click()
                        page.wait_for_timeout(400)
                        self.stdout.write(f'  [specs {sku}] talla "{size_value}" OK')
                        selected_label = variant
                    else:
                        note = (
                            f'Talla "{size_value}" no encontrada. '
                            f'Opciones: {self._list_dialog_options(dialog)}'
                        )
                        self.stdout.write(self.style.WARNING(f'  [specs {sku}] {note}'))
                        item_results[item_id] = {'status': 'variant_not_found', 'notes': note}
                        continue
                else:
                    selected_label = variant  # solo color

            else:
                # Sin variante — seleccionar primera opción disponible
                all_opts = dialog.locator('.zhi_box .zhi_i').all()
                if not all_opts:
                    item_results[item_id] = {'status': 'variant_not_found', 'notes': 'Sin opciones en el diálogo'}
                    continue
                all_opts[0].click()
                page.wait_for_timeout(400)
                first_label = all_opts[0].locator('span').first.inner_text().strip()
                self.stdout.write(self.style.WARNING(
                    f'  [specs {sku}] Sin variante — seleccionando primera opción: "{first_label}"'
                ))
                selected_label = first_label

            before   = self._cart_len(page)
            add_btns = dialog.locator('.btn_box button.btn')
            self.stdout.write(
                f'  [add btn_2 {sku} "{variant}"] botones_agregar={add_btns.count()} cart_antes={before}'
            )
            add_btns.first.click()
            page.wait_for_timeout(CART_WAIT)
            after = self._cart_len(page)
            grew  = '' if after > before else '  <-- NO CRECIO'
            self.stdout.write(f'  [add btn_2 {sku} "{variant}"] cart {before} -> {after}{grew}')

            if qty > 1:
                self._set_last_cart_item_qty(page, qty)

            notes = '' if (color_value or size_value) else f'Sin variante — seleccionado: "{selected_label}"'
            item_results[item_id] = {'status': 'added', 'notes': notes}
            added_parts.append(f'{selected_label}×{qty}')

        # Cerrar dialog
        try:
            if dialog.is_visible():
                dialog.locator('.el-dialog__headerbtn').click()
                page.wait_for_timeout(400)
        except Exception:
            pass

        if added_parts:
            self.stdout.write(self.style.SUCCESS(f'  [OK] Agregado: {", ".join(added_parts)}'))

        return item_results

    @staticmethod
    def _parse_variant(variant: str) -> tuple:
        """
        Extrae (size_value, color_value) de variant_target.
          "Talla L / Rojo burdeos" → ("L", "Rojo burdeos")
          "Talla L"                → ("L", "")
          "Rojo burdeos"           → ("", "Rojo burdeos")
          ""                       → ("", "")
        """
        if not variant:
            return '', ''
        if variant.startswith('Talla') and ' / ' in variant:
            size_part, color_part = variant.split(' / ', 1)
            return size_part.removeprefix('Talla').strip(), color_part.strip()
        if variant.startswith('Talla'):
            return variant.removeprefix('Talla').strip(), ''
        return '', variant.strip()

    def _find_option_in_dialog(self, dialog, value: str, skip_value: str = ''):
        """
        Busca un .zhi_i cuyo texto coincida con value en cualquier .zhi_box del diálogo.
        skip_value evita confundir dimensiones (ej. "Negro" al buscar "L").
        Primero exact match en todos los boxes, luego partial match.
        """
        # Exact match
        for box in dialog.locator('.zhi_box').all():
            for opt in box.locator('.zhi_i').all():
                txt = opt.locator('span').first.inner_text().strip()
                if skip_value and txt.lower() == skip_value.lower():
                    continue
                if txt.lower() == value.lower():
                    return opt
        # Partial match
        for box in dialog.locator('.zhi_box').all():
            for opt in box.locator('.zhi_i').all():
                txt = opt.locator('span').first.inner_text().strip()
                if skip_value and txt.lower() == skip_value.lower():
                    continue
                if value.lower() in txt.lower() or txt.lower() in value.lower():
                    return opt
        return None

    def _list_dialog_options(self, dialog) -> str:
        """Devuelve descripción de todas las opciones por fila para diagnóstico."""
        rows = []
        for i, box in enumerate(dialog.locator('.zhi_box').all()):
            opts = [opt.locator('span').first.inner_text().strip()
                    for opt in box.locator('.zhi_i').all()]
            rows.append(f'Fila{i+1}:[{", ".join(opts)}]')
        return ' | '.join(rows) if rows else '(vacío)'

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
        Además captura los productIds del XHR que modaverse dispara al cargar la
        categoría; esto permite buscar por pid aunque el nombre haya cambiado.
        """
        trigger = page.locator('.bar_box .body_box').first
        if not trigger.is_visible():
            self.stdout.write(self.style.WARNING('  bar_box trigger no visible'))
            return False

        # Toggle-safe: cerrar si ya está abierto antes de abrir limpio
        if page.locator('.info_box').is_visible():
            trigger.click()
            page.wait_for_timeout(400)
        trigger.click()
        page.wait_for_timeout(800)

        # Diagnóstico: mostrar padres disponibles
        mu_all = page.locator('.info_box .mu_i').all()
        mu_texts = [m.inner_text().strip() for m in mu_all if m.is_visible()]
        self.stdout.write(f'  [nav] padres ({len(mu_texts)}): {mu_texts[:6]}')

        if parent_cat:
            # 1. Exact match
            mu_i = page.locator('.info_box .mu_i').filter(has_text=parent_cat).first
            if not mu_i.is_visible():
                # 2. Partial case-insensitive match (primeras palabras significativas)
                parent_lower = parent_cat.lower()
                for m in mu_all:
                    if not m.is_visible():
                        continue
                    txt = m.inner_text().strip().lower()
                    if any(w in txt for w in parent_lower.split() if len(w) > 4):
                        mu_i = m
                        break
            if mu_i.is_visible():
                mu_text = mu_i.inner_text().strip()
                mu_i.click()
                page.wait_for_timeout(500)
                self.stdout.write(f'  [nav] padre: "{mu_text}"')
            else:
                self.stdout.write(f'  [nav] padre "{parent_cat}" no encontrado — sin filtro de padre')

        # Mostrar opciones visibles tras click de padre
        opt_texts = [o.inner_text().strip() for o in page.locator('.info_box .option').all() if o.is_visible()]
        self.stdout.write(f'  [nav] opciones ({len(opt_texts)}): {opt_texts[:8]}')

        option = page.locator('.info_box .option').filter(has_text=category).first
        if not option.is_visible():
            self.stdout.write(self.style.WARNING(f'  Subcategoría "{category}" no encontrada en el menú'))
            return False
        option_text = option.inner_text().strip()
        self.stdout.write(f'  [nav] opción seleccionada: "{option_text}"')

        # Activar captura XHR antes del click de navegación
        self._page_product_pids = []
        self._page_pid_names    = {}
        pids_buf = []

        def _on_response(resp):
            self._try_capture_pids(resp, pids_buf)

        page.on('response', _on_response)
        option.click()
        page.wait_for_load_state('networkidle', timeout=TIMEOUT)
        try:
            page.wait_for_selector('.product_item', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        page.remove_listener('response', _on_response)

        if pids_buf:
            self._page_product_pids = list(pids_buf)
        self.stdout.write(
            f'  Navegado a "{category}" — {len(self._page_product_pids)} pids capturados'
        )
        return True

    def _try_other_category_options(self, page, category: str, target_pid: str) -> bool:
        """
        Fallback exhaustivo: itera TODOS los padres del bar_box buscando opciones
        que coincidan con `category`, verifica el XHR de cada una por `target_pid`.
        Cubre el caso donde el mismo nombre aparece bajo distintos padres
        (ej. "FOG" bajo Gorra Y bajo Camisetas).
        Retorna True si navega a la categoría donde se encontró el pid.
        """
        if not target_pid:
            return False

        trigger = page.locator('.bar_box .body_box').first
        if not trigger.is_visible():
            return False

        def _open_fresh():
            if page.locator('.info_box').is_visible():
                trigger.click()
                page.wait_for_timeout(400)
            trigger.click()
            page.wait_for_timeout(700)

        _open_fresh()

        mu_all = page.locator('.info_box .mu_i').all()
        mu_visible = [(j, m) for j, m in enumerate(mu_all) if m.is_visible()]
        self.stdout.write(
            f'  [fallback] Escaneando {len(mu_visible)} padres por "{category}" pid={target_pid[:20]}...'
        )

        for j, (_, mu) in enumerate(mu_visible):
            mu_text = mu.inner_text().strip()
            mu.click()
            page.wait_for_timeout(400)

            opts = [o for o in page.locator('.info_box .option').all()
                    if category.lower() in o.inner_text().strip().lower() and o.is_visible()]
            if not opts:
                continue

            for opt in opts:
                opt_text = opt.inner_text().strip()
                pids_buf = []

                def _on_resp(resp, buf=pids_buf):
                    self._try_capture_pids(resp, buf)

                page.on('response', _on_resp)
                opt.click()
                page.wait_for_load_state('networkidle', timeout=TIMEOUT)
                try:
                    page.wait_for_selector('.product_item', timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(800)
                page.remove_listener('response', _on_resp)

                if target_pid in pids_buf:
                    self._page_product_pids = list(pids_buf)
                    self.stdout.write(
                        f'  [fallback] ✓ pid en padre="{mu_text}" opción="{opt_text}"'
                    )
                    return True

                self.stdout.write(
                    f'  [fallback] padre="{mu_text}" opt="{opt_text}" — {len(pids_buf)} pids, no encontrado'
                )

                # Re-abrir y volver al mismo padre para la siguiente opción
                _open_fresh()
                mu_fresh = page.locator('.info_box .mu_i').all()
                if j < len(mu_fresh) and mu_fresh[j].is_visible():
                    mu_fresh[j].click()
                    page.wait_for_timeout(400)

        # Asegurar bar_box cerrado al salir
        if page.locator('.info_box').is_visible():
            trigger.click()
            page.wait_for_timeout(400)

        return False

    # ── BÚSQUEDA DE PRODUCTO ──────────────────────────────────────────────────

    def _find_product_with_pagination(self, page, data: dict):
        """
        Busca .product_item con paginación. Primero por pid (XHR), luego por nombre.
        """
        for page_num in range(1, MAX_PAGES + 1):
            card = self._find_product_card(page, data)
            if card is not None:
                return card

            next_btn = page.locator('.el-pagination .btn-next').first
            if not next_btn.is_visible() or next_btn.get_attribute('disabled') is not None:
                break

            # Capturar pids de la nueva página mientras carga
            pids_buf = []

            def _on_response(resp):
                self._try_capture_pids(resp, pids_buf)

            page.on('response', _on_response)
            next_btn.click()
            try:
                page.wait_for_selector('.product_item', state='attached', timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1200)
            page.remove_listener('response', _on_response)
            if pids_buf:
                self._page_product_pids = list(pids_buf)

            self.stdout.write(f'  Pagina {page_num + 1} — {len(self._page_product_pids)} pids')

        # No encontrado — diagnóstico
        try:
            pid  = self._extract_pid(data.get('supplier_url', ''))
            name = (data.get('name') or '')[:50]
            self.stdout.write(f'  [no encontrado] pid="{pid}" nombre="{name}"')
            # Mostrar pids capturados para detectar diferencias de formato
            captured = getattr(self, '_page_product_pids', [])
            if captured:
                self.stdout.write(f'  [pids capturados ({len(captured)})]: {captured[:6]} ...')
                # Búsqueda case-insensitive por si el formato difiere
                pid_lower = pid.lower()
                ci_match = next((i for i, p in enumerate(captured) if p.lower() == pid_lower), -1)
                if ci_match >= 0:
                    self.stdout.write(f'  [!] pid encontrado en capturados (case-insensitive) en índice {ci_match}')
            names = page.evaluate("""
                () => Array.from(document.querySelectorAll('.pro_name'))
                      .slice(0, 8).map(el => el.innerText.trim())
            """)
            for n in names:
                try:
                    self.stdout.write(f'    - {n[:60]}')
                except UnicodeEncodeError:
                    self.stdout.write(f'    - (nombre con chars especiales)')
        except Exception:
            pass

        return None

    @staticmethod
    def _names_match(our_name: str, card_name: str) -> bool:
        """True si los nombres son suficientemente similares (tolerante a variaciones de modaverse)."""
        a = our_name.strip().lower()
        b = card_name.strip().lower()
        if not a or not b:
            return False
        return a == b or a in b or b in a

    def _card_name(self, card) -> str:
        """Devuelve el texto del .pro_name de una card, o '' si falla."""
        try:
            return card.locator('.pro_name').first.inner_text().strip()
        except Exception:
            return ''

    def _find_product_card(self, page, data: dict):
        """
        Busca .product_item verificando SIEMPRE pid + nombre para evitar agregar
        el producto equivocado.

        Prioridad:
        1. pid en XHR → índice → verificar nombre en esa card
        2. pid en outerHTML → verificar nombre
        3. Nombre exacto → verificar pid en outerHTML (si disponible)
        4. Nombre parcial (primera palabra significativa) → verificar pid
        5. Única card → solo si nombre coincide
        """
        pid  = self._extract_pid(data.get('supplier_url', ''))
        name = (data.get('name') or '').strip()

        # 1. Pid en XHR → nombre exacto de modaverse → búsqueda en DOM
        if pid and getattr(self, '_page_product_pids', []):
            try:
                idx = self._page_product_pids.index(pid)
                mv_name = getattr(self, '_page_pid_names', {}).get(pid, '')
                if mv_name:
                    # Buscar por nombre exacto de modaverse (más fiable que índice)
                    card_by_mv = page.locator(
                        f'.product_item:has(.pro_name:has-text("{mv_name}"))'
                    ).first
                    if card_by_mv.count() > 0:
                        cn = self._card_name(card_by_mv)
                        self.stdout.write(f'  [card] pid+mvNombre "{mv_name}" ✓')
                        return card_by_mv
                # Fallback: usar índice y verificar nombre
                card = page.locator('.product_item').nth(idx)
                if card.count() > 0:
                    cn = self._card_name(card)
                    if not name or self._names_match(name, cn) or (mv_name and self._names_match(mv_name, cn)):
                        self.stdout.write(f'  [card] pid@{idx} mv="{mv_name}" cn="{cn}" ✓')
                        return card
                    self.stdout.write(f'  [card] pid@{idx} cn="{cn}" ≠ name="{name}" mv="{mv_name}" — descartado')
            except ValueError:
                pass

        # 2. Pid en outerHTML → confirmar nombre
        if pid:
            idx = page.evaluate(f"""
                () => Array.from(document.querySelectorAll('.product_item'))
                      .findIndex(el => el.outerHTML.includes('{pid}'))
            """)
            if idx >= 0:
                card = page.locator('.product_item').nth(idx)
                cn = self._card_name(card)
                if not name or self._names_match(name, cn):
                    self.stdout.write(f'  [card] pid-html@{idx} "{cn}" ✓')
                    return card
                self.stdout.write(f'  [card] pid-html@{idx} nombre="{cn}" ≠ "{name}" — descartado')

        # 3. Nombre exacto → confirmar pid en outerHTML si tenemos uno
        if name:
            card = page.locator(f'.product_item:has(.pro_name:has-text("{name}"))').first
            if card.count() > 0:
                cn = self._card_name(card)
                if not pid or self._names_match(name, cn):
                    self.stdout.write(f'  [card] nombre-exacto "{cn}" ✓')
                    return card

        # 4. Primera palabra significativa del nombre (≥4 chars)
        words = [w for w in name.split() if len(w) >= 4]
        if words:
            short = words[0]
            card = page.locator(f'.product_item:has(.pro_name:has-text("{short}"))').first
            if card.count() > 0:
                cn = self._card_name(card)
                if self._names_match(name, cn):
                    self.stdout.write(f'  [card] nombre-parcial "{cn}" ✓')
                    return card
                self.stdout.write(f'  [card] nombre-parcial "{cn}" ≠ "{name}" — descartado')

        # 5. Única card SOLO si el nombre coincide (nunca asumir a ciegas)
        if page.locator('.product_item').count() == 1:
            card = page.locator('.product_item').first
            cn = self._card_name(card)
            if not name or self._names_match(name, cn):
                self.stdout.write(f'  [card] única-card "{cn}" ✓')
                return card
            self.stdout.write(f'  [card] única-card "{cn}" ≠ "{name}" — descartado (producto equivocado)')

        return None

    def _try_capture_pids(self, response, buf: list) -> None:
        """
        Extrae la lista de productIds de respuestas XHR de modaverse.
        Escribe en `buf` (lista mutable) para poder usarlo desde closures.
        """
        try:
            if response.status != 200:
                return
            url = response.url
            # Log ALL JSON responses para descubrir el endpoint correcto
            ct = response.headers.get('content-type', '')
            if 'json' not in ct:
                return
            try:
                body = response.json()
            except Exception:
                return
            # Intentar extraer lista de productos de cualquier estructura razonable
            lst = None
            data = body.get('data')
            if isinstance(data, dict):
                lst = data.get('list') or data.get('records') or data.get('rows')
            elif isinstance(data, list):
                lst = data
            if lst and isinstance(lst, list) and lst:
                first = lst[0]
                if isinstance(first, dict) and ('productId' in first or 'id' in first):
                    pids = [str(p.get('productId') or p.get('id', '')) for p in lst]
                    pids = [p for p in pids if p]
                    if pids:
                        self.stdout.write(f'  [xhr] {url[:80]} → {len(pids)} pids')
                        buf.clear()
                        buf.extend(pids)
                        # Capturar pid → nombre exacto de modaverse (misma respuesta XHR)
                        if not hasattr(self, '_page_pid_names'):
                            self._page_pid_names = {}
                        for item in lst:
                            p_id = str(item.get('productId') or item.get('id', ''))
                            if not p_id:
                                continue
                            mv_name = (
                                item.get('productName') or item.get('name') or
                                item.get('title') or item.get('nameEn') or ''
                            )
                            if mv_name:
                                self._page_pid_names[p_id] = str(mv_name).strip()
                        return
            # Sin lista útil — loguear URL y estructura para diagnóstico
            keys = list(body.keys())[:6] if isinstance(body, dict) else type(body).__name__
            self.stdout.write(f'  [xhr] {url[:80]} (sin lista) keys={keys}')
        except Exception:
            pass

    @staticmethod
    def _extract_pid(supplier_url: str) -> str:
        """Extrae el productId del supplier_url. Soporta #/proinfo/{pid} y ?pid={pid}."""
        import re
        if not supplier_url:
            return ''
        m = re.search(r'/proinfo/([A-Za-z0-9]+)', supplier_url)
        if m:
            return m.group(1)
        m = re.search(r'[?&]pid=([A-Za-z0-9]+)', supplier_url)
        return m.group(1) if m else ''

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
