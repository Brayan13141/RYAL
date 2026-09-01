const axios = require('axios')
const pino = require('pino')
const qrcode = require('qrcode-terminal')
const http = require('http')
require('dotenv').config()

// Endpoint local (solo 127.0.0.1) para que el watchdog de otros bots pida
// mandar un aviso de WhatsApp reusando esta conexión ya viva, sin abrir una
// sesión nueva (evita repetir el mismo desajuste de sesión que se está avisando).
const NOTIFY_PORT = parseInt(process.env.NOTIFY_PORT || '8952')
const ALERT_JID = process.env.ALERT_JID || '5214451129186@s.whatsapp.net'
let currentSock = null

const { extractPrice, buildRyalForward, buildImageCaption, markupCaption, cleanCaption, computeTotal, parseModaArgs } = require('./utils')
const { createBatchBuffer, MAX_PER_GROUP } = require('./batchBuffer')
const { acquireAuthLock } = require('./lock')
const { createOrderSessionStore } = require('./orderSession')
const { WELCOME_MESSAGE, menuReply, isGreetableJid, createWelcomeStore } = require('./welcome')
const { writeQrState } = require('./qrState')
const { resolveNotifyJid } = require('./notifyTarget')
const { matchPromo } = require('./promos')
const { avisoSinTipo } = require('./avisoSinTipo')
const { mensajeSinTipo } = require('./ventaSinTipo')
const { describeUndecryptable } = require('./undecryptable')

const AUTH_DIR = '.baileys_auth'
const QR_STATE_FILE = '.qr_state.json'

// Baileys es ESM-only (>=6.7.x) → se carga con import() dinámico desde este
// módulo CommonJS; se asignan en main() antes de connect().
let makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage
let waVersion   // versión de WA Web (sin esto WhatsApp rechaza y cae en loop)

const MARKUP          = parseInt(process.env.MARKUP || '100')
const SUPPLIER_GID    = process.env.SUPPLIER_GROUP_ID
const RYAL_GID        = process.env.RYAL_GROUP_ID
const FORWARD_TO_RYAL = process.env.FORWARD_TO_RYAL === 'true'
const DJANGO_URL      = process.env.DJANGO_API_URL || 'http://localhost:8000'
const DJANGO_KEY      = process.env.DJANGO_API_KEY
const ORDERS_GID = process.env.ORDERS_GROUP_ID  // undefined → feature deshabilitada

const logger = pino({ level: 'info' })
const batch = createBatchBuffer()
const orders = createOrderSessionStore()
// JIDs privados ya saludados — persiste junto a la sesión de esta instancia
// Numeros propios que NUNCA reciben bienvenida ni menu. Va por LID exacto y
// no por telefono: en un chat privado la key del mensaje trae solo el `@lid`
// (verificado: {remoteJid:'154211253772535@lid', fromMe, id}) y el numero no
// aparece por ningun lado.
const INTERNAL_JIDS = new Set(
    (process.env.INTERNAL_JIDS || '').split(',').map(s => s.trim()).filter(Boolean))

const welcome = createWelcomeStore({ filePath: '.welcome_seen.json' })
if (welcome.isSealed()) {
    logger.error({},
        'welcome store ILEGIBLE: no se saluda a nadie para no repetir cientos de ' +
        'bienvenidas. Sembrar con markSeenBulk() o restaurar .welcome_seen.json')
}

// Pausa entre imágenes al reenviar un lote: sin ella, hasta 50 descargas+resubidas
// en ráfaga saturan el socket (keepalive perdido → 408 / stream errored → reconexión,
// que dispara "Sincronizando..." en el teléfono vinculado).
const PACE_BETWEEN_IMAGES_MS = 400

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

async function flushBatch(sock, text, price) {
    const finalPrice = price + MARKUP
    const imageCaption = buildImageCaption(finalPrice)
    const items = batch.flush(SUPPLIER_GID)
    logger.info({ count: items.length, finalPrice }, 'Enviando lote al Grupo Ryal')

    let sentCount = 0
    for (const item of items) {
        try {
            const img = item.message?.imageMessage
            const buf = await downloadMediaMessage(
                item, 'buffer', {},
                { logger, reuploadRequest: sock.updateMediaMessage }
            )
            await sock.sendMessage(RYAL_GID, {
                image: buf,
                caption: imageCaption,
                mimetype: img?.mimetype || 'image/jpeg',
            })
            sentCount++
            await new Promise(r => setTimeout(r, PACE_BETWEEN_IMAGES_MS))
        } catch (err) {
            logger.error({ err: err.message }, 'No se pudo reenviar una imagen del lote — se omite')
        }
    }

    if (sentCount === 0) {
        logger.warn({ count: items.length }, 'Ninguna imagen del lote se pudo reenviar — no se envía la descripción')
        return
    }

    try {
        await sock.sendMessage(RYAL_GID, { text: buildRyalForward(text, MARKUP) })
    } catch (err) {
        logger.error({ err: err.message }, 'No se pudo enviar la descripción del lote')
    }
    logger.info({ sentCount }, 'Lote reenviado completo')
}

