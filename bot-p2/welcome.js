const fs = require('fs')

// --- Bienvenida + menú fijo para clientes nuevos ----------------------------
// Un "cliente nuevo" es un JID privado que escribe por primera vez a este
// número (no hay registro en el archivo de vistos). Ambas personas usan este
// módulo — cada instancia tiene su propio archivo de vistos.

const WELCOME_MESSAGE =
    '¡Hola! Soy tu asesor de RYAL 👋\n' +
    '\n' +
    'Importación directa: playeras 280g, tenis, gorras, bolsos y sudaderas. Más de 20,000 productos a precio de fábrica.\n' +
    '\n' +
    'Cuéntame, ¿qué necesitas? Escribe el número:\n' +
    '1. Ya vendo y quiero precios de mayoreo\n' +
    '2. Quiero emprender (paquete con productos variados)\n' +
    '3. Información general\n' +
    '\n' +
    'Responde con un solo número a la vez, por favor 🙂'

const MENU_RESPONSES = {
    1: '¡Perfecto! Vas directo a precio de fábrica 🔥\n' +
       '\n' +
       'Mínimos por categoría:\n' +
       '• Playeras, gorras y otros: 20 piezas\n' +
       '• Tenis: 12 pares (mismo modelo, hasta 2 colores)\n' +
       '• Bolsos: 5 piezas\n' +
       '\n' +
       'Puedes combinar modelos dentro de la misma categoría. Lo único que no se mezcla son categorías para llegar al mínimo.\n' +
       '\n' +
       'Envío GRATIS en pedidos de fábrica 🚚\n' +
       '\n' +
       'Nuestra página es exclusiva para mayoristas — productos G5 idénticos, y entre más compras, mejor precio:\n' +
       'https://ryalsneackers.com\n' +
       '\n' +
       'En el grupo de WhatsApp encuentras tenis de calidad nacional y productos de la tienda física, con entrega en 3-5 días:\n' +
       'https://chat.whatsapp.com/DtVZ8aANnFg8qacSGp9E5s\n' +
       '\n' +
       'Dime qué te interesa y te hacemos la cotización sin pagar nada 😉',
    2: '¡Excelente decisión! 🚀 El Paquete Emprendedor es surtido listo para revender: te lo armamos nosotros con lo que más rota — playeras, gorras, tenis y accesorios variados.\n' +
       '\n' +
       'Desde $4,000 hasta $10,000 pesos.\n' +
       'Envío no incluido (se cotiza según tu ciudad).\n' +
       '\n' +
       'Tú no adivinas qué comprar: nosotros te surtimos.\n' +
       '\n' +
       'Dime con cuánto quieres arrancar y te armamos la propuesta sin compromiso 💪',
    3: 'Así trabajamos en RYAL 👌\n' +
       '\n' +
       '🏭 *Pedidos de fábrica (nuestra página):*\n' +
       '• Catálogo exclusivo para mayoristas, productos G5 idénticos: https://ryalsneackers.com\n' +
       '• Mínimos por categoría: playeras/gorras 20 piezas, tenis 12 pares, bolsos 5 piezas\n' +
       '• Pides tu cotización sin compromiso — no pagas nada hasta confirmar tu pedido\n' +
       '• Envío GRATIS\n' +
       '\n' +
       '📲 *Grupo de WhatsApp:*\n' +
       '• Tenis de calidad nacional y productos de la tienda física\n' +
       '• Manda la imagen del producto con tus tallas y te cotizamos\n' +
       '• Entrega en 3-5 días\n' +
       '• https://chat.whatsapp.com/DtVZ8aANnFg8qacSGp9E5s\n' +
       '\n' +
       'Si ya vendes y quieres precios, escribe 1.\n' +
       'Si quieres empezar de cero, escribe 2.',
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

    function addWithoutPersist(jid) {
        if (!jid || seen.has(jid)) return
        if (seen.size >= maxEntries) {
            seen.delete(seen.values().next().value)
        }
        seen.add(jid)
    }

    return {
        hasSeen(jid) {
            return seen.has(jid)
        },
        markSeen(jid) {
            addWithoutPersist(jid)
            persist()
        },
        /**
         * Marca varios JIDs de una vez con un solo write a disco — pensado para
         * sembrar de golpe los chats que Baileys reporta como ya existentes
         * (ver messaging-history.set en bot.js) sin escribir el archivo N veces.
         */
        markSeenBulk(jids) {
            if (!jids || jids.length === 0) return
            for (const jid of jids) addWithoutPersist(jid)
            persist()
        },
        size() {
            return seen.size
        },
    }
}

module.exports = { WELCOME_MESSAGE, MENU_RESPONSES, menuReply, isGreetableJid, createWelcomeStore }
