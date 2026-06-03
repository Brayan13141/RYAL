/**
 * RYAL — Detalle de producto
 * Swap de imágenes, selector de variantes, control de cantidad.
 */

// ─── Imágenes ────────────────────────────────────────────────────────────────

// Pk de la imagen actualmente visible — se actualiza con cada swapImage/click en thumbnail
let _selectedImagePk = (function () {
  const cover = document.querySelector('.pd-thumb.active');
  return cover && cover.dataset.imgPk ? parseInt(cover.dataset.imgPk) : null;
})();

// ─── Color seleccionable (variant_colors) ───────────────────────────────────
let SELECTED_COLOR = (typeof VARIANT_COLORS !== 'undefined' && VARIANT_COLORS && VARIANT_COLORS.length === 1)
  ? VARIANT_COLORS[0] : null;

function selectColor(color, btn) {
  SELECTED_COLOR = color;
  document.querySelectorAll('.color-chip').forEach(c => c.classList.remove('selected'));
  if (btn) btn.classList.add('selected');
  if (typeof _updateSizeTotal === 'function') _updateSizeTotal();
}

function _colorRequiredAndMissing() {
  return (typeof VARIANT_COLORS !== 'undefined' && VARIANT_COLORS &&
          VARIANT_COLORS.length > 0 && !SELECTED_COLOR);
}

function swapImage(url, btn) {
  const mainImg = document.getElementById('mainImg');
  if (mainImg && mainImg.tagName === 'IMG') {
    mainImg.style.transition = 'opacity .18s';
    mainImg.style.opacity = '0';
    setTimeout(() => {
      mainImg.src = url;
      mainImg.style.opacity = '1';
    }, 160);
  }

  const imgPk = btn.dataset && btn.dataset.imgPk;

  // Sync pd-thumb active state
  document.querySelectorAll('.pd-thumb').forEach(t => t.classList.remove('active'));
  if (imgPk) {
    const pdt = document.querySelector(`.pd-thumb[data-img-pk="${imgPk}"]`);
    if (pdt) pdt.classList.add('active');
    else btn.classList.add('active');
  } else {
    btn.classList.add('active');
  }

  // Sync color-row-thumb active state (modo color_variant_mode)
  document.querySelectorAll('.color-row-thumb').forEach(t => t.classList.remove('active'));
  if (imgPk) {
    const crt = document.querySelector(`.color-row-thumb[data-img-pk="${imgPk}"]`);
    if (crt) crt.classList.add('active');
  }

  _selectedImagePk = imgPk ? parseInt(imgPk) : null;

  // Actualizar el indicador de color en el grid de tallas (modo tallas+colorway)
  const pickerThumb = document.getElementById('colorPickerThumb');
  const pickerName  = document.getElementById('colorPickerName');
  if (pickerThumb) pickerThumb.src = url;
  if (pickerName) {
    const label = btn.dataset.colorLabel || '';
    pickerName.textContent = label || btn.getAttribute('aria-label') || '—';
  }
}


// ─── Cantidad ────────────────────────────────────────────────────────────────

const _minQty       = (typeof MIN_QTY !== 'undefined' && MIN_QTY > 1) ? MIN_QTY : 1;
const _step         = (typeof QTY_STEP !== 'undefined' && QTY_STEP > 1) ? QTY_STEP : 1;
const _basePrice    = (typeof PRODUCT_BASE_PRICE !== 'undefined') ? PRODUCT_BASE_PRICE : 0;
const _tiers        = (typeof PRODUCT_TIERS !== 'undefined') ? PRODUCT_TIERS : [];
let qty = _minQty;

function _activeTier(currentQty) {
  const applicable = _tiers.filter(t => t.min_qty <= currentQty);
  return applicable.length ? applicable[applicable.length - 1] : null;
}

