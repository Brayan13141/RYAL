// Patrones en orden de especificidad — el primero que coincida gana.
// Todos exigen un marcador de moneda ("precio", "$", "pesos"): un número
// desnudo NO se considera precio para no confundir modelos de tenis
// (New Balance 550, Air Max 270, Yeezy 350...) con un precio real.
const PRICE_PATTERNS = [
    /precio[:\s]+\$?\s*(\d+(?:\.\d{1,2})?)/i,   // "Precio: $350" o "precio 350"
    /\$\s*(\d{2,4}(?:\.\d{1,2})?)/,              // "$350"
    /(\d{2,4}(?:\.\d{1,2})?)\s*pesos?/i,         // "350 pesos"
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

/**
 * Calcula el total a cobrar al cliente: precio menos descuento, con piso en 0
 * (un descuento mayor que el precio nunca produce un total negativo).
 */
function computeTotal(price, descuento) {
    return Math.max(0, price - (descuento || 0))
}

module.exports = { extractPrice, generateMessage, computeTotal }
