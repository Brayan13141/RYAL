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
    1: '🛒 Tenemos dos catálogos:\n' +
       '\n' +
       '🏬 *Tienda física* — entrega más rápida. Pregúntanos qué hay disponible y te mandamos fotos.\n' +
       '📱 *Catálogo web* — fabricación desde 0, mejor precio:\n' +
       '🌐 https://ryalsneackers.com\n' +
       '\n' +
       'Cuando veas algo que te guste, reenvíanos la imagen por este chat y te cotizamos 😉',
    2: '📦 *Cómo hacer un pedido:*\n' +
       '\n' +
       '🌐 *Por la página* — el pedido se hace directo en https://ryalsneackers.com\n' +
       '🚚 Envío GRATIS y 🎁 *$200 MXN de descuento* en tu primer pedido — solicítalo al administrador por este chat.\n' +
       '\n' +
       '📲 *Por WhatsApp* — reenvía por este chat la imagen del modelo que quieres (del Grupo Ryal) con tus tallas y te confirmamos total y forma de pago.\n' +
       '\n' +
       '👟 *Tenis calidad nacional* — únete al grupo:\n' +
       'https://chat.whatsapp.com/DtVZ8aANnFg8qacSGp9E5s\n' +
       '🚀 *Paquetes emprendedores:* todo lo de la tienda física para arrancar tu negocio — pregunta por ellos.',
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
