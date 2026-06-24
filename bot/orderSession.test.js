const { createOrderSessionStore } = require('./orderSession')

const GID = '120363426164721860@g.us'
const GID2 = '120363426164721861@g.us'

describe('createOrderSessionStore', () => {
    let store

    beforeEach(() => {
        store = createOrderSessionStore()
    })

    // startSession
    test('startSession crea sesión con cliente e items vacíos', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        const sess = store.getSession(GID)
        expect(sess).not.toBeNull()
        expect(sess.cliente.nombre).toBe('Bryan')
        expect(sess.cliente.telefono).toBe('5512345678')
        expect(sess.items).toHaveLength(0)
    })

    test('startSession reemplaza sesión existente con items', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        store.startSession(GID, 'Socio', '5598765432')
        const sess = store.getSession(GID)
        expect(sess.cliente.nombre).toBe('Socio')
        expect(sess.items).toHaveLength(0)
    })

    // addItem
    test('addItem sin sesión retorna null', () => {
        expect(store.addItem(GID, 'Gorra azul', 500)).toBeNull()
    })

    test('addItem con sesión agrega ítem y retorna index=1 total correcto', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        const result = store.addItem(GID, 'Gorra azul', 500)
        expect(result).toEqual({ index: 1, total: 500 })
        expect(store.getSession(GID).items).toHaveLength(1)
    })

    test('addItem segundo ítem suma total', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        const result = store.addItem(GID, 'Tenis negro', 800)
        expect(result).toEqual({ index: 2, total: 1300 })
    })

    test('addItem qty inicial es 1', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.getSession(GID).items[0].qty).toBe(1)
    })

    test('addItem total refleja qty modificada', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        store.setQty(GID, 1, 3)
        const result = store.addItem(GID, 'Tenis', 800)
        expect(result.total).toBe(2300) // 500*3 + 800*1
    })

    // removeItem
    test('removeItem elimina por índice 1-based', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        store.addItem(GID, 'Tenis negro', 800)
        store.removeItem(GID, 1)
        const items = store.getSession(GID).items
        expect(items).toHaveLength(1)
        expect(items[0].description).toBe('Tenis negro')
    })

    test('removeItem retorna true al eliminar', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.removeItem(GID, 1)).toBe(true)
    })

    test('removeItem retorna false para índice 0', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.removeItem(GID, 0)).toBe(false)
    })

    test('removeItem retorna false para índice mayor al length', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.removeItem(GID, 2)).toBe(false)
    })

    test('removeItem retorna false sin sesión', () => {
        expect(store.removeItem(GID, 1)).toBe(false)
    })

    // setQty
    test('setQty actualiza cantidad del ítem', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        store.setQty(GID, 1, 3)
        expect(store.getSession(GID).items[0].qty).toBe(3)
    })

    test('setQty retorna true al actualizar', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.setQty(GID, 1, 2)).toBe(true)
    })

    test('setQty retorna false para índice fuera de rango', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.setQty(GID, 0, 2)).toBe(false)
        expect(store.setQty(GID, 2, 2)).toBe(false)
    })

    test('setQty retorna false para cantidad 0 o negativa', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.addItem(GID, 'Gorra azul', 500)
        expect(store.setQty(GID, 1, 0)).toBe(false)
        expect(store.setQty(GID, 1, -1)).toBe(false)
    })

    test('setQty retorna false sin sesión', () => {
        expect(store.setQty(GID, 1, 2)).toBe(false)
    })

    // cancelSession
    test('cancelSession elimina la sesión', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.cancelSession(GID)
        expect(store.getSession(GID)).toBeNull()
    })

    test('cancelSession en sesión inexistente no lanza error', () => {
        expect(() => store.cancelSession(GID)).not.toThrow()
    })

    // getSession
    test('getSession retorna null sin sesión', () => {
        expect(store.getSession(GID)).toBeNull()
    })

    test('getSession no mezcla sesiones de diferentes grupos', () => {
        store.startSession(GID, 'Bryan', '5512345678')
        store.startSession(GID2, 'Socio', '5598765432')
        expect(store.getSession(GID).cliente.nombre).toBe('Bryan')
        expect(store.getSession(GID2).cliente.nombre).toBe('Socio')
    })
})
