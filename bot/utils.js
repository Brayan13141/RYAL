// Patrones en orden de especificidad — el primero que coincida gana
const PRICE_PATTERNS = [
    /precio[:\s]+\$?\s*(\d+(?:\.\d{1,2})?)/i,   // "Precio: $350" o "precio 350"
    /\$\s*(\d{2,4}(?:\.\d{1,2})?)/,              // "$350"
    /(\d{2,4}(?:\.\d{1,2})?)\s*pesos?/i,         // "350 pesos"
    /(\d{3,4}(?:\.\d{1,2})?)/,                   // fallback: 3-4 dígitos
]

const MIN_PRICE = 50
const MAX_PRICE = 9999

/**
 * Extrae el precio de un mensaje de WhatsApp.
 * Retorna el precio como número, o null si no se encuentra.
 */
function extractPrice(text) {
    if (!text) return null
    for (const pattern of PRICE_PATTERNS) {
        const match = text.match(pattern)
        if (match) {
            const price = parseFloat(match[1])
            if (price >= MIN_PRICE && price <= MAX_PRICE) return price
        }
    }
    return null
}

/**
 * Genera el mensaje con el precio modificado.
 * Reemplaza el precio original por el nuevo conservando el resto del texto.
 */
function generateMessage(originalText, originalPrice, newPrice) {
    const escaped = String(originalPrice).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

    let result = originalText
        .replace(new RegExp('\\$\\s*' + escaped + '(?=\\D|$)', 'g'), `$${newPrice}`)

    if (result === originalText) {
        result = originalText
            .replace(new RegExp(escaped + '\\s*pesos?', 'gi'), `${newPrice} pesos`)
    }

    if (result === originalText) {
        result = originalText + `\n\n💰 Precio: $${newPrice}`
    }

    return result
}

module.exports = { extractPrice, generateMessage }
