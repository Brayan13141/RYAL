const axios = require('axios')
const pino = require('pino')
const qrcode = require('qrcode-terminal')
require('dotenv').config()

const { extractPrice, buildRyalForward, buildImageCaption, markupCaption, cleanCaption, computeTotal, parseModaArgs } = require('./utils')
const { createBatchBuffer, MAX_PER_GROUP } = require('./batchBuffer')
const { acquireAuthLock } = require('./lock')
const { createOrderSessionStore } = require('./orderSession')

const AUTH_DIR = '.baileys_auth'

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

    // Videos y su respectivo mensaje de precio no se reenvían
    if (msg.message?.videoMessage) {
        const dropped = batch.flush(SUPPLIER_GID)
        if (dropped.length > 0) logger.warn({ count: dropped.length }, 'Video del proveedor — lote previo descartado')
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

        if (batch.size(SUPPLIER_GID) >= MAX_PER_GROUP) {
            logger.warn('Buffer de lote lleno (>=50) — imagen ignorada')
            return
        }
        batch.addImage(SUPPLIER_GID, msg, Date.now(), price, caption)
        logger.info({ buffered: batch.size(SUPPLIER_GID), price }, 'Imagen buffereada')
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


async function handleClientMessage(sock, msg) {
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

async function handleOrdersMessage(sock, msg) {
    const image = msg.message?.imageMessage
    const text = getText(msg)

    // Ítem de tienda: texto libre cuando hay sesión tienda activa
    const tiendaSess = orders.getSession(ORDERS_GID)
    if (tiendaSess && tiendaSess.tipo === 'tienda' && text && !text.startsWith('/')) {
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
    const pending = orders.getPending(ORDERS_GID)
    const bareNum = (text && /^\s*\d+\s*$/.test(text)) ? parseInt(text.trim(), 10) : null

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
                        ? { items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty })), envio: 0 }
                        : { nombre: sess.cliente.nombre, telefono: sess.cliente.telefono, items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty, costo: i.costo || 0 })), envio: 0, descuento_monto: orders.getDescuento(ORDERS_GID)?.monto || 0, codigo_descuento_id: orders.getDescuento(ORDERS_GID)?.codigoId || null }
                    try {
                        const { data } = await axios.post(
                            closeEndpoint, closePayload,
                            { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 10000 },
                        )
                        await sock.sendMessage(ORDERS_GID, { text: `✅ Pedido #${data.pedido_id} cerrado — Total: $${data.total} MXN` })
                    } catch (err) {
                        logger.error({ err: err.message }, 'Error al cerrar pedido anterior (opción 2)')
                        await sock.sendMessage(ORDERS_GID, { text: '❌ Error al cerrar el pedido anterior. Usa /cerrar manualmente primero.' })
                        return
                    }
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
            ? {
                items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty })),
                envio,
            }
            : {
                nombre: sess.cliente.nombre,
                telefono: sess.cliente.telefono,
                items: sess.items.map(i => ({ description: i.description, price: i.price, qty: i.qty, costo: i.costo || 0 })),
                envio,
                descuento_monto: orders.getDescuento(ORDERS_GID)?.monto || 0,
                codigo_descuento_id: orders.getDescuento(ORDERS_GID)?.codigoId || null,
            }
        try {
            const { data } = await axios.post(
                endpoint, payload,
                { headers: { Authorization: `Bearer ${DJANGO_KEY}` }, timeout: 10000 },
            )
            orders.cancelSession(ORDERS_GID)
            await sock.sendMessage(ORDERS_GID, {
                text: `✅ Pedido #${data.pedido_id} creado — Total: $${data.total} MXN`,
            })
        } catch (err) {
            logger.error({ err: err.message }, 'Error al crear pedido en Django')
            await sock.sendMessage(ORDERS_GID, { text: '❌ Error al crear el pedido. Intenta de nuevo.' })
        }
        return
    }
}


async function connect() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

    const sock = makeWASocket({
        auth:    state,
        version: waVersion,
        logger:  pino({ level: 'warn' }),
    })

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
        // printQRInTerminal esta deprecado en Baileys >=6.6 — renderizamos el QR manualmente
        if (qr) {
            qrcode.generate(qr, { small: true })
            logger.info('Escanear el QR con WhatsApp → Dispositivos vinculados → Vincular dispositivo')
        }
        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode
            if (code !== DisconnectReason.loggedOut) {
                logger.info({ code, motivo: lastDisconnect?.error?.message }, 'Desconectado — reconectando en 5s...')
                setTimeout(connect, 5000)
            } else {
                // loggedOut: la sesión murió. Salir con 0 para que systemd
                // (Restart=on-failure) NO reinicie en bucle generando QR en los logs.
                // Requiere re-login manual: borrar .baileys_auth/ y re-escanear QR.
                logger.error('Sesión cerrada (loggedOut). Borra .baileys_auth/ y re-escanea el QR. El servicio NO se reinicia solo.')
                process.exit(0)
            }
        } else if (connection === 'open') {
            logger.info('Bot conectado ✓')
        }
    })

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return

        for (const msg of messages) {
            if (!msg.message) continue

            const jid     = msg.key.remoteJid
            const isGroup = jid?.endsWith('@g.us')

            // fromMe = true cuando el dueño del número manda desde su teléfono personal.
            // Lo permitimos solo en ORDERS_GID para que pueda dar comandos desde su propio número.
            const isOwnerOrdersMsg = msg.key.fromMe && ORDERS_GID && isGroup && jid === ORDERS_GID
            if (msg.key.fromMe && !isOwnerOrdersMsg) continue

            try {
                if (isGroup && jid === SUPPLIER_GID) {
                    await handleSupplierMessage(sock, msg)
                } else if (ORDERS_GID && isGroup && jid === ORDERS_GID) {
                    await handleOrdersMessage(sock, msg)
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

async function main() {
    // Toma el lock de la sesion ANTES de conectar: si otro proceso ya usa este
    // .baileys_auth (p.ej. el servicio systemd), aborta en vez de invalidar el login.
    acquireAuthLock(AUTH_DIR)

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

main()
