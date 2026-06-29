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

    test('startSession reemplaza sesión existente con items', () => {
        store.startSession('g1', 'A', '5551111111')
        store.addItem('g1', 'cap', 100)
        store.startSession('g1', 'B', '5552222222')
        const sess = store.getSession('g1')
        expect(sess.cliente.nombre).toBe('B')
        expect(sess.items).toHaveLength(0)
    })

    test('addItem segundo ítem suma total', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 200)
        const r = store.addItem('g1', 'b', 300)
        expect(r.total).toBe(500)
    })

    test('addItem qty inicial es 1', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.getSession('g1').items[0].qty).toBe(1)
    })

    test('addItem total refleja qty modificada', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        store.setQty('g1', 1, 3)
        const r = store.addItem('g1', 'b', 200)
        expect(r.total).toBe(500)
    })

    test('removeItem retorna true al eliminar', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.removeItem('g1', 1)).toBe(true)
    })

    test('removeItem retorna false para índice 0', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.removeItem('g1', 0)).toBe(false)
    })

    test('removeItem retorna false para índice mayor al length', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.removeItem('g1', 2)).toBe(false)
    })

    test('removeItem retorna false sin sesión', () => {
        expect(store.removeItem('g1', 1)).toBe(false)
    })

    test('setQty retorna true al actualizar', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.setQty('g1', 1, 5)).toBe(true)
    })

    test('setQty retorna false para índice fuera de rango', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.setQty('g1', 2, 1)).toBe(false)
    })

    test('setQty retorna false para cantidad 0 o negativa', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'a', 100)
        expect(store.setQty('g1', 1, 0)).toBe(false)
        expect(store.setQty('g1', 1, -1)).toBe(false)
    })

    test('setQty retorna false sin sesión', () => {
        expect(store.setQty('g1', 1, 1)).toBe(false)
    })

    test('cancelSession en sesión inexistente no lanza error', () => {
        expect(() => store.cancelSession('g1')).not.toThrow()
    })

    test('getSession retorna null sin sesión', () => {
        expect(store.getSession('g1')).toBeNull()
    })

    test('getSession no mezcla sesiones de diferentes grupos', () => {
        store.startSession('g1', 'A', '5551111111')
        expect(store.getSession('g2')).toBeNull()
    })

    test('startSession tipo por defecto es pedido', () => {
        store.startSession('g1', 'X', '5550000000')
        expect(store.getSession('g1').tipo).toBe('pedido')
    })

    test('startSession tipo tienda', () => {
        store.startSession('g1', 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        expect(store.getSession('g1').tipo).toBe('tienda')
    })

    test('startSession reemplaza tipo al reabrir sesión', () => {
        store.startSession('g1', 'X', '5550000000', 'pedido')
        store.startSession('g1', 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        expect(store.getSession('g1').tipo).toBe('tienda')
    })
})

describe('costo y descuento', () => {
    let store
    beforeEach(() => { store = createOrderSessionStore() })

    test('addItem guarda costo=0 por defecto', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'Gorra', 400)
        expect(store.getSession('g1').items[0].costo).toBe(0)
    })

    test('addItem guarda costo explícito', () => {
        store.startSession('g1', 'X', '5550000000')
        store.addItem('g1', 'Gorra', 400, 300)
        expect(store.getSession('g1').items[0].costo).toBe(300)
    })

    test('total no cambia por costo', () => {
        store.startSession('g1', 'X', '5550000000')
        const r = store.addItem('g1', 'Gorra', 400, 300)
        expect(r.total).toBe(400)
    })

    test('setDescuento guarda codigo y monto', () => {
        store.startSession('g1', 'X', '5550000000')
        store.setDescuento('g1', 'GORRA50', 50, 7)
        const d = store.getDescuento('g1')
        expect(d.codigo).toBe('GORRA50')
        expect(d.monto).toBe(50)
        expect(d.codigoId).toBe(7)
    })

    test('getDescuento retorna null sin descuento', () => {
        store.startSession('g1', 'X', '5550000000')
        expect(store.getDescuento('g1')).toBeNull()
    })

    test('setDescuento retorna false sin sesión', () => {
        expect(store.setDescuento('g1', 'X', 50, 1)).toBe(false)
    })

    test('startSession resetea descuento', () => {
        store.startSession('g1', 'X', '5550000000')
        store.setDescuento('g1', 'PROMO', 100, 1)
        store.startSession('g1', 'Y', '5551111111')
        expect(store.getDescuento('g1')).toBeNull()
    })
})