async function handleSupplierMessage(sock, msg) {
    if (!FORWARD_TO_RYAL) return

    // Los videos no se reenvían, pero son parte del mismo producto que las fotos
    // (el proveedor manda fotos → video → precio): el lote pendiente se conserva
    // para que el precio que viene después sí lo reenvíe.
    if (msg.message?.videoMessage) {
        logger.info({ pending: batch.size(SUPPLIER_GID) }, 'Video del proveedor — ignorado, el lote sigue esperando precio')
        return
    }

    const image = msg.message?.imageMessage
    if (image) {
        const caption   = image.caption || ''
        const price     = extractPrice(caption)
        const buffPrice = batch.getPrice(SUPPLIER_GID)

        // Si el precio cambió y hay imágenes acumuladas → flush del lote anterior.
        if (price && buffPrice !== null && price !== buffPrice && batch.size(SUPPLIER_GID) > 0) {
            logger.info({ prevPrice: buffPrice, newPrice: price }, 'Precio cambió — flush automático del lote anterior')
            await flushBatch(sock, batch.getCaption(SUPPLIER_GID), buffPrice)
        }

        const before = batch.size(SUPPLIER_GID)
        batch.addImage(SUPPLIER_GID, msg, Date.now(), price, caption)
        const buffered = batch.size(SUPPLIER_GID)
        // addImage refresca el TTL aunque el cap rechace la imagen, así que el
        // lote no expira mientras el proveedor siga mandando.
        if (buffered === before) {
            logger.warn({ buffered, max: MAX_PER_GROUP }, 'Buffer de lote lleno — imagen ignorada')
            return
        }
        logger.info({ buffered, price }, 'Imagen buffereada')
        return
    }

    // No es imagen → ¿es el mensaje de precio del lote?
    const text = getText(msg)
    const price = extractPrice(text)
    if (!price) return
    if (batch.size(SUPPLIER_GID) === 0) {
        logger.info('Precio recibido sin lote pendiente — ignorado')
        return
    }
    await flushBatch(sock, text, price)
}


// Comandos de promoción del Grupo Ryal (ver bot/promos.js).
//
// Solo se atienden mensajes PROPIOS. Eso ES la autorización: un mensaje
// fromMe únicamente puede existir si salió del teléfono de esta instancia,
// así que un cliente del grupo no puede disparar el anuncio. De paso resuelve
// el duplicado — las dos instancias (persona1 y persona2) están en este grupo,
// pero solo la dueña del número desde el que se escribe lo ve como propio.
async function handleRyalMessage(sock, msg) {
    if (!msg.key.fromMe) return

    const promo = matchPromo(getText(msg))
    if (!promo) return

    await sock.sendMessage(RYAL_GID, { text: promo })
    logger.info({ cmd: getText(msg).trim().toLowerCase() }, 'Promo publicada en el Grupo Ryal')
}


