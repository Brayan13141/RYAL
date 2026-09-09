const axios = require('axios')
const pino = require('pino')
const qrcode = require('qrcode-terminal')
require('dotenv').config()

// Endpoint local (solo 127.0.0.1) para que el watchdog de otros bots pida
// mandar un aviso de WhatsApp reusando esta conexión ya viva, sin abrir una
// sesión nueva (evita repetir el mismo desajuste de sesión que se está avisando).
// Sin NOTIFY_PORT ni ALERT_JID: sus consumidores (el servidor de avisos y las
// alertas del watchdog) viven en persona1/persona2. Los heredamos con el
// recorte y se quitan a propósito — el fallback traía un teléfono escrito en
// el código, y este repo es público.
let currentSock = null

const { extractPrice, parseModaArgs } = require('./utils')
const { acquireAuthLock } = require('./lock')
const { createOrderSessionStore } = require('./orderSession')
const { avisoSinTipo } = require('./avisoSinTipo')
const { shouldHandleOrders } = require('./routing')
const { writeQrState } = require('./qrState')
const { normalizarNumero, debePedirCodigo } = require('./pairing')
const { mensajeSinTipo } = require('./ventaSinTipo')

const AUTH_DIR = '.baileys_auth'
const QR_STATE_FILE = '.qr_state.json'

// Baileys es ESM-only (>=6.7.x) → se carga con import() dinámico desde este
// módulo CommonJS; se asignan en main() antes de connect().
let makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage
let waVersion   // versión de WA Web (sin esto WhatsApp rechaza y cae en loop)

const MARKUP          = parseInt(process.env.MARKUP || '100')
const DJANGO_URL      = process.env.DJANGO_API_URL || 'http://localhost:8000'
const DJANGO_KEY      = process.env.DJANGO_API_KEY
const ORDERS_GID = process.env.ORDERS_GROUP_ID  // undefined → feature deshabilitada

// Vinculación por código en vez de por QR. Sin PAIR_NUMBER (o con un número
// que no se puede normalizar) queda el QR de siempre: la feature se apaga
// sola, no rompe el arranque. El porqué del formato está en pairing.js.
const PAIR_NUMBER_RAW = process.env.PAIR_NUMBER
const PAIR_NUMBER = normalizarNumero(PAIR_NUMBER_RAW || '')
// Una sola vez por proceso: connection.update emite un qr nuevo cada 60s y
// pedir un código por cada uno invalidaría a mitad el que se está tecleando.
let codigoYaPedido = false

const logger = pino({ level: 'info' })
const orders = createOrderSessionStore()

// Pausa entre imágenes al reenviar un lote: sin ella, hasta 50 descargas+resubidas
// en ráfaga saturan el socket (keepalive perdido → 408 / stream errored → reconexión,
// que dispara "Sincronizando..." en el teléfono vinculado).

// Cache de mensajes enviados para getMessage (retry receipts): cuando un
// destinatario no puede descifrar, WA pide reenviar el mensaje; sin cache
// Baileys responde undefined y esa entrega se pierde ("Falló la sincronización").
const MSG_CACHE_MAX = 500
const sentMsgCache = new Map()
function cacheSentMessage(key, message) {
    if (!key?.id || !message) return
    if (sentMsgCache.size >= MSG_CACHE_MAX) sentMsgCache.delete(sentMsgCache.keys().next().value)
    sentMsgCache.set(key.id, message)
}


async function getDescuento(telefono) {
    try {
        const { data } = await axios.get(
            `${DJANGO_URL}/api/negocio/cliente/${telefono}/`,
            { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 5000 }
        )
        return Number(data.descuento) || 0
    } catch (err) {
        logger.warn({ telefono, err: err.message }, 'No se pudo consultar descuento — usando 0')
        return 0
    }
}


function getText(msg) {
    return msg.message?.conversation || msg.message?.extendedTextMessage?.text || ''
}








const MOSTRADOR_NOMBRE = 'Mostrador'
const MOSTRADOR_TEL    = 'TIENDA-MOSTRADOR'

function parseItemText(text) {
    const tokens = (text || '').trim().split(/\s+/).filter(Boolean)
    if (tokens.length === 0) return null
    const lastToken = tokens[tokens.length - 1]
    const price = parseFloat(lastToken)
    if (isNaN(price) || price <= 0) return null
    const rest = tokens.slice(0, -1)
    let qty = 1
    let description = ''
    if (rest.length > 0 && /^\d+$/.test(rest[0])) {
        qty = parseInt(rest[0], 10)
        description = rest.slice(1).join(' ')
    } else {
        description = rest.join(' ')
    }
    if (qty < 1) return null
    return { qty, description, price }
}

async function buscarCliente(q) {
    try {
        const { data } = await axios.get(
            `${DJANGO_URL}/api/negocio/clientes/buscar/`,
            {
                params: { q },
                headers: { Authorization: `Bearer ${DJANGO_KEY}` },
                timeout: 5000,
            }
        )
        return data.clientes || []
    } catch (err) {
        logger.warn({ err: err.message }, 'buscarCliente falló — devuelve vacío')
        return []
    }
}

