const { createOrderSessionStore } = require('./orderSession')

describe('pending state', () => {
    let store
    beforeEach(() => { store = createOrderSessionStore() })

    test('getPending devuelve null cuando no hay pending', () => {
        expect(store.getPending('g1')).toBeNull()
    })

    test('setPending y getPending roundtrip conflict', () => {
        store.setPending('g1', 'conflict', { nombre: 'Ana', telefono: '5551111111' })
        expect(store.getPending('g1')).toEqual({
            type: 'conflict',
            payload: { nombre: 'Ana', telefono: '5551111111' },
        })
    })

    test('clearPending elimina pending', () => {
        store.setPending('g1', 'conflict', { nombre: 'Ana', telefono: '5551111111' })
        store.clearPending('g1')
        expect(store.getPending('g1')).toBeNull()
    })

    test('startSession limpia pending', () => {
        store.setPending('g1', 'conflict', { nombre: 'Ana', telefono: '5551111111' })
        store.startSession('g1', 'Pedro', '5552222222')
        expect(store.getPending('g1')).toBeNull()
    })

    test('cancelSession limpia pending', () => {
        store.startSession('g1', 'A', '5550000000')
        store.setPending('g1', 'conflict', { nombre: 'B', telefono: '5559999999' })
        store.cancelSession('g1')
        expect(store.getPending('g1')).toBeNull()
    })

    test('pending es aislado por groupJid', () => {
        store.setPending('g1', 'disambig', [{ id: 1, nombre: 'Ana', telefono: '5551111111' }])
        expect(store.getPending('g2')).toBeNull()
    })

    test('setPending disambig guarda array de resultados', () => {
        const results = [
            { id: 1, nombre: 'Ana López',  telefono: '5551111111' },
            { id: 2, nombre: 'Ana García', telefono: '5552222222' },
        ]
        store.setPending('g1', 'disambig', results)
        const p = store.getPending('g1')
        expect(p.type).toBe('disambig')
        expect(p.payload).toHaveLength(2)
        expect(p.payload[0].nombre).toBe('Ana López')
    })
})

describe('comportamiento existente sin cambios', () => {
    let store
    beforeEach(() => { store = createOrderSessionStore() })

    test('startSession crea sesión con items vacíos', () => {
        store.startSession('g1', 'Juan', '5551234567')
        const sess = store.getSession('g1')
        expect(sess).not.toBeNull()
        expect(sess.cliente.nombre).toBe('Juan')
        expect(sess.items).toHaveLength(0)
    })

    test('addItem devuelve null sin sesión', () => {
        expect(store.addItem('g1', 'cap $400', 400)).toBeNull()
    })

    test('addItem devuelve index y total', () => {
        store.startSession('g1', 'X', '5550000000')
        const r = store.addItem('g1', 'cap $400', 400)
        expect(r.index).toBe(1)
        expect(r.total).toBe(400)
    })

    test('setQty actualiza cantidad', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'cap $400', 400)
        expect(store.setQty('g1', 1, 3)).toBe(true)
        expect(store.getSession('g1').items[0].qty).toBe(3)
    })

    test('removeItem elimina por índice 1-based', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        store.addItem('g1', 'b', 200)
        store.removeItem('g1', 1)
        expect(store.getSession('g1').items[0].description).toBe('b')
    })

    test('cancelSession elimina la sesión', () => {
        store.startSession('g1', 'X', '5550000000')
        store.cancelSession('g1')
        expect(store.getSession('g1')).toBeNull()
    })
})
