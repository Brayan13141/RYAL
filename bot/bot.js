const axios = require('axios')
const pino = require('pino')
const qrcode = require('qrcode-terminal')
require('dotenv').config()

const { extractPrice, buildRyalForward, buildImageCaption, markupCaption, cleanCaption, computeTotal } = require('./utils')
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
        const descripcion = cleanCaption(markupCaption(text, MARKUP))
        await sock.sendMessage(RYAL_GID, { text: descripcion })
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


async function handleOrdersMessage(sock, msg) {
    const image = msg.message?.imageMessage
    const text = getText(msg)

    if (image) {
        const caption = image.caption || ''
        const price = extractPrice(caption)
        if (!price) return
        const result = orders.addItem(ORDERS_GID, caption, price)
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

    if (!text || !text.startsWith('/')) return

    const parts = text.trim().split(/\s+/)
    const cmd = parts[0].toLowerCase()
    const args = parts.slice(1)

    if (cmd === '/pedido') {
        const telefono = args[args.length - 1]
        const nombre = args.slice(0, -1).join(' ')
        if (!nombre || !telefono || !/^\d{7,15}$/.test(telefono)) {
            await sock.sendMessage(ORDERS_GID, { text: 'Uso: /pedido Nombre Teléfono\nEjemplo: /pedido Bryan Sanchez 5512345678' })
            return
        }
        orders.startSession(ORDERS_GID, nombre, telefono)
        await sock.sendMessage(ORDERS_GID, {
            text: `📋 Sesión iniciada — ${nombre} (${telefono})\nReenvía fotos con precio para agregar ítems.`,
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
        try {
            const { data } = await axios.post(
                `${DJANGO_URL}/api/negocio/pedido/`,
                {
                    nombre: sess.cliente.nombre,
                    telefono: sess.cliente.telefono,
                    items: sess.items.map(i => ({
                        description: i.description,
                        price: i.price,
                        qty: i.qty,
                    })),
                    envio,
                },
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
            if (msg.key.fromMe || !msg.message) continue

            const jid     = msg.key.remoteJid
            const isGroup = jid?.endsWith('@g.us')

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
