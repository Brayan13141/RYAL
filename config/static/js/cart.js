/**
 * RYAL — Cart (session-based via Django + Bootstrap Offcanvas)
 */

// ─── Bootstrap Offcanvas helpers ────────────────────────────────────────────

function openCart() {
  window.cartOffcanvasInstance?.show();
  // fetchCartHTML es llamado por el evento show.bs.offcanvas
}

function closeCart() {
  window.cartOffcanvasInstance?.hide();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cartOffcanvas')
    ?.addEventListener('show.bs.offcanvas', fetchCartHTML);

  document.getElementById('ocCheckoutBtn')
    ?.addEventListener('click', e => {
      if (e.currentTarget.classList.contains('disabled')) {
        e.preventDefault();
        showToast('Completa el mínimo de piezas por categoría', 'error');
      }
    });
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCart(); });


// ─── Fetch carrito desde Django ─────────────────────────────────────────────

async function fetchCartHTML() {
  const loading = document.getElementById('cartLoading');
  const items   = document.getElementById('cartItems');
  const empty   = document.getElementById('cartEmpty');
  const foot    = document.getElementById('cartFoot');

  loading?.classList.remove('d-none');

  try {
    const res  = await fetch(URLS.cartGet, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    const data = await res.json();

    loading?.classList.add('d-none');

    if (data.items && data.items.length > 0) {
      items?.classList.remove('d-none');
      empty?.classList.add('d-none');
      foot?.classList.remove('d-none');
      renderCartItems(data.items, data.subtotal, data.shipping, data.total, data.category_warnings || []);
    } else {
      items?.classList.add('d-none');
      empty?.classList.remove('d-none');
      foot?.classList.add('d-none');
    }
  } catch {
    loading?.classList.add('d-none');
  }
}

function renderCategoryWarnings(warnings) {
  const el  = document.getElementById('ocCategoryWarnings');
  const btn = document.getElementById('ocCheckoutBtn');
  if (!el) return;

  if (!warnings || warnings.length === 0) {
    el.classList.add('d-none');
    el.innerHTML = '';
    btn?.classList.remove('disabled');
    btn?.removeAttribute('aria-disabled');
    return;
  }

  el.classList.remove('d-none');
  el.innerHTML = warnings.map(w => `
    <div style="background:rgba(220,53,69,.12);border:1px solid rgba(220,53,69,.3);padding:7px 10px;margin-bottom:4px;font-size:11px;font-family:var(--f-mono);color:#e05560;display:flex;gap:8px;align-items:flex-start;">
      <i class="bi bi-exclamation-triangle-fill" style="flex-shrink:0;margin-top:1px;"></i>
      <span><strong>${w.name}</strong>: ${w.current}/${w.min} pzs — faltan ${w.missing}</span>
    </div>
  `).join('');

  btn?.classList.add('disabled');
  btn?.setAttribute('aria-disabled', 'true');
}

function renderCartItems(items, subtotal, shipping, total, categoryWarnings = []) {
  const list = document.getElementById('cartItems');
  if (!list) return;
  list.innerHTML = items.map(item => `
    <li class="oc-item" data-key="${item.key}">
      <div class="oc-item-img">
        ${item.image
          ? `<img src="${item.image}" alt="${item.name}">`
          : '<div class="oc-item-placeholder"></div>'}
      </div>
      <div class="oc-item-info">
        <span class="oc-item-name">${item.name}</span>
        ${item.variant ? `<span class="oc-item-variant">${item.variant}</span>` : ''}
        ${item.discount > 0 ? `
        <div style="display:flex;align-items:center;gap:5px;margin-top:3px;">
          <span style="font-size:10px;color:#555;font-family:var(--f-mono);"><span style="text-decoration:line-through;">$${formatMXN(item.original_price)}</span> c/u</span>
          <span style="font-size:10px;color:var(--gold);font-family:var(--f-mono);">−$${formatMXN(item.discount)} dto.</span>
        </div>` : ''}
        <div class="oc-item-qty">
          <button onclick="updateCartItem('${item.key}', ${item.qty - item.qty_step})" class="qty-btn-sm">−</button>
          <input
            type="number" min="${item.qty_step}" step="${item.qty_step}" value="${item.qty}"
            class="qty-input-sm"
            onchange="updateCartItem('${item.key}', Math.max(${item.qty_step}, Math.round((parseInt(this.value)||${item.qty_step}) / ${item.qty_step}) * ${item.qty_step}))"
            onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"
          >
          <button onclick="updateCartItem('${item.key}', ${item.qty + item.qty_step})" class="qty-btn-sm">+</button>
        </div>
      </div>
      <div class="oc-item-right">
        <span class="oc-item-price">
          ${item.qty > 1 ? `<span style="display:block;font-size:10px;color:#666;font-family:var(--f-mono);text-align:right;">${item.qty} × $${formatMXN(item.price)}</span>` : ''}
          $${formatMXN(item.subtotal)}
        </span>
        <button onclick="removeCartItem('${item.key}')" class="oc-item-remove" aria-label="Eliminar">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round">
            <path d="M2 2l10 10M12 2L2 12"/>
          </svg>
        </button>
      </div>
    </li>
  `).join('');

  document.getElementById('ocSubtotal').textContent = `$${formatMXN(subtotal)} MXN`;
  document.getElementById('ocShipping').textContent = shipping === 0 ? 'Gratis' : `$${formatMXN(shipping)} MXN`;
  document.getElementById('ocTotal').textContent    = `$${formatMXN(total)} MXN`;
  document.getElementById('ocItemCount').textContent = `(${items.reduce((s, i) => s + i.qty, 0)})`;
  renderCategoryWarnings(categoryWarnings);
}


// ─── Acciones del carrito ───────────────────────────────────────────────────

async function addToCart(productId, variantId, qty = 1, imagePk = null, color = null) {
  try {
    const payload = { product_id: productId, variant_id: variantId, qty };
    if (imagePk) payload.image_pk = imagePk;
    if (color) payload.color = color;
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
      showToast(data.message || 'Producto agregado');
      openCart();
    } else {
      showToast(data.error || 'Error al agregar', 'error');
    }
  } catch {
    showToast('Error de conexión', 'error');
  }
}

