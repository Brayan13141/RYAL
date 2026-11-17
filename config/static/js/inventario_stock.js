/* Filtros de la pantalla Stock: modelo, talla y color, sin recargar.
 *
 * Cada filtro reduce a los otros: los chips de talla muestran solo las tallas que hay en
 * los modelos y colores elegidos (con su número de pares), y los de color, solo los
 * colores de esos modelos y tallas. Lo marcado que deja de existir se desmarca solo.
 *
 * La lógica es pura (cuenta / podar / pasa) y se prueba con node:
 *   node --test inventario/tests/js/
 */
(function () {
  'use strict';

  var DIMS = ['modelo', 'talla', 'color'];

  function hay(o) { return Object.keys(o).length > 0; }

  // ¿La entrada pasa los filtros? `excepto` ignora una dimensión (para contar sus chips).
  function pasa(e, sel, excepto) {
    for (var i = 0; i < DIMS.length; i++) {
      var d = DIMS[i];
      if (d !== excepto && hay(sel[d]) && !sel[d][e[d]]) return false;
    }
    return true;
  }

  // Pares por valor de `dim`, aplicando los otros dos filtros.
  function cuenta(entradas, sel, dim) {
    var n = {};
    entradas.forEach(function (e) {
      if (pasa(e, sel, dim)) n[e[dim]] = (n[e[dim]] || 0) + e.n;
    });
    return n;
  }

  // Copia de `sel` sin las tallas ni colores marcados que ya no tienen pares.
  // Primero las tallas y luego los colores: soltar una talla solo agrega pares a los colores.
  function podar(entradas, sel) {
    var nuevo = { modelo: Object.assign({}, sel.modelo), talla: {}, color: Object.assign({}, sel.color) };
    var nt = cuenta(entradas, nuevo, 'talla');
    Object.keys(sel.talla).forEach(function (t) { if (nt[t]) nuevo.talla[t] = true; });
    var nc = cuenta(entradas, nuevo, 'color');
    nuevo.color = {};
    Object.keys(sel.color).forEach(function (c) { if (nc[c]) nuevo.color[c] = true; });
    return nuevo;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { pasa: pasa, cuenta: cuenta, podar: podar };
    return;
  }

  function pares(n) { return n + (n === 1 ? ' par' : ' pares'); }

  function iniciar() {
    var cards = document.querySelectorAll('.js-card');
    if (!cards.length) return;
    var chips = {
      modelo: document.querySelectorAll('.js-filtro'),
      talla: document.querySelectorAll('.js-talla'),
      color: document.querySelectorAll('.js-color'),
    };
    var todos = {
      modelo: document.getElementById('filtro-todos'),
      talla: document.getElementById('talla-todas'),
      color: document.getElementById('color-todos'),
    };
    var vacio = document.getElementById('cards-vacio');
    var sel = { modelo: {}, talla: {}, color: {} };  // vacío = sin filtro

    var entradas = [];
    cards.forEach(function (card) {
      card.querySelectorAll('.js-fila').forEach(function (fila) {
        fila.querySelectorAll('td[data-n]').forEach(function (td) {
          var n = +td.dataset.n;
          if (n) entradas.push({ modelo: card.dataset.modelo, color: fila.dataset.color,
                                 talla: td.dataset.talla, n: n });
        });
      });
    });

    function valor(chip, dim) {
      return dim === 'modelo' ? chip.dataset.modelo
        : dim === 'talla' ? chip.dataset.talla : chip.dataset.color;
    }

    function marcar(chip, on) {
      chip.classList.toggle('is-on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    function pintarChips(dim) {
      var n = cuenta(entradas, sel, dim);
      chips[dim].forEach(function (c) {
        var v = valor(c, dim), k = n[v] || 0;
        marcar(c, !!sel[dim][v]);
        c.hidden = !k;
        var span = c.querySelector('.inv-filtro-n');
        if (span) span.textContent = pares(k);
      });
      if (todos[dim]) marcar(todos[dim], !hay(sel[dim]));
    }

    function pintarCard(card) {
      if (hay(sel.modelo) && !sel.modelo[card.dataset.modelo]) { card.hidden = true; return 0; }
      var conTalla = hay(sel.talla), conColor = hay(sel.color);
      var porTalla = {}, total = 0;
      card.querySelectorAll('.js-fila').forEach(function (fila) {
        var visible = !conColor || !!sel.color[fila.dataset.color], n = 0;
        fila.querySelectorAll('td[data-n]').forEach(function (td) {
          var t = td.dataset.talla, k = (visible && (!conTalla || sel.talla[t])) ? +td.dataset.n : 0;
          porTalla[t] = (porTalla[t] || 0) + k;
          n += k;
        });
        fila.hidden = !visible || ((conTalla || conColor) && n === 0);
        fila.querySelector('.js-fila-tot').textContent = n;
        total += n;
      });
      // Una columna se va si su talla no está elegida, o si con los filtros quedó en cero.
      card.querySelectorAll('[data-talla]').forEach(function (el) {
        var t = el.dataset.talla;
        el.hidden = (conTalla && !sel.talla[t]) || ((conTalla || conColor) && !porTalla[t]);
        if (el.tagName === 'TD' && !el.hasAttribute('data-n')) el.textContent = porTalla[t] || 0;
      });
      card.querySelector('.js-card-tot').textContent = total;
      card.querySelector('.js-card-par').textContent = total === 1 ? 'par' : 'pares';
      card.querySelector('.js-pie-tot').textContent = total;
      card.hidden = total === 0;
      return total;
    }

    function aplicar() {
      sel = podar(entradas, sel);
      var alguna = false;
      cards.forEach(function (card) { if (pintarCard(card)) alguna = true; });
      if (vacio) vacio.hidden = alguna;
      pintarChips('talla');
      pintarChips('color');
      chips.modelo.forEach(function (c) { marcar(c, !!sel.modelo[c.dataset.modelo]); });
      if (todos.modelo) marcar(todos.modelo, !hay(sel.modelo));
    }

    DIMS.forEach(function (dim) {
      chips[dim].forEach(function (c) {
        c.addEventListener('click', function () {
          var v = valor(c, dim);
          if (sel[dim][v]) delete sel[dim][v]; else sel[dim][v] = true;
          aplicar();
        });
      });
      if (todos[dim]) todos[dim].addEventListener('click', function () { sel[dim] = {}; aplicar(); });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
