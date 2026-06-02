const axios = require('axios')
const pino = require('pino')
const qrcode = require('qrcode-terminal')
require('dotenv').config()

const { extractPrice, buildRyalForward, computeTotal } = require('./utils')

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

const logger = pino({ level: 'info' })


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


async function handleSupplierMessage(sock, msg) {
    if (!FORWARD_TO_RYAL) return

    const image = msg.message?.imageMessage
    if (!image) return

    const caption   = image.caption || ''
    const origPrice = extractPrice(caption)

    if (!origPrice) {
        logger.info('Mensaje del proveedor sin precio detectado — no se reenvía')
        return
    }

    // Marca TODOS los precios (+MARKUP), limpia el mensaje y le pone el pie de Ryal
    const newCaption = buildRyalForward(caption, MARKUP)

    // reuploadRequest permite recuperar media cuyo cifrado expiró (mensajes no tan frescos)
    const buffer = await downloadMediaMessage(
        msg, 'buffer', {},
        { logger, reuploadRequest: sock.updateMediaMessage }
    )

    await sock.sendMessage(RYAL_GID, {
        image:    buffer,
        caption:  newCaption,
        mimetype: image.mimetype || 'image/jpeg',
    })

    logger.info({ origPrice, newPrice: origPrice + MARKUP }, 'Reenviado al Grupo Ryal')
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


async function connect() {
    const { state, saveCreds } = await useMultiFileAuthState('.baileys_auth')

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