async function crearPedidoModa(sock, { nombre, telefono, cantidad, ganancia, envio }) {
    const payload = {
        nombre,
        telefono,
        items: [{ description: 'Moda', qty: cantidad, price: ganancia, costo: 0 }],
        envio,
    }
    try {
        const { data } = await axios.post(
            `${DJANGO_URL}/api/negocio/pedido/`, payload,
            { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 10000 },
        )
        await sock.sendMessage(ORDERS_GID, {
            text: `✅ Pedido #${data.pedido_id} creado — Ganancia: $${Number((cantidad * ganancia).toFixed(2))} MXN`,
        })
    } catch (err) {
        logger.error({ err: err.message }, 'Error al crear pedido moda en Django')
        await sock.sendMessage(ORDERS_GID, { text: '❌ Error al crear el pedido. Intenta de nuevo.' })
    }
}

async function resolveClienteYCrearModa(sock, moda) {
    const { query, cantidad, ganancia, envio } = moda
    const isPhone = /^\d{10,13}$/.test(query.replace(/\s/g, ''))
    const clientes = await buscarCliente(query)

    if (isPhone) {
        const digits = query.replace(/\s/g, '')
        const nombre = clientes.length > 0 ? clientes[0].nombre : `Tel. ${digits}`
        const telefono = clientes.length > 0 ? clientes[0].telefono : digits
        await crearPedidoModa(sock, { nombre, telefono, cantidad, ganancia, envio })
        return
    }

    if (clientes.length === 0) {
        await sock.sendMessage(ORDERS_GID, {
            text: '⚠️ No encontré ningún cliente con ese nombre.\nBusca por teléfono (/pedido <número> moda ...) o regístralo en el panel.',
        })
        return
    }

    if (clientes.length > 1) {
        const lines = clientes.map((c, i) => `${i + 1}. ${c.nombre} — ${c.telefono}`)
        orders.setPending(ORDERS_GID, 'disambig_moda', { clientes, cantidad, ganancia, envio })
        await sock.sendMessage(ORDERS_GID, {
            text: `🔍 Varios resultados:\n${lines.join('\n')}\nResponde con el número de la opción.`,
        })
        return
    }

    await crearPedidoModa(sock, {
        nombre: clientes[0].nombre, telefono: clientes[0].telefono, cantidad, ganancia, envio,
    })
}

/**
 * Arma el payload de una venta tienda a partir de la sesión ACTUAL. Es la
 * única fuente de verdad para /cerrar, para el cierre de una sesión previa
 * en el conflicto, y para el reintento tras resolver un alias — así un ítem
 * cargado después del 409 y antes de la respuesta numérica no se pierde.
 */
function payloadVentaTienda(sess, envio = 0) {
    return {
        items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty })),
        envio,
    }
}

/**
 * Manda la venta a Django. El 409 no es un error: es "no se puede registrar
 * todavía". La sesión NO se toca — los ítems tienen que sobrevivir.
 */
async function enviarVentaTienda(sock, { endpoint, payload }) {
    try {
        const { data } = await axios.post(
            endpoint, payload,
            { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 10000 },
        )
        return { ok: true, data }
    } catch (err) {
        if (err.response && err.response.status === 409) {
            const detalles = (err.response.data && err.response.data.sin_tipo) || []
            const totalItems = (payload.items || []).length
            const { texto, opciones } = mensajeSinTipo(detalles, totalItems)
            // Sin sugerencias no hay nada que numerar: armar el pending solo
            // trabaría la carga de ítems de texto libre para siempre (el "1"
            // de un item nunca puede distinguirse del "1" de elegir opción).
            if (opciones.length > 0) {
                orders.setPending(ORDERS_GID, 'sin_tipo', {
                    detalles, opciones, endpoint, envio: payload.envio || 0,
                })
            }
            await sock.sendMessage(ORDERS_GID, { text: texto })
            return { ok: false, sinTipo: detalles }
        }
        logger.error({ err: err.message }, 'Error al crear la venta en Django')
        await sock.sendMessage(ORDERS_GID, { text: '❌ Error al crear el pedido. Intenta de nuevo.' })
        return { ok: false, error: true }
    }
}