function _applyTier(currentQty) {
  const tier  = _activeTier(currentQty);
  const price = tier ? Math.max(0, Math.round(_basePrice - tier.discount)) : Math.round(_basePrice);

  const ctaEl = document.getElementById('ctaPrice');
  if (ctaEl) ctaEl.textContent = price.toLocaleString('es-MX');

  // Tier table rows (legacy fallback)
  document.querySelectorAll('.tier-row').forEach(row => {
    const isActive = tier && parseInt(row.dataset.min) === tier.min_qty;
    row.classList.toggle('tier-row--active', isActive);
    const priceEl = row.querySelector('.tier-price span');
    if (priceEl) {
      const rowPrice = Math.round(parseFloat(row.dataset.price));
      priceEl.textContent = rowPrice.toLocaleString('es-MX');
    }
  });

  // Tier buttons
  document.querySelectorAll('.tier-btn').forEach(btn => {
    const isActive = tier && parseInt(btn.dataset.min) === tier.min_qty;
    btn.classList.toggle('tier-btn--active', isActive);
  });
}

function selectTier(minQty, btn) {
  setQty(minQty);
}

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('qtyVal');
  if (el) el.textContent = qty;
  _applyTier(qty);
});

function changeQty(delta) {
  qty = Math.max(_minQty, qty + delta * _step);
  document.getElementById('qtyVal').textContent = qty;
  _applyTier(qty);
}

function setQty(n) {
  // Snap to nearest valid multiple of _step that is >= _minQty
  const snapped = _step > 1 ? Math.ceil(n / _step) * _step : n;
  qty = Math.max(_minQty, snapped);
  document.getElementById('qtyVal').textContent = qty;
  _applyTier(qty);
  document.getElementById('qtyVal')?.closest('.pd-qty')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}


// ─── Variantes ───────────────────────────────────────────────────────────────

let selectedVariantId = null;

if (typeof PRODUCT_VARIANTS !== 'undefined' && PRODUCT_VARIANTS.length > 0) {
  renderVariants();
}

function renderVariants() {
  const container = document.getElementById('variantControls');
  if (!container) return;

  // Agrupar atributos únicos
  const attrMap = {};
  PRODUCT_VARIANTS.forEach(v => {
    if (!v.is_active) return;
    Object.entries(v.attributes).forEach(([key, val]) => {
      if (!attrMap[key]) attrMap[key] = new Set();
      attrMap[key].add(val);
    });
  });

  container.innerHTML = Object.entries(attrMap).map(([key, vals]) => `
    <div class="variant-group">
      <span class="variant-label">${capitalize(key)}</span>
      <div class="variant-chips" data-attr="${key}">
        ${[...vals].map(val => `
          <button class="chip" data-attr="${key}" data-val="${val}"
                  onclick="selectVariantAttr('${key}', '${val}', this)">
            ${val}
          </button>
        `).join('')}
      </div>
    </div>
  `).join('');
}

const selectedAttrs = {};

function selectVariantAttr(key, val, btn) {
  // Deseleccionar otros en el mismo grupo
  document.querySelectorAll(`.variant-chips[data-attr="${key}"] .chip`).forEach(c => c.classList.remove('selected'));
  btn.classList.add('selected');
  selectedAttrs[key] = val;
  resolveVariant();
}

function resolveVariant() {
  const match = PRODUCT_VARIANTS.find(v => {
    if (!v.is_active) return false;
    return Object.entries(v.attributes).every(([k, val]) => selectedAttrs[k] === val);
  });

  selectedVariantId = match ? match.pk : null;

  if (match) {
    const price = parseFloat(match.final_price);
    document.getElementById('ctaPrice').textContent = Math.round(price).toLocaleString('es-MX');
    const btn = document.getElementById('addToCartBtn');
    if (btn) btn.dataset.variantId = match.pk;
  }
}


// ─── Agregar al carrito ──────────────────────────────────────────────────────

