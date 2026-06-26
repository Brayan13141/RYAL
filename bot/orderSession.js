function createOrderSessionStore() {
    const sessions = {}
    const pendings = {}

    function startSession(groupJid, nombre, telefono) {
        sessions[groupJid] = { cliente: { nombre, telefono }, items: [] }
        delete pendings[groupJid]
    }

    function addItem(groupJid, description, price) {
        const sess = sessions[groupJid]
        if (!sess) return null
        sess.items.push({ description, price, qty: 1 })
        const total = sess.items.reduce((s, i) => s + i.price * i.qty, 0)
        return { index: sess.items.length, total }
    }

    function removeItem(groupJid, index) {
        const sess = sessions[groupJid]
        if (!sess) return false
        if (index < 1 || index > sess.items.length) return false
        sess.items.splice(index - 1, 1)
        return true
    }

    function setQty(groupJid, index, qty) {
        const sess = sessions[groupJid]
        if (!sess) return false
        if (index < 1 || index > sess.items.length) return false
        if (!Number.isInteger(qty) || qty < 1) return false
        sess.items[index - 1].qty = qty
        return true
    }

    function cancelSession(groupJid) {
        delete sessions[groupJid]
        delete pendings[groupJid]
    }

    function getSession(groupJid) {
        return sessions[groupJid] || null
    }

    function setPending(groupJid, type, payload) {
        pendings[groupJid] = { type, payload }
    }

    function getPending(groupJid) {
        return pendings[groupJid] || null
    }

    function clearPending(groupJid) {
        delete pendings[groupJid]
    }

    return { startSession, addItem, removeItem, setQty, cancelSession, getSession, setPending, getPending, clearPending }
}

module.exports = { createOrderSessionStore }