async function handleOrdersMessage(sock, msg) {
    const image = msg.message?.imageMessage
    const text = getText(msg)

    // Un pending activo se queda con las respuestas cortas: `parseItemText`
    // acepta un numero suelto como precio, asi que sin esta guarda el "1"
    // con el que alguien elige una opcion entra como un item de $1 y el
    // pending queda trabado para siempre.
    const pendingVivo = orders.getPending(ORDERS_GID)
    const esRespuestaAPending = Boolean(pendingVivo)
        && /^\s*(\d+|otro)\s*$/i.test(text || '')

    // Ítem de tienda: texto libre cuando hay sesión tienda activa
    const tiendaSess = orders.getSession(ORDERS_GID)
    if (tiendaSess && tiendaSess.tipo === 'tienda' && text && !text.startsWith('/') && !esRespuestaAPending) {
        const parsed = parseItemText(text)
        if (parsed) {
            const result = orders.addItem(ORDERS_GID, parsed.description || 'ítem tienda', parsed.price)
            if (result) {
                if (parsed.qty > 1) orders.setQty(ORDERS_GID, result.index, parsed.qty)
                const sess2 = orders.getSession(ORDERS_GID)
                const total = sess2.items.reduce((s, i) => s + i.price * i.qty, 0)
                const desc = parsed.description ? ` — ${parsed.description}` : ''
                await sock.sendMessage(ORDERS_GID, {
                    text: `✅ Ítem ${result.index}: ${parsed.qty}× $${parsed.price}${desc} — Total: $${total} MXN`,
                })
            }
            return
        }
    }

    if (image) {
        const caption = image.caption || ''
        const price = extractPrice(caption)
        if (!price) return
        // Tomar solo la primera línea no vacía del caption para que no se guarde el
        // footer de Ryal ("↪️ Reenvía...") ni texto largo como descripción del ítem.
        const description = caption.split('\n').map(l => l.trim()).find(l => l) || ''
        const costo = Math.max(0, price - MARKUP)
        const result = orders.addItem(ORDERS_GID, description, price, costo)
        if (!result) {
            await sock.sendMessage(ORDERS_GID, {
                text: '⚠️ Sin sesión activa. Usa /pedido Nombre Teléfono para iniciar.',
            })
            return
        }
        const sess = orders.getSession(ORDERS_GID)
        const total = sess.items.reduce((s, i) => s + i.price * i.qty, 0)
        await sock.sendMessage(ORDERS_GID, {
            text: `✅ Ítem ${result.index}: $${price} MXN agregado — Total acumulado: $${total} MXN`,
        })
        return
    }

    // Respuesta numérica suelta → puede resolver pending de conflict o disambig
    // (mismo valor ya leído como `pendingVivo` arriba, para la guarda del ítem de tienda)
    const pending = pendingVivo
    const bareNum = (text && /^\s*\d+\s*$/.test(text)) ? parseInt(text.trim(), 10) : null

    if (pending && pending.type === 'sin_tipo' && /^\s*otro\s*$/i.test(text || '')) {
        orders.clearPending(ORDERS_GID)
        await sock.sendMessage(ORDERS_GID, {
            text: '📋 Cargá el tipo o el alias en /panel/negocio/tipos/ y volvé a mandar /cerrar.'
                + '\nLos artículos siguen cargados.',
        })
        return
    }

    if (pending && bareNum !== null) {
        if (pending.type === 'conflict') {
            const { nombre, telefono } = pending.payload
            orders.clearPending(ORDERS_GID)

            if (bareNum === 1) {
                await sock.sendMessage(ORDERS_GID, { text: '↩️ Continuando con el pedido actual.' })
                return
            }

            if (bareNum === 2) {
                // Cerrar pedido actual en Django y abrir sesión nueva
                const sess = orders.getSession(ORDERS_GID)
                if (sess && sess.items.length > 0) {
                    const closingTienda = sess.tipo === 'tienda'
                    const closeEndpoint = closingTienda
                        ? `${DJANGO_URL}/api/negocio/tienda/`
                        : `${DJANGO_URL}/api/negocio/pedido/`
                    const closePayload = closingTienda
                        ? payloadVentaTienda(sess, 0)
                        : { nombre: sess.cliente.nombre, telefono: sess.cliente.telefono, items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty, costo: i.costo || 0 })), envio: 0, descuento_monto: orders.getDescuento(ORDERS_GID)?.monto || 0, codigo_descuento_id: orders.getDescuento(ORDERS_GID)?.codigoId || null }
                    const resPrev = await enviarVentaTienda(
                        sock, { endpoint: closeEndpoint, payload: closePayload })
                    if (!resPrev.ok) return   // no se abre sesión nueva sobre una venta sin cerrar
                    await sock.sendMessage(ORDERS_GID, {
                        text: `✅ Pedido #${resPrev.data.pedido_id} cerrado — Total: $${resPrev.data.total} MXN`
                            + avisoSinTipo(resPrev.data.sin_tipo),
                    })
                }
                orders.startSession(ORDERS_GID, nombre, telefono, pending.payload.tipo || 'pedido')
                await sock.sendMessage(ORDERS_GID, {
                    text: `📋 Sesión iniciada — ${nombre} (${telefono})\nReenvía fotos con precio para agregar ítems.`,
                })
                return
            }

            if (bareNum === 3) {
                // Cancelar pedido actual y abrir sesión nueva
                orders.cancelSession(ORDERS_GID)
                orders.startSession(ORDERS_GID, nombre, telefono, pending.payload.tipo || 'pedido')
                await sock.sendMessage(ORDERS_GID, {
                    text: `❌ Sesión anterior cancelada.\n📋 Sesión iniciada — ${nombre} (${telefono})\nReenvía fotos con precio para agregar ítems.`,
                })
                return
            }

            // Número fuera de 1-3 → restaurar pending y avisar
            orders.setPending(ORDERS_GID, 'conflict', pending.payload)
            await sock.sendMessage(ORDERS_GID, { text: '⚠️ Responde 1, 2 o 3.' })
            return
        }

        if (pending.type === 'disambig') {
            const results = pending.payload
            if (bareNum < 1 || bareNum > results.length) {
                orders.setPending(ORDERS_GID, 'disambig', results)
                await sock.sendMessage(ORDERS_GID, { text: `⚠️ Responde un número del 1 al ${results.length}.` })
                return
            }
            const elegido = results[bareNum - 1]
            orders.clearPending(ORDERS_GID)

            // Verificar conflicto de sesión después de la selección
            const sesionActiva = orders.getSession(ORDERS_GID)
            if (sesionActiva) {
                const total = sesionActiva.items.reduce((s, i) => s + i.price * i.qty, 0)
                orders.setPending(ORDERS_GID, 'conflict', { nombre: elegido.nombre, telefono: elegido.telefono })
                await sock.sendMessage(ORDERS_GID, {
                    text: `⚠️ Ya hay una sesión abierta — ${sesionActiva.cliente.nombre} (${sesionActiva.items.length} ítem(s), $${total} MXN).\nResponde:\n1️⃣ Continuar con este pedido\n2️⃣ Cerrar este pedido y abrir uno nuevo\n3️⃣ Cancelar y abrir uno nuevo`,
                })
                return
            }

            orders.startSession(ORDERS_GID, elegido.nombre, elegido.telefono)
            await sock.sendMessage(ORDERS_GID, {
                text: `📋 Sesión iniciada — ${elegido.nombre} (${elegido.telefono})\nReenvía fotos con precio para agregar ítems.`,
            })
            return
        }

        if (pending.type === 'disambig_moda') {
            const { clientes, cantidad, ganancia, envio } = pending.payload
            if (bareNum < 1 || bareNum > clientes.length) {
                orders.setPending(ORDERS_GID, 'disambig_moda', pending.payload)
                await sock.sendMessage(ORDERS_GID, { text: `⚠️ Responde un número del 1 al ${clientes.length}.` })
                return
            }
            const elegido = clientes[bareNum - 1]
            orders.clearPending(ORDERS_GID)
            await crearPedidoModa(sock, { nombre: elegido.nombre, telefono: elegido.telefono, cantidad, ganancia, envio })
            return
        }

        if (pending.type === 'sin_tipo') {
            const { detalles, opciones, endpoint, envio } = pending.payload
            if (bareNum < 1 || bareNum > opciones.length) {
                orders.setPending(ORDERS_GID, 'sin_tipo', pending.payload)
                await sock.sendMessage(ORDERS_GID, {
                    text: `⚠️ Responde un número del 1 al ${opciones.length}, o «otro».`,
                })
                return
            }
            const elegido = opciones[bareNum - 1]
            // El pending sobrevive hasta que el POST del alias confirme éxito:
            // si falla y ya lo hubiéramos borrado, el reintento natural del
            // usuario (teclear el número otra vez) caería en el bloque de
            // ítem de tienda y agregaría un artículo fantasma de $1.
            try {
                await axios.post(
                    `${DJANGO_URL}/api/negocio/alias/`,
                    { texto: detalles[0].texto, tipo_id: elegido.tipo_id },
                    { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 10000 },
                )
            } catch (err) {
                logger.error({ err: err.message }, 'Error al crear el alias')
                await sock.sendMessage(ORDERS_GID, { text: '❌ No pude guardar el tipo. Intenta de nuevo.' })
                return
            }
            orders.clearPending(ORDERS_GID)
            await sock.sendMessage(ORDERS_GID, {
                text: `✅ «${detalles[0].texto}» quedó como ${elegido.nombre} (costo $${elegido.costo}).`,
            })
            // Reintento: el payload se arma desde la sesión ACTUAL, no desde
            // el snapshot del 409 — lo que se cargó mientras el pending
            // estaba vivo tiene que ir incluido. Si queda otro texto sin
            // tipo, el servidor vuelve a rechazar con el siguiente y el
            // pending se rearma solo.
            const sesionActual = orders.getSession(ORDERS_GID)
            if (!sesionActual || sesionActual.items.length === 0) {
                await sock.sendMessage(ORDERS_GID, {
                    text: '⚠️ La sesión ya no existe o quedó vacía — no se reintentó la venta.',
                })
                return
            }
            const payload = payloadVentaTienda(sesionActual, envio)
            const res = await enviarVentaTienda(sock, { endpoint, payload })
            if (!res.ok) return
            orders.cancelSession(ORDERS_GID)
            await sock.sendMessage(ORDERS_GID, {
                text: `✅ Pedido #${res.data.pedido_id} creado — Total: $${res.data.total} MXN`,
            })
            return
        }
    }

    if (!text || !text.startsWith('/')) return

    const parts = text.trim().split(/\s+/)
    const cmd = parts[0].toLowerCase()
    const args = parts.slice(1)

    if (cmd === '/ayuda') {
        const sess = orders.getSession(ORDERS_GID)
        const sesionInfo = sess
            ? `📌 Sesión activa: *${sess.cliente.nombre}* (${sess.items.length} ítem(s))\n\n`
            : ''
        await sock.sendMessage(ORDERS_GID, {
            text:
                `${sesionInfo}` +
                `*🤖 Comandos disponibles*\n\n` +
                `*Iniciar sesión:*\n` +
                `/venta — venta en tienda (Mostrador)\n` +
                `/pedido <tel o nombre> — pedido para cliente\n` +
                `/pedido <cliente> moda <cantidad> <ganancia> — venta rápida sin producto (ej. /pedido Victor moda 12 100)\n\n` +
                `*Durante la sesión:*\n` +
                `/items — ver ítems agregados\n` +
                `/quitar <N> — quitar ítem número N\n` +
                `/cant <N> <cantidad> — cambiar cantidad del ítem N\n` +
                `/descuento <CÓDIGO> — aplicar código de descuento\n` +
                `/cerrar — guardar pedido\n` +
                `/cerrar envio=X — guardar con costo de envío\n` +
                `/cancelar — cancelar sin guardar\n\n` +
                `*Consultas:*\n` +
                `/precios — ver tipos de artículo y costos\n` +
                `/ayuda — este menú`,
        })
        return
    }

    if (cmd === '/venta' && !args.length) {
        const sesionActiva = orders.getSession(ORDERS_GID)
        if (sesionActiva) {
            const total = sesionActiva.items.reduce((s, i) => s + i.price * i.qty, 0)
            orders.setPending(ORDERS_GID, 'conflict', { nombre: MOSTRADOR_NOMBRE, telefono: MOSTRADOR_TEL, tipo: 'tienda' })
            await sock.sendMessage(ORDERS_GID, {
                text: `⚠️ Ya hay una sesión abierta — ${sesionActiva.cliente.nombre} (${sesionActiva.items.length} ítem(s), $${total} MXN).\nResponde:\n1️⃣ Continuar con este pedido\n2️⃣ Cerrar este pedido y abrir uno nuevo\n3️⃣ Cancelar y abrir uno nuevo`,
            })
            return
        }
        orders.startSession(ORDERS_GID, MOSTRADOR_NOMBRE, MOSTRADOR_TEL, 'tienda')
        await sock.sendMessage(ORDERS_GID, {
            text: `🏪 *Venta tienda iniciada — Mostrador*\nIngresa ítems: <cantidad> <precio>  o  <descripción> <precio>  o  <cantidad> <descripción> <precio>\n\n📌 _Comandos:_ /items · /quitar <N> · /descuento <CÓDIGO> · /cerrar · /cancelar · /ayuda`,
        })
        return
    }

    if (cmd === '/pedido') {
        if (args.some((a) => a.toLowerCase() === 'moda')) {
            const moda = parseModaArgs(args)
            if (!moda) {
                await sock.sendMessage(ORDERS_GID, {
                    text: 'Uso: /pedido <cliente> moda <cantidad> <ganancia> [envio=X]\nEjemplo: /pedido Victor moda 12 100',
                })
                return
            }
            await resolveClienteYCrearModa(sock, moda)
            return
        }

        const query = args.join(' ').trim()
        if (!query) {
            await sock.sendMessage(ORDERS_GID, {
                text: 'Uso: /pedido <teléfono>  o  /pedido <nombre>\nEjemplo: /pedido 5512345678\nEjemplo: /pedido Juan García',
            })
            return
        }

        const isPhone = /^\d{10,13}$/.test(query.replace(/\s/g, ''))
        const clientes = await buscarCliente(query)

        let clienteNombre, clienteTelefono

        if (isPhone) {
            const digits = query.replace(/\s/g, '')
            if (clientes.length > 0) {
                clienteNombre   = clientes[0].nombre
                clienteTelefono = clientes[0].telefono
            } else {
                // Nuevo cliente — nombre temporal; Bryan puede editarlo desde el panel
                clienteNombre   = `Tel. ${digits}`
                clienteTelefono = digits
            }
        } else {
            if (clientes.length === 0) {
                await sock.sendMessage(ORDERS_GID, {
                    text: `⚠️ No encontré ningún cliente con ese nombre.\nBusca por teléfono (/pedido <número>) o regístralo en el panel.`,
                })
                return
            }
            if (clientes.length > 1) {
                const lines = clientes.map((c, i) => `${i + 1}. ${c.nombre} — ${c.telefono}`)
                orders.setPending(ORDERS_GID, 'disambig', clientes)
                await sock.sendMessage(ORDERS_GID, {
                    text: `🔍 Varios resultados:\n${lines.join('\n')}\nResponde con el número de la opción.`,
                })
                return
            }
            clienteNombre   = clientes[0].nombre
            clienteTelefono = clientes[0].telefono
        }

        // Verificar si hay sesión activa antes de abrir
        const sesionActiva = orders.getSession(ORDERS_GID)
        if (sesionActiva) {
            const total = sesionActiva.items.reduce((s, i) => s + i.price * i.qty, 0)
            orders.setPending(ORDERS_GID, 'conflict', { nombre: clienteNombre, telefono: clienteTelefono })
            await sock.sendMessage(ORDERS_GID, {
                text: `⚠️ Ya hay una sesión abierta — ${sesionActiva.cliente.nombre} (${sesionActiva.items.length} ítem(s), $${total} MXN).\nResponde:\n1️⃣ Continuar con este pedido\n2️⃣ Cerrar este pedido y abrir uno nuevo\n3️⃣ Cancelar y abrir uno nuevo`,
            })
            return
        }

        orders.startSession(ORDERS_GID, clienteNombre, clienteTelefono)
        await sock.sendMessage(ORDERS_GID, {
            text: `📋 Sesión iniciada — ${clienteNombre} (${clienteTelefono})\nReenvía fotos con precio para agregar ítems.`,
        })
        return
    }

    if (cmd === '/items') {
        const sess = orders.getSession(ORDERS_GID)
        if (!sess) {
            await sock.sendMessage(ORDERS_GID, { text: 'Sin sesión activa.' })
            return
        }
        if (sess.items.length === 0) {
            await sock.sendMessage(ORDERS_GID, { text: 'Sin ítems. Reenvía fotos con precio.' })
            return
        }
        const lines = sess.items.map((item, i) =>
            `${i + 1}. ${item.description.slice(0, 40)} — $${item.price} ×${item.qty}`
        )
        const total = sess.items.reduce((s, i) => s + i.price * i.qty, 0)
        lines.push(`\nTotal: $${total} MXN`)
        await sock.sendMessage(ORDERS_GID, { text: lines.join('\n') })
        return
    }

    if (cmd === '/quitar') {
        const idx = parseInt(args[0], 10)
        if (!orders.removeItem(ORDERS_GID, idx)) {
            await sock.sendMessage(ORDERS_GID, { text: `Ítem ${idx} no encontrado.` })
            return
        }
        await sock.sendMessage(ORDERS_GID, { text: `🗑️ Ítem ${idx} eliminado.` })
        return
    }

    if (cmd === '/cant') {
        const idx = parseInt(args[0], 10)
        const qty = parseInt(args[1], 10)
        if (!orders.setQty(ORDERS_GID, idx, qty)) {
            await sock.sendMessage(ORDERS_GID, {
                text: `No se pudo actualizar el ítem ${idx} — índice o cantidad inválidos.`,
            })
            return
        }
        await sock.sendMessage(ORDERS_GID, { text: `✅ Ítem ${idx}: cantidad = ${qty}` })
        return
    }

    if (cmd === '/cancelar') {
        orders.cancelSession(ORDERS_GID)
        await sock.sendMessage(ORDERS_GID, { text: '❌ Sesión cancelada.' })
        return
    }

    if (cmd === '/precios') {
        try {
            const { data } = await axios.get(
                `${DJANGO_URL}/api/negocio/tipos/`,
                { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 5000 },
            )
            if (!data.tipos || data.tipos.length === 0) {
                await sock.sendMessage(ORDERS_GID, {
                    text: '📋 Sin tipos registrados. Agrégalos en el panel → Tipos de artículo.',
                })
                return
            }
            const lines = data.tipos.map(t =>
                `• *${t.nombre}* — costo $${t.costo} MXN\n  _Keywords: ${t.keywords}_`
            )
            await sock.sendMessage(ORDERS_GID, {
                text: `📋 *Tipos de artículo registrados:*\n\n${lines.join('\n\n')}`,
            })
        } catch (err) {
            logger.error({ err: err.message }, '/precios falló')
            await sock.sendMessage(ORDERS_GID, { text: '❌ Error al obtener tipos.' })
        }
        return
    }

    if (cmd === '/descuento') {
        const sess = orders.getSession(ORDERS_GID)
        if (!sess) {
            await sock.sendMessage(ORDERS_GID, { text: '⚠️ Sin sesión activa.' })
            return
        }
        const codigoStr = (parts[1] || '').trim().toUpperCase()
        if (!codigoStr) {
            await sock.sendMessage(ORDERS_GID, { text: 'Uso: /descuento <CODIGO>\nEj: /descuento GORRA50' })
            return
        }
        const descriptions = sess.items.map(i => i.description)
        try {
            const { data } = await axios.post(
                `${DJANGO_URL}/api/negocio/codigos/validar/`,
                { codigo: codigoStr, descriptions },
                { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 5000 },
            )
            if (!data.valido) {
                await sock.sendMessage(ORDERS_GID, { text: `❌ ${data.mensaje}` })
                return
            }
            orders.setDescuento(ORDERS_GID, codigoStr, data.descuento, data.codigo_id)
            const totalBruto = sess.items.reduce((s, i) => s + i.price * i.qty, 0)
            const totalNeto = Math.max(0, totalBruto - data.descuento)
            await sock.sendMessage(ORDERS_GID, {
                text: `✅ ${data.mensaje}\nTotal: $${totalBruto} − $${data.descuento} = *$${totalNeto} MXN*`,
            })
        } catch (err) {
            logger.error({ err: err.message }, '/descuento codigos/validar falló')
            await sock.sendMessage(ORDERS_GID, { text: '❌ Error al validar el código.' })
        }
        return
    }

    if (cmd === '/cerrar') {
        const sess = orders.getSession(ORDERS_GID)
        if (!sess) {
            await sock.sendMessage(ORDERS_GID, { text: 'Sin sesión activa.' })
            return
        }
        if (sess.items.length === 0) {
            await sock.sendMessage(ORDERS_GID, { text: 'Sin ítems en la sesión. Reenvía fotos primero.' })
            return
        }
        const envioArg = args.find(a => /^envio=\d+(\.\d+)?$/.test(a))
        const envio = envioArg ? parseFloat(envioArg.split('=')[1]) : 0
        const isTienda = sess.tipo === 'tienda'
        const endpoint = isTienda
            ? `${DJANGO_URL}/api/negocio/tienda/`
            : `${DJANGO_URL}/api/negocio/pedido/`
        const payload = isTienda
            ? payloadVentaTienda(sess, envio)
            : {
                nombre: sess.cliente.nombre,
                telefono: sess.cliente.telefono,
                items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty, costo: i.costo || 0 })),
                envio,
                descuento_monto: orders.getDescuento(ORDERS_GID)?.monto || 0,
                codigo_descuento_id: orders.getDescuento(ORDERS_GID)?.codigoId || null,
            }
        const res = await enviarVentaTienda(sock, { endpoint, payload })
        if (!res.ok) return          // 409 o error: la sesión queda intacta
        orders.cancelSession(ORDERS_GID)
        await sock.sendMessage(ORDERS_GID, {
            text: `✅ Pedido #${res.data.pedido_id} creado — Total: $${res.data.total} MXN`
                + avisoSinTipo(res.data.sin_tipo),
        })
        return
    }
}