async function handleClientMessage(sock, msg) {
    // La instancia de reenvío proveedor→Ryal (FORWARD_TO_RYAL=true, 4439728793) no
    // atiende chats privados en absoluto: su único trabajo es reenviar imágenes
    // del Grupo Proveedor al Grupo Ryal (handleSupplierMessage).
    if (FORWARD_TO_RYAL) return

    const jid = msg.key.remoteJid

    // Bienvenida + menú para clientes nuevos (primer chat privado con este número)
    if (isGreetableJid(jid, INTERNAL_JIDS) && !welcome.hasSeen(jid)) {
        welcome.markSeen(jid)
        try {
            await sock.sendMessage(jid, { text: WELCOME_MESSAGE })
            logger.info({ jid }, 'Bienvenida enviada a cliente nuevo')
        } catch (err) {
            logger.error({ err: err.message, jid }, 'No se pudo enviar la bienvenida')
        }
        // sin return: si su primer mensaje ya es una imagen cotizable, también se cotiza
    }

    // Respuestas del menú (1/2/3 o "menu") — solo texto exacto, no interfiere
    // con precios/tallas porque esos nunca son un solo dígito 1-3
    const option = menuReply(getText(msg))
    if (option) {
        await sock.sendMessage(jid, { text: option })
        return
    }

    const image = msg.message?.imageMessage
    if (!image) return

    const isForwarded = image.contextInfo?.isForwarded
    if (!isForwarded) return

    const text  = image.caption || ''
    const price = extractPrice(text)
    if (!price) return

    const telefono = msg.key.remoteJid.replace('@s.whatsapp.net', '')
    const descuento = await getDescuento(telefono)
    const total = computeTotal(price, descuento)

    await sock.sendMessage(msg.key.remoteJid, {
        text: `Total: $${total} MXN`,
    })

    logger.info({ telefono, price, descuento, total }, 'Precio enviado')
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
        // reparten dentro de esas ventanas se pierden, y así el bot quedó sin la
        // sender key del 4451076015: sus mensajes en el Grupo Pedidos morían en
        // `No session found to decrypt message` mientras el privado funcionaba.
        //
        // El sembrado incompleto solo afecta al próximo login por QR, y se paga
        // con alguna bienvenida repetida. Perder comandos no se paga con nada.
        syncFullHistory: false,
    })
    currentSock = sock

    sock.ev.on('creds.update', saveCreds)

    // WA reenvía el historial de chats al conectar — solo se sembra de verdad
    // en un login QR nuevo (verificado 2026-07-11: un `systemctl restart` con
    // .baileys_auth ya guardado NO dispara este evento con datos útiles).
    // Se usa para NO mandar la bienvenida a números que ya tenían conversación
    // con este WhatsApp antes de que existiera el bot. Con syncFullHistory
    // puede llegar en varios chunks — markSeenBulk acumula, no pisa.
    sock.ev.on('messaging-history.set', ({ chats }) => {
        const jids = (chats || [])
            .map(c => c.id)
            .filter(isGreetableJid)
        if (jids.length === 0) return
        welcome.markSeenBulk(jids)
        logger.info({ count: jids.length }, 'Chats existentes sembrados en welcome store — no recibirán bienvenida')
    })

    sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
        // printQRInTerminal esta deprecado en Baileys >=6.6 — renderizamos el QR manualmente
        if (qr) {
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
            if (!msg.message) {
                // Sin `message` no hay nada que procesar, pero si venía de un grupo
                // que el bot atiende hay que dejar rastro: es un comando perdido.
                const noDescifrado = describeUndecryptable(
                    msg, { orders: ORDERS_GID, supplier: SUPPLIER_GID, ryal: RYAL_GID })
                if (noDescifrado) {
                    logger.warn(noDescifrado,
                        'Mensaje no descifrado en un grupo operativo — ignorado (falta su sender key)')
                }
                continue
            }

            const jid     = msg.key.remoteJid
            const isGroup = jid?.endsWith('@g.us')

            // fromMe = true cuando el dueño del número manda desde su teléfono personal.
            // Lo permitimos solo en ORDERS_GID para que pueda dar comandos desde su propio número.
            const isOwnerOrdersMsg = msg.key.fromMe && ORDERS_GID && isGroup && jid === ORDERS_GID
            // Los comandos de promo se disparan desde el propio teléfono, así que
            // los mensajes propios del Grupo Ryal también tienen que pasar.
            const isOwnerRyalMsg = msg.key.fromMe && RYAL_GID && isGroup && jid === RYAL_GID
            if (msg.key.fromMe && !isOwnerOrdersMsg && !isOwnerRyalMsg) continue

            try {
                if (isGroup && jid === SUPPLIER_GID) {
                    await handleSupplierMessage(sock, msg)
                } else if (ORDERS_GID && isGroup && jid === ORDERS_GID) {
                    await handleOrdersMessage(sock, msg)
                } else if (RYAL_GID && isGroup && jid === RYAL_GID) {
                    await handleRyalMessage(sock, msg)
                } else if (!isGroup) {
                    await handleClientMessage(sock, msg)
                }
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

function startNotifyServer() {
    const server = http.createServer((req, res) => {
        if (req.method !== 'POST' || req.url !== '/notify') {
            res.writeHead(404).end()
            return
        }
        let body = ''
        req.on('data', (chunk) => { body += chunk })
        req.on('end', async () => {
            let message, target
            try {
                const parsed = JSON.parse(body || '{}')
                message = parsed.message
                target = parsed.target
            } catch (e) {
                res.writeHead(400).end('JSON inválido')
                return
            }
            if (!message) {
                res.writeHead(400).end('Falta "message"')
                return
            }
            if (!currentSock) {
                res.writeHead(503).end('Bot aún no conectado')
                return
            }
            const { jid, error } = resolveNotifyJid(target, { ordersGid: ORDERS_GID, alertJid: ALERT_JID })
            if (error) {
                res.writeHead(503).end('Grupo de pedidos no configurado en esta instancia')
                return
            }
            try {
                await currentSock.sendMessage(jid, { text: message })
                res.writeHead(200).end('ok')
            } catch (err) {
                logger.error({ err: err.message, jid, target }, 'Error enviando aviso (/notify)')
                res.writeHead(500).end('Error al enviar')
            }
        })
    })
    server.on('error', (err) => {
        logger.error({ err: err.message, port: NOTIFY_PORT }, 'No se pudo levantar el servidor de avisos')
    })
    server.listen(NOTIFY_PORT, '127.0.0.1', () => {
        logger.info({ port: NOTIFY_PORT }, 'Servidor de avisos (watchdog) escuchando en localhost')
    })
}

async function main() {
    // Toma el lock de la sesion ANTES de conectar: si otro proceso ya usa este
    // .baileys_auth (p.ej. el servicio systemd), aborta en vez de invalidar el login.
    acquireAuthLock(AUTH_DIR)

    startNotifyServer()

    // Descarta lotes que nunca recibieron precio (TTL 5 min).
    const sweeper = setInterval(() => {
        const dropped = batch.purgeExpired(Date.now())
        if (dropped > 0) logger.warn({ dropped }, 'Lote(s) expirado(s) sin precio — imágenes descartadas')
    }, 60 * 1000)
    sweeper.unref()

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

module.exports = { handleSupplierMessage, handleOrdersMessage, batch, avisoSinTipo, enviarVentaTienda, orders }
