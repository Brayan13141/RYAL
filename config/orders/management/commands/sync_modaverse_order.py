"""
Arma el carrito en modaverse.vip para un pedido de ryalsneackers.

Flujo:
1. Bootstrapping: navega a homepage → primera cat → primer sub → llega a /product/ con bar_box.
2. Por cada ítem: usa el menú bar_box (CLICK, no hover) para navegar a la subcategoría.
   - Trigger click → .mu_i padre → .option subcategoría → /product/{id}
3. Busca la tarjeta del producto por SKU o nombre (has-text, no text-is exacto).
4. btn_1 → click directo; btn_2 → selecciona talla en el dialog.
5. Abre el carrito, ajusta cantidades, toma screenshot, genera código.
6. Guarda todos los resultados DESPUÉS de cerrar Playwright (no ORM dentro del bloque).

Sin login — modaverse es acceso público.
"""
from django.core.files.base import ContentFile
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

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError(
                'playwright no está instalado.\n'
                'Ejecuta: pip install playwright && playwright install chromium'
            )

        # ── PLAYWRIGHT (sin ORM) ──────────────────────────────────────────────
        results          = {}   # {item_id: {'status': str, 'notes': str}}
        screenshot_bytes = None
        modaverse_code   = ''

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx     = browser.new_context(viewport={'width': 1280, 'height': 900})
            page    = ctx.new_page()

            self.stdout.write(f'Abriendo {MODAVERSE_BASE}...')
            page.goto(MODAVERSE_BASE, timeout=TIMEOUT)
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(1500)

            for data in items_data:
                result = self._add_item(page, data)
                results[data['id']] = result

            # Abrir carrito
            self.stdout.write('\nAbriendo carrito...')
            if not self._open_cart(page):
                self.stdout.write(self.style.WARNING('No se pudo abrir el carrito'))
            page.wait_for_timeout(1500)

            # Ajustar cantidades
            added_data = [d for d in items_data if results.get(d['id'], {}).get('status') == 'added']
            self._adjust_quantities(page, added_data)
            page.wait_for_timeout(800)

            # Screenshot
            try:
                screenshot_bytes = page.screenshot(full_page=False)
                self.stdout.write('Screenshot capturado ✓')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Screenshot fallido: {exc}'))

            # Generar código
            modaverse_code = self._generate_code(page)
            if modaverse_code:
                self.stdout.write(self.style.SUCCESS(f'Código generado: {modaverse_code}'))
            else:
                self.stdout.write(self.style.WARNING('No se pudo capturar el código — el carrito puede estar vacío'))

            browser.close()

        # ── GUARDAR RESULTADOS (Django ORM — fuera de Playwright) ────────────
        for item in pending_items:
            r = results.get(item.id)
            if r:
                item.status = r['status']
                item.notes  = r['notes']
                item.save(update_fields=['status', 'notes'])

        if screenshot_bytes:
            supplier_order.screenshot.save(
                f'order_{order_id}_cart.png',
                ContentFile(screenshot_bytes),
                save=False,
            )

        if modaverse_code:
            supplier_order.modaverse_code = modaverse_code

        statuses = {results[i.id]['status'] for i in pending_items if i.id in results}
        if statuses <= {'added', 'no_url'}:
            supplier_order.status = 'done'
        elif 'added' in statuses:
            supplier_order.status = 'partial'
        else:
            supplier_order.status = 'failed'

        supplier_order.save(update_fields=['status', 'screenshot', 'modaverse_code', 'updated_at'])

        items_final = list(supplier_order.items.select_related('order_item').all())
        self._print_summary(items_final, modaverse_code)

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _add_item(self, page, data: dict) -> dict:
        sku = data['sku']
        self.stdout.write(f'\n[{sku}] Buscando en categoría "{data["category"]}"...')

        navigated = self._navigate_to_category(page, data)
        if not navigated:
            return {'status': 'variant_not_found',
                    'notes': f'No se pudo navegar a la categoría "{data["category"]}"'}

        card = self._find_product_with_pagination(page, data)
        if card is None:
            return {'status': 'variant_not_found',
                    'notes': f'Producto no encontrado en la subcategoría (revisadas hasta {MAX_PAGES} páginas)'}

        btn_simple = card.locator('button.btn_1').first
        btn_specs  = card.locator('button.btn_2').first

        if btn_simple.is_visible():
            btn_simple.click()
            page.wait_for_timeout(CART_WAIT)
            self.stdout.write(self.style.SUCCESS('  ✓ Agregado (sin especificaciones)'))
            return {'status': 'added', 'notes': ''}

        if btn_specs.is_visible():
            return self._add_with_specs(page, card, data)

        return {'status': 'variant_not_found', 'notes': 'Sin botón de compra visible en la tarjeta'}

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
            # Bootstrap: llegar a una página /product/ que tiene el bar_box
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
        """
        try:
            if MODAVERSE_BASE not in page.url or len(page.url) > len(MODAVERSE_BASE) + 3:
                page.goto(MODAVERSE_BASE, timeout=TIMEOUT)
                page.wait_for_load_state('networkidle', timeout=TIMEOUT)
                page.wait_for_timeout(1500)

            # Click primera categoría → /zifenlei/
            first_cat = page.locator('.product_item').first
            if not first_cat.is_visible():
                return False
            first_cat.click()
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(1200)

            # Click primera subcategoría → /product/
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

        # Clic en la categoría padre para cargar sus subcategorías en el lado derecho
        if parent_cat:
            mu_i = page.locator('.info_box .mu_i').filter(has_text=parent_cat).first
            if mu_i.is_visible():
                mu_i.click()
                page.wait_for_timeout(500)

        # Clic en la subcategoría
        option = page.locator('.info_box .option').filter(has_text=category).first
        if not option.is_visible():
            self.stdout.write(self.style.WARNING(f'  Subcategoría "{category}" no encontrada en el menú'))
            return False

        option.click()
        page.wait_for_load_state('networkidle', timeout=TIMEOUT)
        page.wait_for_timeout(1200)
        self.stdout.write(f'  Navegado a "{category}"')
        return True

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
            self.stdout.write(f'  Página {page_num + 1}...')

        return None

    def _find_product_card(self, page, data: dict):
        """
        Busca .product_item por SKU o nombre usando has-text (substring, case-insensitive).
        """
        for search in (data['sku'], data['name']):
            if not search:
                continue
            card = page.locator(f'.product_item:has(.pro_name:has-text("{search}"))').first
            if card.is_visible():
                return card

        all_cards = page.locator('.product_item').all()
        if len(all_cards) == 1:
            return page.locator('.product_item').first

        return None

    def _add_with_specs(self, page, card, data: dict) -> dict:
        """
        Abre el dialog de especificaciones, selecciona la talla y agrega al carrito.

        Estructura del dialog:
          .el-dialog.dialog_box
            .zhi_box > .zhi_i > span   ← opciones de talla
            .btn_box > button.btn      ← "Agregar"
          .el-dialog__headerbtn        ← cerrar
        """
        variant    = data['variant'].strip()
        size_value = variant.removeprefix('Talla').strip() if variant.startswith('Talla') else variant

        card.locator('button.btn_2').first.click()
        page.wait_for_timeout(900)

        dialog = page.locator('.el-dialog.dialog_box')
        if not dialog.is_visible():
            self.stdout.write(self.style.WARNING('  Diálogo no abrió'))
            return {'status': 'variant_not_found', 'notes': 'Diálogo de especificaciones no apareció'}

        options   = dialog.locator('.zhi_box .zhi_i')
        available = []
        matched   = None

        for opt in options.all():
            txt = opt.locator('span').first.inner_text().strip()
            available.append(txt)
            if size_value and txt.lower() == size_value.lower():
                matched = opt

        # Fallback: match parcial
        if not matched and size_value:
            for opt in options.all():
                txt = opt.locator('span').first.inner_text().strip()
                if size_value.lower() in txt.lower() or txt.lower() in size_value.lower():
                    matched = opt
                    break

        # Sin variante especificada → primera disponible
        if not matched and not size_value:
            all_opts = options.all()
            if all_opts:
                matched = all_opts[0]
                label = available[0] if available else '?'
                self.stdout.write(self.style.WARNING(f'  Sin variante — seleccionando primera: "{label}"'))

        if not matched:
            try:
                dialog.locator('.el-dialog__headerbtn').click()
                page.wait_for_timeout(400)
            except Exception:
                pass
            notes = f'Disponibles: {", ".join(available)}' if available else 'Sin opciones en el diálogo'
            self.stdout.write(self.style.WARNING(f'  Talla "{size_value}" no encontrada — {notes}'))
            return {'status': 'variant_not_found', 'notes': notes}

        matched.click()
        page.wait_for_timeout(400)

        dialog.locator('.btn_box button.btn').first.click()
        page.wait_for_timeout(CART_WAIT)

        # Cerrar dialog (puede que ya se cerró solo)
        try:
            if dialog.is_visible():
                dialog.locator('.el-dialog__headerbtn').click()
                page.wait_for_timeout(400)
        except Exception:
            pass

        label = size_value if size_value else (available[0] if available else '?')
        self.stdout.write(self.style.SUCCESS(f'  ✓ Variante "{label}" agregada'))
        return {'status': 'added', 'notes': ''}

    def _open_cart(self, page) -> bool:
        """Clic en el ícono del carrito (.car_W) para abrir el panel."""
        try:
            cart_icon = page.locator('.car_W').first
            if cart_icon.is_visible():
                cart_icon.click()
                page.wait_for_timeout(1200)
                return True
            page.goto(f'{MODAVERSE_BASE}/#/cart', timeout=TIMEOUT)
            page.wait_for_load_state('networkidle', timeout=TIMEOUT)
            page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def _adjust_quantities(self, page, added_data: list):
        """
        Ajusta cantidades en el carrito para ítems con qty > 1.
        Busca por SKU o nombre con has-text.
        """
        for data in added_data:
            qty = data['quantity']
            if qty <= 1:
                continue
            for search in (data['sku'], data['name']):
                if not search:
                    continue
                card = page.locator(
                    f'.body_box .product_item:has(.pro_name:has-text("{search}"))'
                ).first
                if card.is_visible():
                    try:
                        inp = card.locator('.el-input__inner').first
                        inp.fill(str(qty))
                        inp.press('Enter')
                        page.wait_for_timeout(400)
                        self.stdout.write(f'  [{data["sku"]}] Cantidad → {qty}')
                    except Exception as exc:
                        self.stdout.write(self.style.WARNING(
                            f'  [{data["sku"]}] Error ajustando cantidad: {exc}'
                        ))
                    break

    def _generate_code(self, page) -> str:
        """
        Clic en 'Generar código de pedido' (solo si habilitado) y captura el código.
        El código es una cadena corta alfanumérica (ej: KD260524) que aparece sola en una línea.
        """
        import re
        try:
            gen_btn = page.locator('button:has-text("Generar código de pedido")').first
            if not gen_btn.is_visible():
                return ''

            cls = gen_btn.get_attribute('class') or ''
            if 'is-disabled' in cls:
                self.stdout.write(self.style.WARNING('  Botón deshabilitado — el carrito parece vacío'))
                return ''

            gen_btn.click()
            page.wait_for_timeout(2500)

            for selector in (
                '[class*="order_code"]',
                '[class*="orderCode"]',
                '.el-message-box__message',
                '.el-dialog__body',
                '[class*="success"] span',
                '[class*="result"] span',
            ):
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if not txt:
                        continue
                    # Extraer solo el código (línea corta alfanumérica, ej: KD260524)
                    for line in txt.splitlines():
                        line = line.strip()
                        if re.match(r'^[A-Z]{2}\d{6,}$', line) or re.match(r'^[A-Z0-9]{6,12}$', line):
                            return line
                    # Fallback: primera línea que parezca un código
                    match = re.search(r'\b([A-Z]{1,3}\d{4,})\b', txt)
                    if match:
                        return match.group(1)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'  Error capturando código: {exc}'))
        return ''

    def _print_summary(self, items, modaverse_code):
        self.stdout.write('\n─── Resumen ───────────────────────────────────────')
        for item in items:
            icon = {'added': '✓', 'variant_not_found': '⚠', 'no_url': '○', 'pending': '?'}.get(item.status, '?')
            self.stdout.write(f'  {icon} {item.order_item.sku_snapshot} — {item.get_status_display()}')
            if item.notes:
                self.stdout.write(f'      {item.notes[:120]}')
        if modaverse_code:
            self.stdout.write(f'\n  Código modaverse: {modaverse_code}')
        self.stdout.write('')