async function connect() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

    const sock = makeWASocket({
        auth:    state,
        version: waVersion,
        logger:  pino({ level: 'warn' }),
        // Baileys no trae default de qrTimeout, así que aplica su fallback:
        // 60s el primer ref y 20s los siguientes (socket.js:709,723). Con 6
        // refs eso mata el socket a los 2m46s — medido 26 veces seguidas el
        // 09-07.
        //
        // En modo QR se fijan los 60s del PRIMER ref para todos: es la vida
        // que Baileys ya le da a un ref recién llegado, y la misma cadencia
        // con la que WhatsApp Web refresca su propio QR. 20s no alcanzan para
        // llevar un QR de una terminal a un teléfono.
        //
        // En modo código el socket tiene que sobrevivir mientras alguien
        // teclea 8 caracteres; si muere, el código deja de servir.
        qrTimeout: PAIR_NUMBER ? 10 * 60 * 1000 : 60 * 1000,
        // Ping más frecuente que el default (30s): detecta antes la conexión
        // muerta y mantiene vivo el NAT durante los reenvíos de lotes pesados.
        keepAliveIntervalMs: 20000,
        // Baileys manda presencia 'available' al conectar por default
        // (Defaults/index.js: markOnlineOnConnect: true → chats.js: sendPresenceUpdate).
        // Con la cuenta marcada online desde este dispositivo vinculado, WhatsApp deja
        // de enviar la notificación push al teléfono: el mensaje llega al chat pero el
        // teléfono nunca avisa. 'unavailable' devuelve las notificaciones al teléfono
        // sin afectar la recepción ni el envío del bot.
        markOnlineOnConnect: false,
        getMessage: async (key) => sentMsgCache.get(key?.id),
        // APAGADO 2026-09-01. Estaba en `true` para sembrar completo el filtro
        // anti-spam de bienvenida: sin eso WA solo manda un snapshot de unos
        // pocos chats en messaging-history.set (verificado: 8-13 chats en un
        // número con muchos más contactos reales) y clientes viejos recibían la
        // bienvenida de nuevo.
        //
        // El costo resultó más caro que el beneficio: con `true`, CADA reconexión
        // arranca una sincronización completa de historial, y en esta cuenta
        // ninguna terminaba — 113 desconexiones en 7 días (69 `Stream Errored
        // (ack)` 500) y un `Timeout in AwaitingInitialSync` detrás de cada
        // reconexión, sin excepción. Las claves de grupo que los participantes
        // reparten dentro de esas ventanas se pierden, y el bot se queda sin la
        // sender key de esos participantes: sus mensajes en un grupo mueren en
        // `No session found to decrypt message` mientras el privado funciona.
        //
        // El sembrado incompleto solo afecta al próximo login por QR, y se paga
        // con alguna bienvenida repetida. Perder comandos no se paga con nada.
        syncFullHistory: false,
    })
    currentSock = sock

    sock.ev.on('creds.update', saveCreds)

    // Sin sembrado de historial: esta instancia no manda bienvenidas, así que
    // no necesita saber qué chats ya existían. El welcome store no existe acá
    // a propósito — ver el encabezado del archivo.

    sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
        // printQRInTerminal esta deprecado en Baileys >=6.6 — renderizamos el QR manualmente
        if (qr) {
            // El `qr` es la señal de que el socket está abierto y WhatsApp ya
            // mandó los refs de emparejamiento: el momento exacto en que
            // requestPairingCode puede mandar su nodo, sin sleeps a ojo.
            if (debePedirCodigo({ creds: state.creds, numero: PAIR_NUMBER, yaPedido: codigoYaPedido })) {
                codigoYaPedido = true
                sock.requestPairingCode(PAIR_NUMBER)
                    .then((codigo) => {
                        logger.info({ codigo, numero: PAIR_NUMBER },
                            'Código de vinculación — en el teléfono: Dispositivos vinculados → Vincular con número de teléfono')
                        writeQrState(QR_STATE_FILE, 'pairing', null)
                    })
                    .catch((err) => {
                        // Que vuelva a intentar con el próximo qr: si esto se
                        // queda pegado en true, el proceso se convierte en el
                        // bucle de QR que veníamos a eliminar.
                        codigoYaPedido = false
                        logger.error({ err: err.message }, 'requestPairingCode falló — se reintenta con el próximo QR')
                    })
                return
            }
            if (PAIR_NUMBER) return  // en modo código el QR no es el camino: no ensuciar el journal
            qrcode.generate(qr, { small: true })
            logger.info('Escanear el QR con WhatsApp → Dispositivos vinculados → Vincular dispositivo')
            writeQrState(QR_STATE_FILE, 'qr', qr)
        }
        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode
            if (code !== DisconnectReason.loggedOut) {
                logger.info({ code, motivo: lastDisconnect?.error?.message }, 'Desconectado — reconectando en 5s...')
                writeQrState(QR_STATE_FILE, 'close', null)
                setTimeout(connect, 5000)
            } else {
                // loggedOut: la sesión murió. Salir con 0 para que systemd
                // (Restart=on-failure) NO reinicie en bucle generando QR en los logs.
                // Requiere re-login manual: borrar .baileys_auth/ y re-escanear QR.
                logger.error('Sesión cerrada (loggedOut). Borra .baileys_auth/ y re-escanea el QR. El servicio NO se reinicia solo.')
                writeQrState(QR_STATE_FILE, 'logged_out', null)
                process.exit(0)
            }
        } else if (connection === 'open') {
            logger.info('Bot conectado ✓')
            writeQrState(QR_STATE_FILE, 'open', null)
        }
    })

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        // Cachear TODO mensaje propio (llega como 'append') para servir retry receipts
        for (const m of messages) {
            if (m.key?.fromMe && m.message) cacheSentMessage(m.key, m.message)
        }

        if (type !== 'notify') return

        for (const msg of messages) {
            if (!msg.message) continue

            // Una sola regla de entrada, probada en routing.test.js: solo los
            // mensajes PROPIOS del Grupo Pedidos. El porqué está en routing.js.
            if (!shouldHandleOrders(msg, ORDERS_GID)) continue

            try {
                await handleOrdersMessage(sock, msg)
            } catch (err) {
                logger.error({ err: err.message }, 'Error procesando mensaje')
            }
        }
    })
}

