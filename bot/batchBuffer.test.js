const { createBatchBuffer } = require('./batchBuffer')

const T0 = 1_000_000
const FIVE_MIN = 5 * 60 * 1000

test('addImage acumula y size refleja el conteo', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { id: 1 }, T0)
    b.addImage('g1', { id: 2 }, T0)
    expect(b.size('g1')).toBe(2)
})

test('flush devuelve los items y vacía el buffer', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { id: 1 }, T0)
    b.addImage('g1', { id: 2 }, T0)
    const items = b.flush('g1')
    expect(items).toHaveLength(2)
    expect(b.size('g1')).toBe(0)
})

test('flush de grupo vacío devuelve []', () => {
    const b = createBatchBuffer()
    expect(b.flush('nada')).toEqual([])
})

test('purgeExpired no descarta dentro de la ventana TTL', () => {
    const b = createBatchBuffer({ ttlMs: FIVE_MIN })
    b.addImage('g1', { id: 1 }, T0)
    const dropped = b.purgeExpired(T0 + FIVE_MIN - 1)
    expect(dropped).toBe(0)
    expect(b.size('g1')).toBe(1)
})

test('purgeExpired descarta pasado el TTL y devuelve el conteo', () => {
    const b = createBatchBuffer({ ttlMs: FIVE_MIN })
    b.addImage('g1', { id: 1 }, T0)
    b.addImage('g1', { id: 2 }, T0)
    const dropped = b.purgeExpired(T0 + FIVE_MIN + 1)
    expect(dropped).toBe(2)
    expect(b.size('g1')).toBe(0)
})

test('addImage purga lotes viejos de forma perezosa', () => {
    const b = createBatchBuffer({ ttlMs: FIVE_MIN })
    b.addImage('g1', { id: 1 }, T0)
    b.addImage('g1', { id: 2 }, T0 + FIVE_MIN + 1) // el viejo expira; arranca lote nuevo
    expect(b.size('g1')).toBe(1)
})

test('los grupos están aislados entre sí', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { a: 1 }, T0)
    b.addImage('g2', { c: 1 }, T0)
    b.flush('g1')
    expect(b.size('g1')).toBe(0)
    expect(b.size('g2')).toBe(1)
})

test('addImage respeta el cap maxPerGroup', () => {
    const b = createBatchBuffer({ maxPerGroup: 2 })
    b.addImage('g1', { id: 1 }, T0)
    b.addImage('g1', { id: 2 }, T0)
    b.addImage('g1', { id: 3 }, T0) // excede el cap → no se agrega
    expect(b.size('g1')).toBe(2)
})

test('getPrice devuelve el precio del último addImage con precio', () => {
    const b = createBatchBuffer()
    expect(b.getPrice('g1')).toBeNull()
    b.addImage('g1', { id: 1 }, T0, 300, '$300 Nike Rojo')
    expect(b.getPrice('g1')).toBe(300)
    b.addImage('g1', { id: 2 }, T0, 300, '$300 Nike Azul')
    expect(b.getPrice('g1')).toBe(300)
})

test('getCaption devuelve el caption del último addImage con caption', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { id: 1 }, T0, 300, '$300 Nike Rojo')
    b.addImage('g1', { id: 2 }, T0, 300, '$300 Nike Azul')
    expect(b.getCaption('g1')).toBe('$300 Nike Azul')
})

test('getPrice y getCaption se resetean tras flush', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { id: 1 }, T0, 300, '$300 Nike')
    b.flush('g1')
    expect(b.getPrice('g1')).toBeNull()
    expect(b.getCaption('g1')).toBe('')
})

test('imágenes sin precio no sobreescriben el precio del lote', () => {
    const b = createBatchBuffer()
    b.addImage('g1', { id: 1 }, T0, 300, '$300 Nike Rojo')
    b.addImage('g1', { id: 2 }, T0, null, '') // sin precio
    expect(b.getPrice('g1')).toBe(300)
})
