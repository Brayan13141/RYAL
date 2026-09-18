const crypto = require('crypto')

// Tope del body de /notify: un aviso es una línea; más que esto no es un aviso.
const MAX_NOTIFY_BODY = 16 * 1024

// Comparación en tiempo constante. Sin token configurado, nada es válido.
function isValidToken(received, expected) {
    if (!expected || typeof received !== 'string') return false
    const a = Buffer.from(received)
    const b = Buffer.from(expected)
    if (a.length !== b.length) return false
    return crypto.timingSafeEqual(a, b)
}

module.exports = { MAX_NOTIFY_BODY, isValidToken }