document.getElementById('addToCartBtn')?.addEventListener('click', async function () {
  const productId = this.dataset.productId;
  const variantId = selectedVariantId || null;

  if (PRODUCT_VARIANTS.length > 0 && !selectedVariantId) {
    showToast('Selecciona una variante primero', 'error');
    return;
  }

  if (_colorRequiredAndMissing()) {
    showToast('Selecciona un color primero', 'error');
    return;
  }
  const colorForCart = (typeof VARIANT_COLORS !== 'undefined' && VARIANT_COLORS && VARIANT_COLORS.length > 0)
    ? SELECTED_COLOR : null;

  const btn = this;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Agregando...';

  // Solo enviar image_pk si el colorway está activo — evita fragmentar el mismo producto
  const imagePkForCart = (typeof HAS_COLORWAY !== 'undefined' && HAS_COLORWAY && _selectedImagePk)
    ? _selectedImagePk
    : null;
  await addToCart(productId, variantId, qty, imagePkForCart, colorForCart);

  btn.disabled = false;
  btn.innerHTML = original;
});


// ─── Helpers ────────────────────────────────────────────────────────────────

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}


// ─── Selector de colores / variantes (calzado) ──────────────────────────────

if (typeof IMAGES_DATA !== 'undefined' && IMAGES_DATA && IMAGES_DATA.length > 0) {

  // { imgPk: { qty, colorNum } }
  const _colorItems = {};
  IMAGES_DATA.forEach((img, idx) => {
    _colorItems[img.pk] = { qty: 0, colorNum: idx + 1 };
  });

  // Mínimo por variante: reutiliza QTY_STEP (min_qty_per_item de la categoría)
  const _colorMinQty = (typeof QTY_STEP !== 'undefined' && QTY_STEP > 1) ? QTY_STEP : 1;

  function _colorTotal() {
    return Object.values(_colorItems).reduce((sum, v) => sum + v.qty, 0);
  }

  function _updateColorUI() {
    const total = _colorTotal();
    const el = document.getElementById('colorTotalVal');
    if (el) el.textContent = total;
    const btn = document.getElementById('addColorsBtn');
    if (btn) btn.disabled = total === 0;
  }

  function changeColorQty(imgPk, delta, colorNum) {
    const item = _colorItems[imgPk];
    if (!item) return;

    if (delta > 0 && item.qty === 0) {
      // Primer incremento: saltar directo al mínimo
      item.qty = _colorMinQty;
    } else {
      const next = item.qty + delta;
      // Si el resultado cae entre 1 y el mínimo exclusivo, ir directo a 0
      item.qty = (next > 0 && next < _colorMinQty) ? 0 : Math.max(0, next);
    }

    const el = document.getElementById('cqty-' + imgPk);
    if (el) el.textContent = item.qty;

    // Resaltar la fila si tiene qty > 0
    const row = el?.closest('.color-row-item');
    if (row) row.classList.toggle('has-qty', item.qty > 0);

    _updateColorUI();
  }

  document.addEventListener('DOMContentLoaded', () => {
    _updateColorUI();

    const btn = document.getElementById('addColorsBtn');
    if (!btn) return;

    btn.addEventListener('click', async function () {
      const entries = Object.entries(_colorItems).filter(([, v]) => v.qty > 0);
      if (!entries.length) return;

      const original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Agregando...';

      for (const [imgPkStr, { qty, colorNum }] of entries) {
        const imgPk = parseInt(imgPkStr);
        const res = await fetch(URLS.cartAdd, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken':   CSRF_TOKEN,
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: JSON.stringify({
            product_id: PRODUCT_ID,
            image_pk:   imgPk,
            color_num:  colorNum,
            qty,
          }),
        });
        const data = await res.json();
        if (data.ok) {
          updateBadge(data.cart_count);
        } else {
          showToast(data.error || `Error al agregar Color ${colorNum}`, 'error');
          btn.disabled = false;
          btn.innerHTML = original;
          return;
        }
      }

      showToast(`${btn.dataset.productName || 'Producto'} agregado al carrito`);
      openCart();

      // Resetear todas las cantidades
      Object.keys(_colorItems).forEach(pk => {
        _colorItems[pk].qty = 0;
        const el = document.getElementById('cqty-' + pk);
        if (el) el.textContent = '0';
        const row = el?.closest('.color-row-item');
        if (row) row.classList.remove('has-qty');
      });
      _updateColorUI();
      btn.innerHTML = original;
    });
  });
}


