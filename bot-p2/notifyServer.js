const { resolveNotifyJid } = require('./notifyTarget')
const { MAX_NOTIFY_BODY, isValidToken } = require('./notifyAuth')

// Handler de POST /notify. `customer` (avisos a clientes) exige X-Notify-Token;
// `orders` y el target por defecto (watchdog) siguen sin token: escuchan solo en 127.0.0.1.
function createNotifyHandler({ getSock, ordersGid, alertJid, notifyToken, logger }) {
    return (req, res) => {
        if (req.method !== 'POST' || req.url !== '/notify') {
            res.writeHead(404).end()
            return
        }
        let body = ''
        let tooLarge = false
        req.on('data', (chunk) => {
            if (tooLarge) return
            body += chunk
            if (Buffer.byteLength(body) > MAX_NOTIFY_BODY) {
                tooLarge = true
                body = ''
            }
        })
        req.on('end', async () => {
            if (tooLarge) {
                res.writeHead(413).end('Body demasiado grande')
                return
            }
            let message, target, phone
            try {
                const parsed = JSON.parse(body || '{}')
                message = parsed.message
                target = parsed.target
                phone = parsed.phone
            } catch (e) {
                res.writeHead(400).end('JSON inválido')
                return
            }
            if (!message) {
                res.writeHead(400).end('Falta "message"')
                return
            }
            if (target === 'customer') {
                if (!notifyToken) {
                    res.writeHead(503).end('NOTIFY_TOKEN no configurado en esta instancia')
                    return
                }
                if (!isValidToken(req.headers['x-notify-token'], notifyToken)) {
                    res.writeHead(401).end('Token inválido')
                    return
                }
            }
            const sock = getSock()
            if (!sock) {
                res.writeHead(503).end('Bot aún no conectado')
                return
            }
            const resolved = resolveNotifyJid(target, { ordersGid, alertJid, phone })
            if (resolved.error === 'orders_group_not_configured') {
                res.writeHead(503).end('Grupo de pedidos no configurado en esta instancia')
                return
            }
            if (resolved.error === 'invalid_phone') {
                res.writeHead(400).end('Teléfono inválido')
                return
            }
            let jid = resolved.jid
            try {
                if (resolved.customerPhone) {
                    const [found] = (await sock.onWhatsApp('52' + resolved.customerPhone)) || []
                    if (!found || !found.exists) {
                        logger.warn({ phone: '…' + resolved.customerPhone.slice(-4) },
                            'Aviso a cliente: el número no tiene WhatsApp')
                        res.writeHead(404).end('El número no tiene WhatsApp')
                        return
                    }
                    jid = found.jid
                }
                await sock.sendMessage(jid, { text: message })
                res.writeHead(200).end('ok')
            } catch (err) {
                logger.error({ err: err.message, target }, 'Error enviando aviso (/notify)')
                res.writeHead(500).end('Error al enviar')
            }
        })
    }
}

module.exports = { createNotifyHandler }
