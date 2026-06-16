// --- Reconocimiento de precio ---------------------------------------------
// Un precio es un número de 2-5 dígitos CON marcador de moneda. Marcadores:
//   "$" antes ($300, $ 300, $500c/p) | "precio" antes | "Mayoreo"/"c/p"/"pesos" después.
// Un número desnudo NO es precio: así no se confunden tallas/modelos con precios
// (#3 al 6, 1pz del 3, Modelo-013, Mod-01, New Balance 550, Air Max 270...).
const PRICE_TOKEN = /precio[:\s]+\$?\s*\d{2,5}(?:\.\d{1,2})?|\$\s*\d{2,5}(?:\.\d{1,2})?|\d{2,5}(?:\.\d{1,2})?\s*(?:c\/p|pesos?|mayoreo)/gi
const NUM_IN_TOKEN = /\d{2,5}(?:\.\d{1,2})?/

const MIN_PRICE = 50
const MAX_PRICE = 99999

const RYAL_FOOTER =
    '↪️ Reenvía esta imagen con las tallas que quieres para tu pedido.\n' +
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
    return text.replace(PRICE_TOKEN, (token) => {
        const v = _tokenValue(token)
        if (!_inRange(v)) return token
        const m = token.match(NUM_IN_TOKEN)
        return token.replace(m[0], String(v + markup))
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

module.exports = { extractPrice, markupCaption, cleanCaption, buildRyalForward, computeTotal }
