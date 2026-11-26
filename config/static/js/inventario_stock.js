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
    res.sobra = [];
    res.sobraPos = [];  // posición de cada palabra de `sobra` entre las palabras del texto
    palabras.forEach(function (p, i) { if (!usada[i]) { res.sobra.push(p); res.sobraPos.push(i); } });
    return res;
  }

  // Distancia de edición entre dos palabras; cambiar dos letras vecinas de lugar cuenta 1.
  function distancia(a, b) {
    var d = [], i, j;
    for (i = 0; i <= a.length; i++) { d[i] = [i]; }
    for (j = 0; j <= b.length; j++) d[0][j] = j;
    for (i = 1; i <= a.length; i++) {
      for (j = 1; j <= b.length; j++) {
        var costo = a[i - 1] === b[j - 1] ? 0 : 1;
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + costo);
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
          d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
        }
      }
    }
    return d[a.length][b.length];
  }

  function esNumero(p) { return /^\d+(?:\.\d+)?$/.test(p); }

  // Qué tanto se parece la palabra escrita `s` a la palabra del nombre `w`:
  // 3 igual · 2 prefijo (≥2 letras) · 1 error de dedo (≥4 letras) · 0 nada. Números: solo iguales.
  function parecido(s, w) {
    if (s === w) return 3;
    if (esNumero(s) || esNumero(w)) return 0;
    if (s.length >= 2 && w.indexOf(s) === 0) return 2;
    if (s.length >= 4 && distancia(s, w) <= (s.length >= 7 ? 2 : 1)) return 1;
    return 0;
  }

  // Modelos que podrían ser lo que no se entendió (`sobra`). Solo los que tienen pares
  // (`conPares`) y no están prendidos; máximo 5, los más parecidos y con más pares primero.
  // `usadas`: índices de `sobra` que aportaron, para reemplazarlos al elegir.
  function sugerir(sobra, modelos, conPares, prendidos) {
    var res = [];
    (modelos || []).forEach(function (m, idx) {
      if (!conPares[m.clave] || (prendidos && prendidos[m.clave])) return;
      var ws = normalizar(m.nombre).split(' ').filter(Boolean);
      var puntos = 0, usadas = [], conLetras = false;
      sobra.forEach(function (s, k) {
        var mejor = 0;
        ws.forEach(function (w) { mejor = Math.max(mejor, parecido(s, w)); });
        if (!mejor) return;
        puntos += mejor;
        usadas.push(k);
        if (!esNumero(s)) conLetras = true;
      });
      if (conLetras) res.push({ clave: m.clave, nombre: m.nombre, n: conPares[m.clave], usadas: usadas, p: puntos, i: idx });
    });
    res.sort(function (a, b) { return (b.p - a.p) || (b.n - a.n) || (a.i - b.i); });
    return res.slice(0, 5).map(function (r) { return { clave: r.clave, nombre: r.nombre, n: r.n, usadas: r.usadas }; });
  }

  // Suma a `posiciones` las palabras pegadas a ellas que ya son parte de `nombre`: con
  // "jordan 4 retr" y "Jordan 4 Retro", el "jordan 4" que la búsqueda ya había tomado como
  // otro modelo se va con la elección, en vez de dejar prendidos los dos.
  function ampliarPosiciones(texto, posiciones, nombre) {
    var palabras = String(texto == null ? '' : texto).split(/\s+/).filter(Boolean).map(normalizar);
    var delNombre = {};
    normalizar(nombre).split(' ').forEach(function (w) { if (w) delNombre[w] = true; });
    var dentro = {};
    posiciones.forEach(function (p) { dentro[p] = true; });
    var ini = Math.min.apply(null, posiciones), fin = Math.max.apply(null, posiciones);
    while (ini > 0 && delNombre[palabras[ini - 1]]) dentro[--ini] = true;
    while (fin < palabras.length - 1 && delNombre[palabras[fin + 1]]) dentro[++fin] = true;
    return Object.keys(dentro).map(Number).sort(function (a, b) { return a - b; });
  }

  // Cambia las palabras en `posiciones` por `nombre` (en la primera de ellas) y quita las demás.
  function reemplazarPalabras(texto, posiciones, nombre) {
    var palabras = String(texto == null ? '' : texto).split(/\s+/).filter(Boolean);
    var primera = Math.min.apply(null, posiciones), quitar = {};
    posiciones.forEach(function (p) { quitar[p] = true; });
    var out = [];
    palabras.forEach(function (p, i) {
      if (i === primera) out.push(nombre);
      else if (!quitar[i]) out.push(p);
    });
    return out.join(' ');
  }

  // ¿`a` va antes que `b`? Listas de posiciones del mismo largo, en orden.
  function antes(a, b) {
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i];
    return false;
  }

  // Qué fichas de la búsqueda prender sobre lo ya marcado (`base`). hallazgos: [{dim, v}] en
  // el orden del texto. Gana el grupo más grande en el que cada ficha tiene pares con las
  // demás; a igual tamaño, el que conserva lo escrito primero. Devuelve sus posiciones.
  // Una búsqueda trae pocas fichas: se prueban todas las combinaciones (con más de 8, de
  // una en una en el orden del texto).
  function elegir(entradas, base, hallazgos) {
    var n = hallazgos.length;
    function valen(idx) {
      var s = { modelo: Object.assign({}, base.modelo), talla: Object.assign({}, base.talla),
                color: Object.assign({}, base.color), via: Object.assign({}, base.via || {}) };
      for (var i = 0; i < idx.length; i++) {
        if (hallazgos[idx[i]].v == null) return false;
        s[hallazgos[idx[i]].dim][hallazgos[idx[i]].v] = true;
      }
      return idx.every(function (i) {
        var h = hallazgos[i];
        return (cuenta(entradas, s, h.dim)[h.v] || 0) > 0;
      });
    }
    var mejor = [];
    if (n > 8) {
      for (var j = 0; j < n; j++) if (valen(mejor.concat([j]))) mejor.push(j);
      return mejor;
    }
    for (var mask = 1; mask < (1 << n); mask++) {
      var idx = [];
      for (var k = 0; k < n; k++) if (mask & (1 << k)) idx.push(k);
      if (idx.length < mejor.length || (idx.length === mejor.length && !antes(idx, mejor))) continue;
      if (valen(idx)) mejor = idx;
    }
    return mejor;
  }

  // La línea "Entendí" contra lo que de verdad quedó prendido: lo prendido es entendido, lo
  // apagado queda "sin pares". Lo que se tocó a mano después de buscar (`aMano`) no va en
  // ninguna de las dos: ya no es lo que dijo la búsqueda.
  function repartir(entendido, sel, aMano, clave) {
    var r = { si: [], sinPares: [] };
    entendido.forEach(function (h) {
      var v = clave(h);
      if (aMano[h.dim] && aMano[h.dim][v]) return;
      (sel[h.dim][v] ? r.si : r.sinPares).push(h);
    });
    return r;
  }

  // Query de una pestaña + lo marcado a mano + el texto del buscador (`q`).
  function queryDeFiltros(base, manual, texto) {
    var p = new URLSearchParams(base);
    DIMS.forEach(function (d) {
      Object.keys(manual[d] || {}).forEach(function (v) { p.append(d, v); });
    });
    var t = String(texto == null ? '' : texto).trim();
    if (t) p.set('q', t);
    return p.toString();
  }

  // Query de las descargas: solo modelo, talla, color y vía (sin q, estado ni base_id), cada
  // dimensión en su orden y sus valores en orden alfabético.
  function queryDescarga(sel) {
    var p = new URLSearchParams();
    DIMS.forEach(function (d) {
      Object.keys((sel && sel[d]) || {}).sort().forEach(function (v) { p.append(d, v); });
    });
    return p.toString();
  }

  // Lo inverso: los filtros y el texto que trae la URL.
  function filtrosDeQuery(search) {
    var p = new URLSearchParams(search), sel = { modelo: {}, talla: {}, color: {}, via: {} };
    DIMS.forEach(function (d) {
      p.getAll(d).forEach(function (v) { if (v) sel[d][v] = true; });
    });
    return { sel: sel, texto: p.get('q') || '' };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { pasa: pasa, cuenta: cuenta, podar: podar, desglose: desglose, normalizar: normalizar,
                       interpretar: interpretar, elegir: elegir, repartir: repartir,
                       queryDeFiltros: queryDeFiltros, filtrosDeQuery: filtrosDeQuery, queryDescarga: queryDescarga,
                       distancia: distancia, sugerir: sugerir, reemplazarPalabras: reemplazarPalabras,
                       ampliarPosiciones: ampliarPosiciones };
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

  // Las pestañas recargan la página: su enlace lleva lo marcado a mano y el texto buscado.
  var pestanas = [];
  function pintarPestanas(manual, texto) {
    pestanas.forEach(function (a) {
      a.setAttribute('href', '?' + queryDeFiltros(a.dataset.query, manual, texto));
    });
  }

  // Los enlaces "Descargar" llevan lo que quedó marcado (ya podado).
  function pintarDescargas(sel) {
    var q = queryDescarga(sel);
    document.querySelectorAll('.js-descarga').forEach(function (a) {
      a.setAttribute('href', a.dataset.url + (q ? '?' + q : ''));
    });
  }

  function iniciar() {
    pestanas = Array.prototype.slice.call(document.querySelectorAll('.js-pestana'));
    var desdeUrl = filtrosDeQuery(window.location.search);
    var cards = document.querySelectorAll('.js-card');
    // Sin pares no hay chips, pero los filtros siguen viajando a las otras pestañas.
    if (!cards.length) {
      pintarPestanas(desdeUrl.sel, desdeUrl.texto);
      pintarDescargas(desdeUrl.sel);
      return;
    }
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
    var aMano = vacios();        // lo tocado a mano después de la última búsqueda
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
      var manual = vacios();
      DIMS.forEach(function (d) {
        Object.keys(sel[d]).forEach(function (v) { if (!selBusqueda[d][v]) manual[d][v] = true; });
      });
      pintarPestanas(manual, buscar ? buscar.value : '');
      pintarDescargas(sel);
    }

    DIMS.forEach(function (dim) {
      chips[dim].forEach(function (c) {
        c.addEventListener('click', function () {
          var v = valor(c, dim);
          delete selBusqueda[dim][v];  // tocado a mano: la próxima búsqueda no lo apaga
          aMano[dim][v] = true;         // y deja de contar en "Entendí"
          if (sel[dim][v]) delete sel[dim][v]; else prender(dim, v);
          aplicar();
        });
      });
      if (todos[dim]) todos[dim].addEventListener('click', function () {
        if (ultima) ultima.entendido.forEach(function (h) { if (h.dim === dim) aMano[dim][claveDe(h)] = true; });
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

    // "Entendí" de la última búsqueda, contra lo que de verdad quedó prendido (ver `repartir`).
    var ultima = null;
    function pintarEntendi() {
      if (!entendi) return;
      entendi.textContent = '';
      if (!ultima) { entendi.hidden = true; return; }
      var r = repartir(ultima.entendido, sel, aMano, claveDe);
      var si = r.si.map(textoEntendido), sinPares = r.sinPares.map(textoEntendido);
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
      aMano = vacios();
      // Se prende el grupo más grande de fichas con pares juntas (ver `elegir`): así
      // "jordan 4 blanco 27" deja Jordan 4 + 27 aunque Jordan 4 no tenga blanco, y
      // "25 jordan 4 negro" no deja que el 25 tape a Jordan 4 negro. Lo que no entra queda
      // "sin pares".
      if (r) {
        var hs = r.entendido.map(function (h) { return { dim: h.dim, v: claveDe(h) }; });
        elegir(entradas, sel, hs).forEach(function (i) {
          var x = hs[i];
          if (sel[x.dim][x.v]) return;  // ya estaba marcado a mano: se respeta
          prender(x.dim, x.v);
          selBusqueda[x.dim][x.v] = true;
        });
      }
      aplicar();  // también repinta "Entendí" con lo que sobrevivió a podar
      pintarSugerencias();
    }

    // ── Sugerencias: modelos parecidos a lo que no se entendió, para elegir uno ──
    var cajaSug = document.getElementById('stock-sugerencias-caja');
    var listaSug = document.getElementById('stock-sugerencias');
    var sugerencias = [], activa = -1;

    function marcarActiva(i) {
      activa = i;
      Array.prototype.forEach.call(listaSug.children, function (li, k) {
        li.classList.toggle('is-activa', k === i);
        li.setAttribute('aria-selected', k === i ? 'true' : 'false');
      });
      if (i >= 0) buscar.setAttribute('aria-activedescendant', listaSug.children[i].id);
      else buscar.removeAttribute('aria-activedescendant');
    }

    function cerrarSugerencias() {
      sugerencias = [];
      if (!cajaSug) return;
      cajaSug.hidden = true;
      listaSug.textContent = '';
      buscar.setAttribute('aria-expanded', 'false');
      buscar.removeAttribute('aria-activedescendant');
      activa = -1;
    }

    function pintarSugerencias() {
      if (!cajaSug || !buscar) return;
      // Solo mientras se escribe: al cargar una pestaña con `q` no se abre sola.
      sugerencias = (ultima && ultima.sobra.length && document.activeElement === buscar)
        ? sugerir(ultima.sobra, cat.modelos, cuenta(entradas, sel, 'modelo'), sel.modelo) : [];
      if (!sugerencias.length) { cerrarSugerencias(); return; }
      listaSug.textContent = '';
      sugerencias.forEach(function (s, i) {
        var li = document.createElement('li'), nombre = document.createElement('span'), n = document.createElement('span');
        li.id = 'sug-' + s.clave;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        li.dataset.i = i;
        nombre.textContent = s.nombre;
        n.className = 'inv-filtro-n';
        n.textContent = pares(s.n);
        li.appendChild(nombre);
        li.appendChild(n);
        listaSug.appendChild(li);
      });
      cajaSug.hidden = false;
      buscar.setAttribute('aria-expanded', 'true');
      marcarActiva(-1);
    }

    function elegirSugerencia(i) {
      var s = sugerencias[i];
      if (!s || !ultima) return;
      var pos = ampliarPosiciones(buscar.value, s.usadas.map(function (k) { return ultima.sobraPos[k]; }), s.nombre);
      buscar.value = reemplazarPalabras(buscar.value, pos, s.nombre);
      clearTimeout(pausa);
      buscarAhora();
      cerrarSugerencias();
      buscar.focus();
    }

    if (cajaSug && buscar) {
      // Tocar una opción no debe quitarle el foco al buscador (el blur cerraría la lista).
      ['mousedown', 'pointerdown'].forEach(function (ev) {
        cajaSug.addEventListener(ev, function (e) { e.preventDefault(); });
      });
      listaSug.addEventListener('click', function (e) {
        var li = e.target.closest('li[role=option]');
        if (li) elegirSugerencia(+li.dataset.i);
      });
      buscar.addEventListener('keydown', function (e) {
        if (cajaSug.hidden) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          var ult = sugerencias.length - 1;
          marcarActiva(e.key === 'ArrowDown' ? Math.min(activa + 1, ult) : Math.max(activa - 1, 0));
        } else if (e.key === 'Enter' && activa >= 0) {
          e.preventDefault();
          elegirSugerencia(activa);
        } else if (e.key === 'Escape') {
          e.preventDefault();  // en un type=search, Escape también borraría el texto
          cerrarSugerencias();
        }
      });
      buscar.addEventListener('blur', cerrarSugerencias);
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
      aMano = vacios();
      ultima = null;
      cerrarSugerencias();
      aplicar();
      if (buscar) buscar.focus();
    });

    // Filtros de la URL (los trae una pestaña) y `?via=` de enlaces viejos, que el servidor ya
    // dejó marcado: se prenden los que existen en esta pestaña; lo demás se ignora.
    DIMS.forEach(function (dim) {
      chips[dim].forEach(function (c) {
        var v = valor(c, dim);
        if (desdeUrl.sel[dim][v] || (dim === 'via' && c.classList.contains('is-on'))) prender(dim, v);
      });
    });
    if (buscar && !buscar.value && desdeUrl.texto) buscar.value = desdeUrl.texto;
    aplicar();
    // El texto (de la URL, o el que el navegador devuelve al volver atrás) se vuelve a leer.
    if (buscar && buscar.value) buscarAhora();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
