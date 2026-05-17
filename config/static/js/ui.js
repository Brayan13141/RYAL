/**
 * RYAL — UI interactions
 * Toast, buscador, newsletter, animaciones menores.
 */

// ─── Toast ───────────────────────────────────────────────────────────────────

let toastTimeout = null;

function showToast(msg, type = 'success') {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = msg;
  toast.className   = `toast toast--${type} show`;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { toast.classList.remove('show'); }, 3000);
}

// Mostrar mensajes Django como toast al cargar la página
document.querySelectorAll('.django-message[data-autohide]').forEach(el => {
  const type = el.classList.contains('error') ? 'error' : 'success';
  showToast(el.textContent.trim(), type);
  el.remove();
});


// ─── Buscador ────────────────────────────────────────────────────────────────

const searchToggle = document.getElementById('searchToggle');
const searchBar    = document.getElementById('searchBar');

searchToggle?.addEventListener('click', () => {
  const open = searchBar.style.display === 'block';
  searchBar.style.display = open ? 'none' : 'block';
  if (!open) searchBar.querySelector('input')?.focus();
});


// ─── Newsletter (stub) ───────────────────────────────────────────────────────

document.getElementById('newsletterForm')?.addEventListener('submit', e => {
  e.preventDefault();
  showToast('¡Te avisamos cuando haya novedades!');
  e.target.reset();
});


// ─── Animación de entrada de secciones ──────────────────────────────────────

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.section, .card, .cat-card').forEach(el => {
    el.classList.add('fade-in');
    observer.observe(el);
  });
}
