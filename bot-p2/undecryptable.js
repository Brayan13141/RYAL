// Cuando WhatsApp entrega un mensaje que el bot no puede descifrar, Baileys lo
// emite igual pero con `message` vacío. El loop de `messages.upsert` lo descarta
// y sigue. Eso está bien: no hay nada que procesar. Lo que NO está bien es que
// pase en silencio — si el mensaje era un comando en un grupo operativo, el bot
// se ve "sordo" sin dejar rastro. Esta función decide qué merece una línea de log.
//
// Solo los grupos que el bot atiende. Los fallos de `status@broadcast` y de los
// grupos ajenos son ruido de fondo permanente (miles por semana) y a ese volumen
// esconderían justamente la línea que importa.

function describeUndecryptable(msg, gids = {}) {
    if (msg?.message) return null

    const jid = msg?.key?.remoteJid
    if (!jid) return null

    const operativos = [gids.orders, gids.supplier, gids.ryal].filter(Boolean)
    if (!operativos.includes(jid)) return null

    return {
        jid,
        participant: msg.key.participantPn || msg.key.participant || null,
        id: msg.key.id || null,
    }
}

module.exports = { describeUndecryptable }