// ─── Grid de tallas ──────────────────────────────────────────────────────────

if (typeof SIZE_NAMES !== 'undefined' && SIZE_NAMES && SIZE_NAMES.length > 0) {
  const _sizeQtys = {};
  SIZE_NAMES.forEach(s => { _sizeQtys[s] = 0; });

  function _sizeTotalQty() {
    return Object.values(_sizeQtys).reduce((a, b) => a + b, 0);
  }

  function _updateSizeTotal() {
    const total = _sizeTotalQty();
    const totalEl = document.getElementById('sizeTotalVal');
    if (totalEl) totalEl.textContent = total;
    const btn = document.getElementById('addSizesBtn');
    if (!btn) return;
    const minOk = SIZE_MIN_QTY <= 1 ? total > 0 : total >= SIZE_MIN_QTY;
    btn.disabled = !minOk || _colorRequiredAndMissing();
  }

  function changeSizeQty(sizeName, sizeSlug, delta) {
    _sizeQtys[sizeName] = Math.max(0, (_sizeQtys[sizeName] || 0) + delta);
    const el = document.getElementById('sqty-' + sizeSlug);
    if (el) el.textContent = _sizeQtys[sizeName];
    _updateSizeTotal();
  }

  document.addEventListener('DOMContentLoaded', () => {
    _updateSizeTotal();

    const btn = document.getElementById('addSizesBtn');
    if (!btn) return;

    btn.addEventListener('click', async function () {
      const entries = Object.entries(_sizeQtys).filter(([, q]) => q > 0);
      if (!entries.length) return;

      const minOk = SIZE_MIN_QTY <= 1 ? true : _sizeTotalQty() >= SIZE_MIN_QTY;
      if (!minOk) {
        showToast(`Mínimo ${SIZE_MIN_QTY} piezas en total por modelo`, 'error');
        return;
      }

      const original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Agregando...';

      for (const [sizeName, qty] of entries) {
        // Cuando hay colorway activo, incluir la imagen seleccionada para diferenciar por color
        const payload = { product_id: PRODUCT_ID, size_name: sizeName, qty };
        if (typeof HAS_COLORWAY !== 'undefined' && HAS_COLORWAY && _selectedImagePk) {
          payload.image_pk = _selectedImagePk;
        }
        if (typeof VARIANT_COLORS !== 'undefined' && VARIANT_COLORS && VARIANT_COLORS.length > 0) {
          if (!SELECTED_COLOR) { showToast('Selecciona un color primero', 'error'); btn.disabled = false; btn.innerHTML = original; return; }
          payload.color = SELECTED_COLOR;
        }
        const res = await fetch(URLS.cartAdd, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': CSRF_TOKEN,
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.ok) {
          updateBadge(data.cart_count);
        } else {
          showToast(data.error || 'Error al agregar talla ' + sizeName, 'error');
          btn.disabled = false;
          btn.innerHTML = original;
          return;
        }
      }

      showToast(`${btn.dataset.productName || 'Producto'} agregado al carrito`);
      openCart();
      // Resetear cantidades
      SIZE_NAMES.forEach(s => { _sizeQtys[s] = 0; });
      document.querySelectorAll('[id^="sqty-"]').forEach(el => { el.textContent = '0'; });
      _updateSizeTotal();
      btn.innerHTML = original;
    });
  });
}
