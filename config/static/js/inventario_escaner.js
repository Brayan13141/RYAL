// Escáner de inventario (modos Recibir, Vender y Contar): pistola (Enter) o cámara (jsQR).
(function () {
  const cfg = document.getElementById('esc-cfg').dataset;
  const input = document.getElementById('esc-input');
  const lote = document.getElementById('esc-lote');
  const panel = document.getElementById('esc-panel');
  const msg = document.getElementById('esc-msg');
  const lista = document.getElementById('esc-lista');
  const video = document.getElementById('esc-video');
  const camBtn = document.getElementById('esc-cam');
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrftoken='));
  const token = csrf ? decodeURIComponent(csrf.split('=')[1]) : '';

  const TONOS = { verde: 880, amarillo: 520, rojo: 220 };
  // Las lecturas se encolan y se mandan de una en una: con la pistola el siguiente par
  // llega antes de la respuesta del anterior y no puede perderse.
  const cola = [];
  let enCurso = false;
  let ultimaLectura = '', ultimaLecturaAt = 0;  // la misma lectura en 1.5 s es un rebote

  function bip(nivel) {
    try {
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ac.createOscillator();
      osc.frequency.value = TONOS[nivel] || 440;
      osc.connect(ac.destination);
      osc.start();
      osc.stop(ac.currentTime + (nivel === 'rojo' ? 0.4 : 0.15));
    } catch (e) { /* sin audio: no pasa nada */ }
  }

  function post(url, cuerpo) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify(cuerpo),
    }).then(r => r.json());
  }

  function mostrar(nivel, texto) {
    panel.classList.remove('verde', 'amarillo', 'rojo');
    if (nivel) panel.classList.add(nivel);
    msg.textContent = texto;
  }

  function agregarLectura(d) {
    const li = document.createElement('li');
    li.className = 'mb-1';
    const punto = { verde: '🟢', amarillo: '🟡', rojo: '🔴' }[d.nivel] || '';
    const cod = d.codigo
      ? Object.assign(document.createElement('a'),
          { href: cfg.ficha.replace('CODIGO', d.codigo), textContent: d.codigo })
      : document.createTextNode('—');
    li.append(punto + ' ', cod, ' · ' + d.mensaje + ' ');
    if (d.nivel === 'verde' && d.movimiento_id) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn-g';
      b.textContent = '↩';
      b.title = 'Deshacer';
      b.addEventListener('click', () => deshacer(d.movimiento_id, li, b));
      li.append(b);
    }
    lista.prepend(li);
  }

  function deshacer(movimientoId, li, boton) {
    const nota = prompt('Motivo para deshacer esta lectura:');
    if (!nota || !nota.trim()) return;
    post(cfg.deshacer, { movimiento_id: movimientoId, nota: nota.trim() })
      .then(d => {
        mostrar(d.nivel, d.mensaje || d.error);
        bip(d.nivel);
        if (d.nivel === 'verde') { boton.remove(); li.style.textDecoration = 'line-through'; }
      })
      .catch(() => mostrar('rojo', 'Error de red'));
  }

  // ── Modo Vender: carrito de pares escaneados ────────────────────────────────
  let modo = 'recibir';
  const carrito = [];  // { codigo, nombre, costo, precios, precio (texto) }
  const vd = {
    caja: document.getElementById('vd-carrito'),
    vacio: document.getElementById('vd-vacio'),
    total: document.getElementById('vd-total'),
    metodo: document.getElementById('vd-metodo'),
    cobrar: document.getElementById('vd-cobrar'),
    msg: document.getElementById('vd-msg'),
  };
  let cobrando = false;
  let ligando = false;

  function precioValido(v) {
    return /^\d+(\.\d{1,2})?$/.test(String(v).trim());
  }

  function actualizarTotal() {
    let total = 0, completo = carrito.length > 0;
    carrito.forEach(l => {
      if (precioValido(l.precio)) total += parseFloat(l.precio); else completo = false;
    });
    vd.total.textContent = total.toFixed(2);
    vd.cobrar.disabled = !completo || cobrando;
  }

  // "Ya se vendió por el bot": liga el par escaneado a una venta del bot (sin pedido ni pago nuevos).
  function bloqueBot(l) {
    const caja = document.createElement('div');
    caja.className = 'mt-2 small';
    const tit = document.createElement('div');
    tit.textContent = 'Ya se vendió por el bot:';
    caja.append(tit);
    l.itemsBot.forEach(it => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn-g d-block mt-1';
      b.textContent = it.fecha + ' · pedido #' + it.pedido_id + ' · ' + it.nombre_snapshot +
        ' · ' + (it.libres > 0 ? it.libres + ' libre(s)' : 'sin libres (intercambia)');
      b.addEventListener('click', () => ligarBot(l, it, caja));
      caja.append(b);
    });
    return caja;
  }

  function ligarBot(l, it, caja) {
    if (cobrando || ligando) return;
    ligando = true;
    caja.querySelectorAll('button').forEach(b => { b.disabled = true; });
    post(cfg.venderLigar, { codigo: l.codigo, item_id: it.id })
      .then(d => {
        if (d.nivel === 'verde') {
          const i = carrito.indexOf(l);
          if (i >= 0) carrito.splice(i, 1);
          pintarCarrito();
        } else {
          caja.querySelectorAll('button').forEach(b => { b.disabled = false; });
        }
        avisar(d.nivel || 'rojo', d.mensaje || d.error || 'Error');
      })
      .catch(() => {
        caja.querySelectorAll('button').forEach(b => { b.disabled = false; });
        avisar('rojo', 'Error de red');
      })
      .finally(() => { ligando = false; });
  }

  function pintarCarrito() {
    vd.caja.replaceChildren();
    carrito.forEach((l, i) => {
      const fila = document.createElement('div');
      fila.className = 'border rounded p-2 mb-2';
      const tit = document.createElement('div');
      tit.textContent = l.codigo + ' · ' + l.nombre + ' (costo $' + l.costo + ')';
      const campo = document.createElement('input');
      campo.type = 'text';
      campo.inputMode = 'decimal';
      campo.className = 'form-control form-control-sm d-inline-block';
      campo.style.maxWidth = '120px';
      campo.placeholder = 'Precio';
      campo.value = l.precio;
      campo.addEventListener('input', () => { l.precio = campo.value; actualizarTotal(); });
      const botones = document.createElement('div');
      botones.className = 'd-flex flex-wrap gap-1 my-1';
      l.precios.forEach(p => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn-g';
        b.textContent = p.nombre + ' $' + p.precio;
        b.addEventListener('click', () => { l.precio = p.precio; campo.value = p.precio; actualizarTotal(); });
        botones.append(b);
      });
      const quitar = document.createElement('button');
      quitar.type = 'button';
      quitar.className = 'btn-g';
      quitar.textContent = '✕';
      quitar.title = 'Quitar del carrito';
      quitar.addEventListener('click', () => { carrito.splice(i, 1); pintarCarrito(); });
      fila.append(tit, botones, campo, ' ', quitar);
      if (l.itemsBot && l.itemsBot.length) fila.append(bloqueBot(l));
      vd.caja.append(fila);
    });
    vd.vacio.hidden = carrito.length > 0;
    actualizarTotal();
  }

  function avisar(nivel, texto) {
    mostrar(nivel, texto);
    bip(nivel);
  }

  function escanearVender(lectura) {
    return post(cfg.venderInfo, { lectura: lectura })
      .then(d => {
        if (d.nivel === 'amarillo') {  // vendido por el bot: no entra al carrito
          const n = (d.items_bot && d.items_bot[0]) ? ' (pedido #' + d.items_bot[0].pedido_id + ')' : '';
          avisar('amarillo', (d.mensaje || '') + n);
          return;
        }
        if (d.nivel !== 'verde') { avisar('rojo', d.mensaje || d.error || 'Error'); return; }
        if (carrito.some(l => l.codigo === d.par.codigo)) {
          avisar('amarillo', 'Ese par ya está en el carrito'); return;
        }
        carrito.push({
          codigo: d.par.codigo,
          nombre: d.par.nombre + ' ' + d.par.color + ' ' + d.par.talla,
          costo: d.par.costo, precios: d.precios, precio: '',
          itemsBot: d.items_bot || [],
        });
        pintarCarrito();
        avisar('verde', d.par.codigo + ' agregado');
      })
      .catch(() => avisar('rojo', 'Error de red'));
  }

  vd.cobrar.addEventListener('click', () => {
    if (cobrando || !carrito.length) return;
    cobrando = true;
    vd.cobrar.disabled = true;  // protección contra doble envío
    const cuerpo = {
      lineas: carrito.map(l => ({ codigo: l.codigo, precio: l.precio.trim() })),
      metodo_pago: vd.metodo.value,
    };
    post(cfg.venderCerrar, cuerpo)
      .then(d => {
        // El bot ya registró esa venta: se confirma antes de cobrarla otra vez.
        if (!d.ok && d.requiere_confirmacion && confirm(d.error)) {
          cuerpo.confirmar_venta_nueva = true;
          return post(cfg.venderCerrar, cuerpo);
        }
        return d;
      })
      .then(d => {
        if (d.ok) {
          carrito.length = 0;
          pintarCarrito();
          vd.msg.replaceChildren('Venta registrada: ',
            Object.assign(document.createElement('a'), {
              href: cfg.pedido.replace(/0\/$/, d.pedido_id + '/'),
              textContent: 'pedido #' + d.pedido_id }));
          avisar('verde', 'Venta registrada');
        } else {
          vd.msg.textContent = d.error || 'No se pudo registrar la venta';
          avisar('rojo', d.error || 'Error');
        }
      })
      .catch(() => avisar('rojo', 'Error de red'))
      .finally(() => { cobrando = false; actualizarTotal(); });
  });

  function cambiarModo(nuevo) {
    modo = nuevo;
    document.getElementById('tab-recibir').classList.toggle('active', nuevo === 'recibir');
    document.getElementById('tab-vender').classList.toggle('active', nuevo === 'vender');
    document.getElementById('tab-contar').classList.toggle('active', nuevo === 'contar');
    document.getElementById('esc-contar').hidden = nuevo !== 'contar';
    document.getElementById('esc-lote-box').hidden = nuevo !== 'recibir';
    document.getElementById('esc-recibir').hidden = nuevo !== 'recibir';
    document.getElementById('esc-vender').hidden = nuevo !== 'vender';
    mostrar(null, 'Listo para escanear.');
    input.focus();
  }
  document.getElementById('tab-recibir').addEventListener('click', () => cambiarModo('recibir'));
  document.getElementById('tab-vender').addEventListener('click', () => cambiarModo('vender'));
  document.getElementById('tab-contar').addEventListener('click', () => cambiarModo('contar'));

  // ── Modo Contar: las lecturas van al conteo abierto (nunca cambian el estado del par) ──
  function escanearContar(lectura) {
    if (!cfg.conteoId) { avisar('rojo', 'Inicia un conteo primero.'); return Promise.resolve(); }
    return post(cfg.contar, { lectura: lectura, conteo_id: cfg.conteoId })
      .then(d => {
        avisar(d.nivel || 'rojo', d.mensaje || d.error || 'Error');
        const l = document.getElementById('ct-lista');
        if (d.nivel && l) {
          const li = document.createElement('li');
          li.textContent = ({ verde: '🟢', amarillo: '🟡', rojo: '🔴' }[d.nivel] || '') + ' ' +
            (d.codigo || lectura) + ' · ' + d.mensaje;
          l.prepend(li);
        }
      })
      .catch(() => avisar('rojo', 'Error de red'));
  }

  function escanearRecibir(lectura) {
    if (!lote.value) { mostrar('rojo', 'Elige un lote primero.'); bip('rojo'); return Promise.resolve(); }
    return post(cfg.recibir, { lectura: lectura, lote_id: lote.value })
      .then(d => {
        const nivel = d.nivel || 'rojo';
        mostrar(nivel, d.mensaje || d.error || 'Error');
        bip(nivel);
        if (d.nivel) agregarLectura(d);
      })
      .catch(() => { mostrar('rojo', 'Error de red'); bip('rojo'); });
  }

  function procesarCola() {
    if (enCurso || !cola.length) return;
    enCurso = true;
    const { lectura, modoLectura } = cola.shift();
    const accion = modoLectura === 'vender' ? escanearVender
      : modoLectura === 'contar' ? escanearContar : escanearRecibir;
    accion(lectura).finally(() => { enCurso = false; procesarCola(); });
  }

  function enviar(lectura) {
    lectura = (lectura || '').trim();
    if (!lectura) return;
    const ahora = Date.now();
    if (lectura === ultimaLectura && ahora - ultimaLecturaAt < 1500) return;  // rebote
    ultimaLectura = lectura; ultimaLecturaAt = ahora;
    cola.push({ lectura: lectura, modoLectura: modo });
    procesarCola();
  }

  if (new URLSearchParams(location.search).has('conteo')) cambiarModo('contar');

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      // Se vacía al instante: lo que teclee la pistola después es el siguiente par.
      const lectura = input.value;
      input.value = '';
      enviar(lectura);
    }
  });
  // La pistola necesita el foco siempre: lo recupera al tocar fuera de controles.
  document.addEventListener('click', e => {
    if (!e.target.closest('select, button, a, input')) input.focus();
  });

  // ── Cámara (mismo patrón que el POS) ───────────────────────────────────────
  let stream = null;
  let ultimoCodigo = '', ultimoEnvio = 0;  // la cámara no re-envía el mismo código en 4 s
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  function detener() {
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    video.srcObject = null;
    video.classList.remove('on');
  }

  function bucle() {
    if (!stream) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = window.jsQR && window.jsQR(img.data, img.width, img.height,
        { inversionAttempts: 'dontInvert' });
      if (code && code.data) {
        const ahora = Date.now();
        if (code.data !== ultimoCodigo || ahora - ultimoEnvio > 4000) {
          ultimoCodigo = code.data; ultimoEnvio = ahora;
          enviar(code.data);
        }
      }
    }
    requestAnimationFrame(bucle);
  }

  camBtn.addEventListener('click', async () => {
    if (stream) { detener(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      mostrar('rojo', 'Tu navegador no soporta acceso a la cámara.');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      video.srcObject = stream;
      video.classList.add('on');
      await video.play();
      bucle();
    } catch (err) {
      detener();
      mostrar('rojo', 'Sin acceso a la cámara: ' + err.message);
    }
  });
})();