// No tumbar el proceso por una promesa sin manejar; dejar registro y seguir vivo
process.on('unhandledRejection', (err) => {
    logger.error({ err: err?.message || String(err) }, 'unhandledRejection')
})


async function main() {
    // Toma el lock de la sesion ANTES de conectar: si otro proceso ya usa este
    // .baileys_auth (p.ej. el servicio systemd), aborta en vez de invalidar el login.
    acquireAuthLock(AUTH_DIR)

    // Un PAIR_NUMBER escrito mal cae al QR en silencio, y el silencio es justo
    // lo que hizo perder la tarde del 09-07: hay que verlo en el journal.
    if (PAIR_NUMBER_RAW && !PAIR_NUMBER) {
        logger.warn({ PAIR_NUMBER_RAW }, 'PAIR_NUMBER no se pudo normalizar — sigo con QR')
    } else if (PAIR_NUMBER) {
        logger.info({ numero: PAIR_NUMBER }, 'Vinculación por CÓDIGO activada — el QR queda desactivado')
    }

    // Sin servidor de avisos (es de persona1) ni sweeper de lotes: esta
    // instancia no reenvía imágenes del proveedor, así que no hay lotes.

    const baileys = await import('@whiskeysockets/baileys')
    makeWASocket          = baileys.default
    useMultiFileAuthState = baileys.useMultiFileAuthState
    DisconnectReason      = baileys.DisconnectReason
    downloadMediaMessage  = baileys.downloadMediaMessage
    try {
        waVersion = (await baileys.fetchLatestBaileysVersion()).version
        logger.info({ waVersion: waVersion.join('.') }, 'Versión de WA Web')
    } catch (e) {
        logger.warn({ err: e.message }, 'No se pudo obtener la versión de WA — uso la default')
    }
    await connect()
}

// Solo arranca cuando se ejecuta como `node bot.js`; al requerirlo desde los
// tests el módulo se carga sin conectar a WhatsApp.
if (require.main === module) main()

module.exports = { handleOrdersMessage, enviarVentaTienda, orders }
