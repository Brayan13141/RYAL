/**
 * RYAL — Detalle de producto
 * Swap de imágenes, selector de variantes, control de cantidad.
 */

// ─── Imágenes ────────────────────────────────────────────────────────────────

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
  document.querySelectorAll('.pd-thumb').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
}


// ─── Cantidad ────────────────────────────────────────────────────────────────

const _minQty       = (typeof MIN_QTY !== 'undefined' && MIN_QTY > 1) ? MIN_QTY : 1;
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

  document.querySelectorAll('.tier-row').forEach(row => {
    const isActive = tier && parseInt(row.dataset.min) === tier.min_qty;
    row.classList.toggle('tier-row--active', isActive);
    const priceEl = row.querySelector('.tier-price span');
    if (priceEl) {
      const rowPrice = Math.round(parseFloat(row.dataset.price));
      priceEl.textContent = rowPrice.toLocaleString('es-MX');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('qtyVal');
  if (el) el.textContent = qty;
  _applyTier(qty);
});

function changeQty(delta) {
  qty = Math.max(_minQty, qty + delta);
  document.getElementById('qtyVal').textContent = qty;
  _applyTier(qty);
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

  const btn = this;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Agregando...';

  await addToCart(productId, variantId, qty);

  btn.disabled = false;
  btn.innerHTML = original;
});


// ─── Helpers ────────────────────────────────────────────────────────────────

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}
