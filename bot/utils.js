// --- Reconocimiento de precio ---------------------------------------------
// Un precio es un número de 2-5 dígitos CON marcador de moneda. Marcadores:
//   "$" antes ($300, $ 300, $500c/p) | "precio" antes | "Mayoreo"/"c/p"/"pesos" después.
// Un número desnudo NO es precio: así no se confunden tallas/modelos con precios
// (#3 al 6, 1pz del 3, Modelo-013, Mod-01, New Balance 550, Air Max 270...).
const PRICE_TOKEN = /precio[:\s]+\$?\s*\d{2,5}(?:\.\d{1,2})?|\$\s*\d{2,5}(?:\.\d{1,2})?|\d{2,5}(?:\.\d{1,2})?\$\s*(?:c\/p|pesos?|mayoreo)|\d{2,5}(?:\.\d{1,2})?\s*(?:c\/p|pesos?|mayoreo)/gi
const NUM_IN_TOKEN = /\d{2,5}(?:\.\d{1,2})?/

// Línea de "paquete" (venta por mayoreo de N piezas): el total del paquete
// usa separador de miles ($4,750), que PRICE_TOKEN no reconoce, y su markup
// no puede ser el mismo monto plano que un precio individual — debe ser
// proporcional a la cantidad de piezas: cantidad x (precio c/u ya marcado).
const PACKAGE_TOKEN = /(\d{1,3})\*?\s*pz\D{0,10}?\$\s*([\d,]+(?:\.\d{1,2})?)\s*\$\s*(\d{2,5})(?:\.\d{1,2})?\s*c\/u/gi

const MIN_PRICE = 50
const MAX_PRICE = 99999

const RYAL_FOOTER =
    '🔥 Reenvía la imagen del modelo que quieres y las tallas para tu pedido 🔥\n' +
    '🌐 ryalsneackers.com'

function _tokenValue(token) {
    const m = token.match(NUM_IN_TOKEN)
    return m ? parseFloat(m[0]) : NaN
}

function _inRange(v) {
    return v >= MIN_PRICE && v <= MAX_PRICE
}

/**
 * Extrae el PRIMER precio válido de un mensaje (el mayoreo en mensajes
 * multi-precio). Retorna el precio como número, o null si no se encuentra.
 */
function extractPrice(text) {
    if (!text) return null
    const tokens = text.match(PRICE_TOKEN) || []
    for (const token of tokens) {
        const v = _tokenValue(token)
        if (_inRange(v)) return v
    }
    return null
}

/**
 * Suma `markup` a TODOS los precios del mensaje, conservando el resto del texto
 * (emojis, formato, marcadores). Los números sin marcador no se tocan.
 */
function markupCaption(text, markup) {
    if (!text) return text
    const marked = text.replace(PRICE_TOKEN, (token) => {
        const v = _tokenValue(token)
        if (!_inRange(v)) return token
        const m = token.match(NUM_IN_TOKEN)
        return token.replace(m[0], String(v + markup))
    })
    // El c/u de arriba ya quedó marcado; el total del paquete se recalcula
    // desde ese c/u marcado x cantidad, en vez de sumarle el markup plano.
    return marked.replace(PACKAGE_TOKEN, (full, qty, total, perUnitMarked) => {
        const newTotal = parseInt(qty, 10) * parseFloat(perUnitMarked)
        return full.replace(total, newTotal.toLocaleString('en-US'))
    })
}

/**
 * Limpia el mensaje del proveedor para que se vea de Ryal: normaliza la fuente
 * decorativa a ASCII (ℂ𝔸𝕃𝕀𝔻𝔸𝔻 → CALIDAD), quita emojis pictográficos
 * CONSERVANDO la viñeta ▪️, y recorta espacios/líneas en blanco repetidas.
 */
function cleanCaption(text) {
    if (!text) return text
    let out = text.normalize('NFKC')
    // quitar pictogramas salvo la viñeta ▪ (U+25AA)
    out = out.replace(/\p{Extended_Pictographic}/gu, (ch) => (ch === '▪' ? ch : ''))
    // quitar selectores de variación huérfanos (los que NO siguen a ▪)
    out = out.replace(/([^▪])️/gu, '$1').replace(/^️/u, '')
    // quitar modificadores de tono de piel y ZWJ residuales
    out = out.replace(/[\u{1F3FB}-\u{1F3FF}‍]/gu, '')
    // recortar espacios por línea y colapsar líneas en blanco repetidas
    out = out.split('\n').map((l) => l.trim()).join('\n').replace(/\n{3,}/g, '\n\n')
    return out.trim()
}

/**
 * Construye el mensaje final que el bot reenvía al Grupo Ryal:
 * precios marcados + cuerpo limpio + pie de página de Ryal.
 */
function buildRyalForward(text, markup) {
    const body = cleanCaption(markupCaption(text || '', markup))
    return `${body}\n\n${RYAL_FOOTER}`
}

/**
 * Total a cobrar al cliente: precio menos descuento, con piso en 0.
 */
function computeTotal(price, descuento) {
    return Math.max(0, price - (descuento || 0))
}

/**
 * Caption corto para cada imagen reenviada de un lote: el precio ya marcado
 * (+MARKUP) en formato que extractPrice puede releer + el pie de Ryal.
 * Mantiene el flujo del cliente intacto (reenvía la imagen → trae precio).
 */
function buildImageCaption(finalPrice) {
    return `$${finalPrice} MXN`
}

/**
 * Parsea "/pedido <cliente...> moda <cantidad> <ganancia> [envio=X]".
 * `args` son los tokens después de "/pedido". Devuelve null si el formato
 * no matchea (incluye: token "moda" ausente, sin cliente antes, cantidad/
 * ganancia faltante o inválida, envío mal formado, o tokens de sobra).
 */
function parseModaArgs(args) {
    const idx = args.findIndex((a) => a.toLowerCase() === 'moda')
    if (idx === -1) return null

    const queryTokens = args.slice(0, idx)
    const rest = args.slice(idx + 1)
    if (queryTokens.length === 0 || rest.length < 2 || rest.length > 3) return null

    if (!/^\d+$/.test(rest[0])) return null
    const cantidad = parseInt(rest[0], 10)
    if (cantidad <= 0) return null

    if (!/^\d+(\.\d{1,2})?$/.test(rest[1])) return null
    const ganancia = parseFloat(rest[1])

    let envio = 0
    if (rest.length === 3) {
        const m = rest[2].match(/^envio=(\d+(?:\.\d{1,2})?)$/)
        if (!m) return null
        envio = parseFloat(m[1])
    }

    return { query: queryTokens.join(' '), cantidad, ganancia, envio }
}

module.exports = { extractPrice, markupCaption, cleanCaption, buildRyalForward, buildImageCaption, computeTotal, parseModaArgs }