async function removeCartItem(key) {
  try {
    const res = await fetch(URLS.cartRemove, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CSRF_TOKEN,
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({ key }),
    });
    const data = await res.json();
    if (data.ok) {
      updateBadge(data.cart_count);
      fetchCartHTML();
    }
  } catch {
    showToast('Error de conexión', 'error');
  }
}

async function updateCartItem(key, qty) {
  if (qty < 1) { removeCartItem(key); return; }
  try {
    const res = await fetch(URLS.cartUpdate, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CSRF_TOKEN,
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({ key, qty }),
    });
    const data = await res.json();
    if (data.ok) {
      updateBadge(data.cart_count);
      fetchCartHTML();
    }
  } catch {
    showToast('Error de conexión', 'error');
  }
}


// ─── Quick add desde tarjeta ────────────────────────────────────────────────

function cardQuickAdd(btn) {
  const productId    = btn.dataset.productId;
  const hasVariants  = btn.dataset.hasVariants  === 'true';
  const hasSizes     = btn.dataset.hasSizes     === 'true';
  const hasColorway  = btn.dataset.hasColorway  === 'true';
  const hasColors    = btn.dataset.hasColors    === 'true';
  const minQty       = parseInt(btn.dataset.minQty || '1', 10);
  if (hasVariants || hasSizes || hasColors || hasColorway || minQty > 1) {
    window.location.href = `/catalogo/${productId}/`;
    return;
  }
  addToCart(productId, null, 1);
}


// ─── Helpers ────────────────────────────────────────────────────────────────

function updateBadge(count) {
  const badge = document.getElementById('cartBadge');
  if (!badge) return;
  badge.textContent = count;
  badge.style.display = count > 0 ? 'flex' : 'none';
}

function formatMXN(n) {
  return Number(n).toLocaleString('es-MX', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}


// ─── Thumbstrip — cambio de imagen en card ──────────────────────────────────

function cardThumbSwap(thumb) {
  const card = thumb.closest('.card');
  const mainImg = card?.querySelector('.card-main-img');
  if (!mainImg) return;
  card.querySelectorAll('.card-thumb').forEach(t => t.classList.remove('card-thumb--active'));
  thumb.classList.add('card-thumb--active');
  mainImg.src = thumb.dataset.src;
}
