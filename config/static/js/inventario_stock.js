/* Filtros de la pantalla Stock: modelo, talla, color y vía, sin recargar, más un buscador
 * que traduce el texto a esos mismos chips.
 *
 * Cada filtro reduce a los otros: los chips de talla muestran solo las tallas que hay en
 * los modelos, colores y vías elegidos (con su número de pares), y así los demás. Lo
 * marcado que deja de existir se desmarca solo.
 *
 * La lógica es pura (cuenta / podar / pasa / desglose / interpretar) y se prueba con node:
 *   node --test inventario/tests/js/stock_filtros.test.js inventario/tests/js/buscador.test.js
 */
(function () {
  'use strict';

  var DIMS = ['modelo', 'talla', 'color', 'via'];
  var VIAS = [['aereo', 'A'], ['maritimo', 'M'], ['nacional', 'N']];

  function hay(o) { return Object.keys(o).length > 0; }

  // ¿La entrada pasa los filtros? `excepto` ignora una dimensión (para contar sus chips).
  function pasa(e, sel, excepto) {
    for (var i = 0; i < DIMS.length; i++) {
      var d = DIMS[i];
      if (d !== excepto && sel[d] && hay(sel[d]) && !sel[d][e[d]]) return false;
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

  // Copia de `sel` sin las tallas, colores ni vías marcados que ya no tienen pares.
  // Primero las tallas, luego los colores y al final las vías: soltar una dimensión solo
  // agrega pares a las siguientes.
  function podar(entradas, sel) {
    var nuevo = { modelo: Object.assign({}, sel.modelo), talla: {},
                  color: Object.assign({}, sel.color), via: Object.assign({}, sel.via) };
    var nt = cuenta(entradas, nuevo, 'talla');
    Object.keys(sel.talla).forEach(function (t) { if (nt[t]) nuevo.talla[t] = true; });
    var nc = cuenta(entradas, nuevo, 'color');
    nuevo.color = {};
    Object.keys(sel.color).forEach(function (c) { if (nc[c]) nuevo.color[c] = true; });
    var nv = cuenta(entradas, nuevo, 'via');
    nuevo.via = {};
    Object.keys(sel.via || {}).forEach(function (v) { if (nv[v]) nuevo.via[v] = true; });
    return nuevo;
  }

  // Desglose por vía como lo escribe el servidor: 'A', 'A4 M2'. Una sola vía → solo la letra.
  function desglose(porVia) {
    var partes = [], n = 0;
    VIAS.forEach(function (v) { if (porVia[v[0]]) { n++; partes.push([v[1], porVia[v[0]]]); } });
    return partes.map(function (p) { return n === 1 ? p[0] : p[0] + p[1]; }).join(' ');
  }

  // Minúsculas, sin acentos, sin espacios repetidos ni en los extremos.
  function normalizar(s) {
    return String(s == null ? '' : s).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().split(/\s+/).filter(Boolean).join(' ');
  }

  var PALABRAS_VIA = { aereo: 'aereo', avion: 'aereo', maritimo: 'maritimo', barco: 'maritimo', nacional: 'nacional' };
  var NOMBRE_VIA = { aereo: 'Aéreo', maritimo: 'Marítimo', nacional: 'Nacional' };

  // Texto libre → qué fichas encender. cat: {modelos:[{clave,nombre}], colores:[{clave,nombre}], tallas:[str]}.
  function interpretar(texto, cat) {
    var palabras = normalizar(texto).split(' ').filter(Boolean);
    var usada = palabras.map(function () { return false; });
    var res = { modelo: {}, color: {}, talla: {}, via: {}, entendido: [], sobra: [] };
    var hallazgos = []; // {pos, dim, nombre}: se ordenan por aparición al final

    // Nombres de más palabras primero; a igual largo, el orden del catálogo.
    function buscarNombres(dim, lista) {
      var cand = (lista || []).map(function (it, i) {
        return { it: it, i: i, w: normalizar(it.nombre).split(' ').filter(Boolean) };
      }).filter(function (c) { return c.w.length; });
      cand.sort(function (a, b) { return (b.w.length - a.w.length) || (a.i - b.i); });
      cand.forEach(function (c) {
        for (var ini = 0; ini + c.w.length <= palabras.length; ini++) {
          var ok = true;
          for (var k = 0; k < c.w.length; k++) {
            if (usada[ini + k] || palabras[ini + k] !== c.w[k]) { ok = false; break; }
          }
          if (!ok) continue;
          for (var j = 0; j < c.w.length; j++) usada[ini + j] = true;
          res[dim][c.it.clave] = true;
          hallazgos.push({ pos: ini, dim: dim, nombre: c.it.nombre });
          break;
        }
      });
    }
    buscarNombres('modelo', cat && cat.modelos);
    buscarNombres('color', cat && cat.colores);

    var tallas = (cat && cat.tallas) || [];
    palabras.forEach(function (p, i) {
      if (usada[i]) return;
      if (Object.prototype.hasOwnProperty.call(PALABRAS_VIA, p)) {
        var v = PALABRAS_VIA[p];
        usada[i] = true; res.via[v] = true;
        hallazgos.push({ pos: i, dim: 'via', nombre: NOMBRE_VIA[v] });
        return;
      }
      var m = /^t?(\d+(?:\.\d+)?)$/.exec(p);
      if (m) {
        var t = String(parseFloat(m[1]));
        if (tallas.indexOf(t) !== -1) {
          usada[i] = true; res.talla[t] = true;
          hallazgos.push({ pos: i, dim: 'talla', nombre: t });
        }
      }
    });

    hallazgos.sort(function (a, b) { return a.pos - b.pos; });
    res.entendido = hallazgos.map(function (h) { return { dim: h.dim, nombre: h.nombre }; });
    res.sobra = palabras.filter(function (p, i) { return !usada[i]; });
    return res;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { pasa: pasa, cuenta: cuenta, podar: podar, desglose: desglose, normalizar: normalizar, interpretar: interpretar };
    return;
  }

  function pares(n) { return n + (n === 1 ? ' par' : ' pares'); }

  function vacios() { return { modelo: {}, talla: {}, color: {}, via: {} }; }

  // Atributo de la celda → vía: data-a (avión), data-m (barco), data-nac (nacional).
  var ATRIBUTO_VIA = { aereo: 'a', maritimo: 'm', nacional: 'nac' };

  // Texto del chip sin su conteo: "Jordan 4 <span>3 pares</span>" → "Jordan 4".
  function nombreDe(chip) {
    var s = '';
    chip.childNodes.forEach(function (n) { if (n.nodeType === 3) s += n.nodeValue; });
    return s.trim();
  }

  function iniciar() {
    var cards = document.querySelectorAll('.js-card');
    if (!cards.length) return;
    var chips = {
      modelo: document.querySelectorAll('.js-filtro'),
      talla: document.querySelectorAll('.js-talla'),
      color: document.querySelectorAll('.js-color'),
      via: document.querySelectorAll('.js-via'),
    };
    var todos = {
      modelo: document.getElementById('filtro-todos'),
      talla: document.getElementById('talla-todas'),
      color: document.getElementById('color-todos'),
      via: document.getElementById('via-todas'),
    };
    var vacio = document.getElementById('cards-vacio');
    var buscar = document.getElementById('stock-buscar');
    var limpiar = document.getElementById('stock-buscar-limpiar');
    var entendi = document.getElementById('stock-entendi');
    var sel = vacios();          // vacío = sin filtro
    var selBusqueda = vacios();  // lo que prendió la última búsqueda (no lo marcado a mano)
    var marcadoEn = vacios();    // valor → número de orden en que se marcó
    var contador = 0;

    function valor(chip, dim) { return chip.dataset[dim]; }  // data-modelo / data-talla / data-color / data-via

    // Una entrada por celda y vía con pares; cada celda guarda sus pares por vía.
    var entradas = [];
    cards.forEach(function (card) {
      card.querySelectorAll('.js-fila').forEach(function (fila) {
        fila.querySelectorAll('td[data-n]').forEach(function (td) {
          td._vias = {};
          VIAS.forEach(function (v) {
            var n = +(td.getAttribute('data-' + ATRIBUTO_VIA[v[0]]) || 0);
            td._vias[v[0]] = n;
            if (n) entradas.push({ modelo: card.dataset.modelo, color: fila.dataset.color,
                                   talla: td.dataset.talla, via: v[0], n: n });
          });
        });
      });
    });

    function marcar(chip, on) {
      chip.classList.toggle('is-on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    var alFrente = {};  // dimensiones con un chip recién marcado: su carril vuelve al inicio

    function prender(dim, v) {
      sel[dim][v] = true;
      marcadoEn[dim][v] = ++contador;
      alFrente[dim] = true;
    }

    // Conteo y visibilidad de los chips de `dim` con los otros filtros. Lo marcado no se
    // esconde aunque quede en cero, para poder apagarlo. El "Todos" de modelo no cambia.
    function pintarChips(dim) {
      var n = cuenta(entradas, sel, dim);
      chips[dim].forEach(function (c) {
        var v = valor(c, dim), k = n[v] || 0;
        marcar(c, !!sel[dim][v]);
        c.hidden = !k && !sel[dim][v];
        var span = c.querySelector('.inv-filtro-n');
        if (span) span.textContent = pares(k);
      });
      if (todos[dim]) marcar(todos[dim], !hay(sel[dim]));
    }

    // Lo marcado al principio del carril, en el orden en que se marcó; "Todos" siempre primero.
    // Se mueven los mismos nodos (appendChild), así conservan sus listeners; solo si el orden
    // cambió, y devolviendo el foco al chip que lo tenía (mover un nodo lo suelta).
    function reordenar(dim) {
      var lista = Array.prototype.slice.call(chips[dim]);
      if (!lista.length) return;
      var carril = lista[0].parentNode;
      var marcados = lista.filter(function (c) { return sel[dim][valor(c, dim)]; });
      marcados.sort(function (a, b) {
        return (marcadoEn[dim][valor(a, dim)] || 0) - (marcadoEn[dim][valor(b, dim)] || 0);
      });
      var orden = (todos[dim] ? [todos[dim]] : []).concat(marcados,
        lista.filter(function (c) { return !sel[dim][valor(c, dim)]; }));
      var actual = carril.children, igual = actual.length === orden.length;
      for (var i = 0; igual && i < orden.length; i++) igual = actual[i] === orden[i];
      if (!igual) {
        var foco = document.activeElement;
        orden.forEach(function (c) { carril.appendChild(c); });
        if (foco && foco !== document.activeElement && carril.contains(foco)) foco.focus({ preventScroll: true });
      }
      if (alFrente[dim]) { carril.scrollLeft = 0; delete alFrente[dim]; }
    }

    function pintarCelda(td, k, porVia) {
      var span = td.querySelector('.inv-cell'), letras = desglose(porVia);
      if (!span || span._k === k + '|' + letras) return;
      span._k = k + '|' + letras;
      span.className = 'inv-cell ' + (!k ? 'h0' : k >= 4 ? 'h3' : k >= 2 ? 'h2' : 'h1');
      span.textContent = '';
      if (!k) { span.textContent = '·'; return; }
      var b = document.createElement('b'), i = document.createElement('i');
      b.textContent = k;
      i.textContent = letras;
      span.appendChild(b);
      span.appendChild(i);
    }

    function pintarCard(card) {
      if (hay(sel.modelo) && !sel.modelo[card.dataset.modelo]) { card.hidden = true; return 0; }
      var conTalla = hay(sel.talla), conColor = hay(sel.color), conVia = hay(sel.via);
      var filtrado = conTalla || conColor || conVia;
      var porTalla = {}, total = 0;
      card.querySelectorAll('.js-fila').forEach(function (fila) {
        var visible = !conColor || !!sel.color[fila.dataset.color], n = 0;
        fila.querySelectorAll('td[data-n]').forEach(function (td) {
          var t = td.dataset.talla, porVia = {}, enCelda = 0;
          VIAS.forEach(function (v) {
            if (!conVia || sel.via[v[0]]) { porVia[v[0]] = td._vias[v[0]]; enCelda += td._vias[v[0]]; }
          });
          pintarCelda(td, enCelda, porVia);
          var k = (visible && (!conTalla || sel.talla[t])) ? enCelda : 0;
          porTalla[t] = (porTalla[t] || 0) + k;
          n += k;
        });
        fila.hidden = !visible || (filtrado && n === 0);
        fila.querySelector('.js-fila-tot').textContent = n;
        total += n;
      });
      // Una columna se va si su talla no está elegida, o si con los filtros quedó en cero.
      card.querySelectorAll('[data-talla]').forEach(function (el) {
        var t = el.dataset.talla;
        el.hidden = (conTalla && !sel.talla[t]) || (filtrado && !porTalla[t]);
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
      pintarChips('via');
      pintarChips('modelo');
      DIMS.forEach(reordenar);
      pintarEntendi();
    }

    DIMS.forEach(function (dim) {
      chips[dim].forEach(function (c) {
        c.addEventListener('click', function () {
          var v = valor(c, dim);
          delete selBusqueda[dim][v];  // tocado a mano: la próxima búsqueda no lo apaga
          if (sel[dim][v]) delete sel[dim][v]; else prender(dim, v);
          aplicar();
        });
      });
      if (todos[dim]) todos[dim].addEventListener('click', function () {
        sel[dim] = {};
        selBusqueda[dim] = {};
        aplicar();
      });
    });

    // ── Buscador ──
    var cat = {
      modelos: Array.prototype.map.call(chips.modelo, function (c) { return { clave: c.dataset.modelo, nombre: nombreDe(c) }; }),
      colores: Array.prototype.map.call(chips.color, function (c) { return { clave: c.dataset.color, nombre: nombreDe(c) }; }),
      tallas: Array.prototype.map.call(chips.talla, function (c) { return c.dataset.talla; }),
    };

    function textoEntendido(h) {
      if (h.dim === 'modelo') return h.nombre.toUpperCase();
      if (h.dim === 'talla') return 'talla ' + h.nombre;
      return h.nombre.toLowerCase();
    }

    // Nombre que `interpretar` pone en `entendido` → su clave en `sel`.
    function claveDe(h) {
      if (h.dim === 'talla') return h.nombre;
      if (h.dim === 'via') return Object.keys(NOMBRE_VIA).filter(function (v) { return NOMBRE_VIA[v] === h.nombre; })[0];
      var it = cat[h.dim === 'modelo' ? 'modelos' : 'colores'].filter(function (x) { return x.nombre === h.nombre; })[0];
      return it && it.clave;
    }

    // "Entendí" de la última búsqueda, contra lo que de verdad quedó prendido: lo que
    // `podar` soltó (no tiene pares con lo demás) no se presume entendido, va atenuado.
    var ultima = null;
    function pintarEntendi() {
      if (!entendi) return;
      entendi.textContent = '';
      if (!ultima) { entendi.hidden = true; return; }
      var si = [], sinPares = [];
      ultima.entendido.forEach(function (h) {
        (sel[h.dim][claveDe(h)] ? si : sinPares).push(textoEntendido(h));
      });
      var algo = false;
      function parte(txt, atenuado) {
        var nodo = atenuado ? document.createElement('span') : document.createTextNode('');
        if (atenuado) nodo.className = 'inv-muted';
        nodo.textContent = (algo ? ' · ' + txt : txt.charAt(0).toUpperCase() + txt.slice(1));
        entendi.appendChild(nodo);
        algo = true;
      }
      if (si.length) parte('entendí: ' + si.join(' · '));
      if (sinPares.length) parte('sin pares: ' + sinPares.join(' · '), true);
      if (ultima.sobra.length) parte('no entendí: ' + ultima.sobra.join(' '), true);
      entendi.hidden = false;
    }

    function buscarAhora() {
      var texto = buscar.value;
      var r = normalizar(texto) ? interpretar(texto, cat) : null;
      ultima = r;
      DIMS.forEach(function (dim) {
        Object.keys(selBusqueda[dim]).forEach(function (v) { delete sel[dim][v]; });
        selBusqueda[dim] = {};
      });
      // En el orden del texto, cada ficha se prende solo si tiene pares con lo ya prendido:
      // así "jordan 4 blanco 27" deja Jordan 4 + 27 aunque Jordan 4 no tenga blanco (si no,
      // podar soltaría la talla por culpa del color). Lo que no entra queda "sin pares".
      if (r) r.entendido.forEach(function (h) {
        var v = claveDe(h);
        if (v == null || sel[h.dim][v]) return;  // ya estaba marcado a mano: se respeta
        if (!cuenta(entradas, sel, h.dim)[v]) return;
        prender(h.dim, v);
        selBusqueda[h.dim][v] = true;
      });
      aplicar();  // también repinta "Entendí" con lo que sobrevivió a podar
    }

    var pausa = null;
    if (buscar) buscar.addEventListener('input', function () {
      clearTimeout(pausa);
      pausa = setTimeout(buscarAhora, 150);
    });

    if (limpiar) limpiar.addEventListener('click', function () {
      clearTimeout(pausa);
      if (buscar) buscar.value = '';
      sel = vacios();
      selBusqueda = vacios();
      marcadoEn = vacios();
      ultima = null;
      aplicar();
      if (buscar) buscar.focus();
    });

    // `?via=` de enlaces viejos: el chip llega marcado desde el servidor.
    chips.via.forEach(function (c) { if (c.classList.contains('is-on')) prender('via', valor(c, 'via')); });
    aplicar();
    // El navegador puede devolver el texto al volver atrás: se vuelve a leer.
    if (buscar && buscar.value) buscarAhora();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
