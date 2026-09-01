// --- Mensajes promocionales fijos del Grupo Ryal ----------------------------
// Bryan manda las fotos del producto al grupo y luego escribe el comando; el
// bot publica el anuncio que antes tecleaba a mano. Los textos son 100%
// estáticos: no llevan el precio del lote ni ningún dato variable, así que los
// comandos no aceptan argumentos.
//
// El cierre NO es RYAL_FOOTER de utils.js: ese pide "la imagen del modelo que
// quieres y las tallas", y las gorras son de talla ajustable. No sustituir.

const GORRAS_PROMO =
    '*New Era* o *Barbas y Dandy*\n' +
    'CALIDAD G5\n' +
    '▪️Talla ajustable\n' +
    '▪️ *$330 Mayoreo*\n' +
    '▪️Bordado de alta definición\n' +
    '▪️Etiquetas de su marca\n' +
    '▪️Broche metálico reforzado\n' +
    '▪️Caballero\n' +
    'Ojo: *A partir de 50 piezas el precio se reduce $50 c/p*\n' +
    'Los pedidos son surtidos a su elección\n' +
    '\n' +
    '🔥 Reenvía la imagen del modelo que quieres para tu pedido 🔥\n' +
    '🌐 ryalsneackers.com'

// Agregar una promo nueva es una línea aquí (p.ej. '/tenis': TENIS_PROMO).
const PROMOS = {
    '/gorras': GORRAS_PROMO,
}

// Match EXACTO del comando completo, no substring: "necesito /gorras urgente"
// no debe publicar nada.
function matchPromo(text) {
    if (typeof text !== 'string') return null
    return PROMOS[text.trim().toLowerCase()] || null
}

module.exports = { GORRAS_PROMO, PROMOS, matchPromo }
