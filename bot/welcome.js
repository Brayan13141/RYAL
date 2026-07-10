const fs = require('fs')

// --- Bienvenida + menú fijo para clientes nuevos ----------------------------
// Un "cliente nuevo" es un JID privado que escribe por primera vez a este
// número (no hay registro en el archivo de vistos). Ambas personas usan este
// módulo — cada instancia tiene su propio archivo de vistos.

const WELCOME_MESSAGE =
    '¡Hola! 👋 Bienvenido(a) a *RYAL Sneakers* 🔥\n' +
    '\n' +
    'Somos tienda de importación: tenis, gorras, ropa y más al mejor precio.\n' +
    '\n' +
    'Escribe el número de la opción que necesitas:\n' +
    '1️⃣ Ver el catálogo\n' +
    '2️⃣ Cómo hacer un pedido\n' +
    '3️⃣ Hablar con un asesor'

const MENU_RESPONSES = {
    1: '🛒 Explora el catálogo completo aquí:\n' +
       '🌐 https://ryalsneackers.com\n' +
       '\n' +
       'Cuando veas algo que te guste, reenvíanos la imagen por este chat y te cotizamos 😉',
    2: '📦 *Cómo hacer un pedido:*\n' +
       '1. Busca el modelo en el Grupo Ryal o en https://ryalsneackers.com\n' +
       '2. Reenvía por este chat la imagen del modelo que quieres y las tallas.\n' +
       '3. Te confirmamos el total y la forma de pago.\n' +
       '\n' +
       '🚚 Hacemos envíos a todo México.',
    3: '👤 ¡Listo! En breve te atiende una persona del equipo Ryal.\n' +
       'Mientras tanto puedes mandarnos la foto del modelo que te interesa.',
}

// JIDs que nunca deben recibir bienvenida (no son chats de personas)
const IGNORED_JID_SUFFIXES = ['@broadcast', '@newsletter', '@g.us']

function isGreetableJid(jid) {
    if (!jid) return false
    return !IGNORED_JID_SUFFIXES.some(sfx => jid.endsWith(sfx))
}

/**
 * Devuelve la respuesta del menú si el texto es una opción válida ("1"-"3",
 * con espacios tolerados) o el menú completo si pide "menu"/"menú".
 * null si el texto no es una interacción de menú.
 */
function menuReply(text) {
    const t = (text || '').trim().toLowerCase()
    if (t === 'menu' || t === 'menú') return WELCOME_MESSAGE
    if (/^[123]$/.test(t)) return MENU_RESPONSES[t]
    return null
}

/**
 * Store persistente de JIDs ya saludados. Archivo JSON simple (array de
 * strings); si no existe o está corrupto se empieza de cero sin tirar el bot.
 */
function createWelcomeStore({ filePath, maxEntries = 20000 } = {}) {
    let seen = new Set()

    if (filePath && fs.existsSync(filePath)) {
        try {
            const data = JSON.parse(fs.readFileSync(filePath, 'utf8'))
            if (Array.isArray(data)) seen = new Set(data)
        } catch (_) {
            seen = new Set()
        }
    }

    function persist() {
        if (!filePath) return
        try {
            fs.writeFileSync(filePath, JSON.stringify([...seen]))
        } catch (_) {
            // sin persistencia no se rompe el flujo — solo se repite bienvenida tras restart
        }
    }

    return {
        hasSeen(jid) {
            return seen.has(jid)
        },
        markSeen(jid) {
            if (!jid || seen.has(jid)) return
            if (seen.size >= maxEntries) {
                seen.delete(seen.values().next().value)
            }
            seen.add(jid)
            persist()
        },
        size() {
            return seen.size
        },
    }
}

module.exports = { WELCOME_MESSAGE, MENU_RESPONSES, menuReply, isGreetableJid, createWelcomeStore }
