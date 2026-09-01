// Buffer en memoria de imágenes de un lote.
// Soporta dos flujos del proveedor:
//   A) Imágenes con precio en su caption: se acumulan mientras el precio
//      sea el mismo; cuando cambia el precio se hace flush automático del
//      lote anterior y se empieza uno nuevo.
//   B) Imágenes sin precio + texto final con el precio: flush al recibir
//      el texto (comportamiento original).
const TTL_MS = 5 * 60 * 1000   // 5 minutos
const MAX_PER_GROUP = 50

/**
 * Crea una instancia de buffer con estado propio (facilita los tests).
 * @param {{ ttlMs?: number, maxPerGroup?: number }} opts
 */
function createBatchBuffer({ ttlMs = TTL_MS, maxPerGroup = MAX_PER_GROUP } = {}) {
    // { [groupJid]: { items: [msg, ...], lastTs: number, price: number|null, caption: string } }
    const buffers = {}

    function purgeExpired(now) {
        let dropped = 0
        for (const jid of Object.keys(buffers)) {
            if (now - buffers[jid].lastTs > ttlMs) {
                dropped += buffers[jid].items.length
                delete buffers[jid]
            }
        }
        return dropped
    }

    /**
     * @param {string} groupJid
     * @param {object} msg
     * @param {number} now   - Date.now()
     * @param {number|null} price   - precio extraído del caption, o null
     * @param {string} caption      - caption original de la imagen
     */
    function addImage(groupJid, msg, now, price = null, caption = '') {
        purgeExpired(now)
        const entry = buffers[groupJid] || { items: [], lastTs: now, price: null, caption: '' }
        if (entry.items.length >= maxPerGroup) return entry.items.length
        entry.items.push(msg)
        entry.lastTs = now
        // Guardar el precio y caption del lote (se actualiza con cada imagen)
        if (price !== null) entry.price = price
        if (caption)        entry.caption = caption
        buffers[groupJid] = entry
        return entry.items.length
    }

    function size(groupJid) {
        return buffers[groupJid] ? buffers[groupJid].items.length : 0
    }

    /** Precio actualmente en buffer para el grupo, o null si no hay. */
    function getPrice(groupJid) {
        return buffers[groupJid]?.price ?? null
    }

    /** Caption del último mensaje con precio del lote actual. */
    function getCaption(groupJid) {
        return buffers[groupJid]?.caption ?? ''
    }

    function flush(groupJid) {
        const entry = buffers[groupJid]
        if (!entry) return []
        delete buffers[groupJid]
        return entry.items
    }

    return { addImage, size, getPrice, getCaption, flush, purgeExpired }
}

module.exports = { createBatchBuffer, TTL_MS, MAX_PER_GROUP }
