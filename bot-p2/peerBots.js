/**
 * Mensajes del Grupo Pedidos que escribe OTRA instancia del bot.
 *
 * bot-p3 existe para que un segundo operador dé comandos desde su propio
 * número, y atiende solo sus mensajes (`fromMe`, ver bot-p3/routing.js). Para
 * bot-p2 esos mismos mensajes son de un tercero, así que los atendía también:
 * el 2026-09-20 un `/cerrar` quedó registrado dos veces (pedidos 105 y 106,
 * 18 ms de diferencia).
 *
 * La `idem_key` ya impide el pedido duplicado, pero NO alcanza sola: cada
 * instancia arma su propia sesión en memoria, y si una se perdió un ítem —
 * porque no pudo descifrarlo — el POST que llegue primero es el que queda.
 * Una venta registrada de menos es peor que una registrada dos veces, porque
 * nada la delata. Que cada mensaje lo atienda UNA sola instancia es lo que
 * evita que las sesiones diverjan.
 *
 * `fromMe` nunca se ignora: una lista mal cargada con el número propio dejaría
 * a esta instancia sin atender sus propios comandos, y eso sí es un hueco.
 */

/** Compara por dígitos: el sufijo de dispositivo (`:2`) y el dominio cambian
 *  entre `@s.whatsapp.net`, `@lid` y las dos formas que convive 7.x. */
function normalizar(jid) {
    return String(jid || '').split('@')[0].split(':')[0].replace(/\D/g, '')
}

function parsePeerJids(raw) {
    return new Set(
        String(raw || '').split(',').map(normalizar).filter(Boolean))
}

function esDeOtraInstancia(msg, peerJids) {
    if (!peerJids || peerJids.size === 0) return false
    if (msg?.key?.fromMe) return false
    // Cuál de estos campos trae al autor depende de la migración a LID, así
    // que se miran todos: con que uno acierte, la exclusión funciona.
    const autores = [
        msg?.key?.participant,
        msg?.key?.participantAlt,
        msg?.key?.participantPn,
        msg?.participant,
    ]
    return autores.some((a) => {
        const n = normalizar(a)
        return Boolean(n) && peerJids.has(n)
    })
}

module.exports = { parsePeerJids, esDeOtraInstancia, normalizar }
