const { MENU_RESPONSES, buildMenuResponses, menuReply } = require('./welcome')
const { createMinimosSource } = require('./minimos')

// Lo que devuelve /api/negocio/minimos/ en prod al 2026-10-04.
const PROD = [
    { slug: 'bolsos-de-lujo-de-gama-alta', nombre: 'Bolsos de lujo de gama alta', min_pedido: 5, min_por_modelo: 0 },
    { slug: 'calzado', nombre: 'Calzado', min_pedido: 1, min_por_modelo: 12 },
    { slug: 'camisetas-deportivas-y-jerseys-de-futbol', nombre: 'Camisetas deportivas y jerseys de fútbol', min_pedido: 20, min_por_modelo: 0 },
    { slug: 'camisetassudaderas-calidad-11', nombre: 'Camisetas/Sudaderas Calidad 1:1', min_pedido: 20, min_por_modelo: 0 },
    { slug: 'camisetassudaderas-calidad-g5', nombre: 'Camisetas/Sudaderas Calidad G5', min_pedido: 20, min_por_modelo: 0 },
    { slug: 'gorra', nombre: 'Gorra', min_pedido: 20, min_por_modelo: 0 },
    { slug: 'toda-la-linea-de-accesorios-de-joyeria', nombre: 'Toda la línea de accesorios de joyería', min_pedido: 1, min_por_modelo: 0 },
    { slug: 'van-cleef-arpels', nombre: 'Van Cleef & Arpels', min_pedido: 10, min_por_modelo: 0 },
]

const con = (slug, cambios) => PROD.map(c => (c.slug === slug ? { ...c, ...cambios } : c))

describe('buildMenuResponses', () => {
    test('con los mínimos de prod el texto sale idéntico al que ya se mandaba', () => {
        const r = buildMenuResponses(PROD)
        expect(r[1]).toBe(MENU_RESPONSES[1])
        expect(r[2]).toBe(MENU_RESPONSES[2])
        expect(r[3]).toBe(MENU_RESPONSES[3])
    })

    test('un mínimo editado en el panel aparece en las opciones 1 y 3', () => {
        const r = buildMenuResponses(con('gorra', { min_pedido: 30 }))
        expect(r[1]).toMatch(/• Gorras: 30 piezas\n/)
        expect(r[3]).toMatch(/gorras 30 piezas; playeras, sudaderas y jerseys 20 piezas;/)
    })

    test('tenis sale de min_por_modelo', () => {
        const r = buildMenuResponses(con('calzado', { min_por_modelo: 6 }))
        expect(r[1]).toMatch(/• Tenis: 6 pares por modelo y color \(puedes combinar tallas\)/)
        expect(r[3]).toMatch(/tenis 6 pares por modelo y color/)
    })

    test('una categoría que ya no viene (inactiva) desaparece del texto', () => {
        const r = buildMenuResponses(PROD.filter(c => c.slug !== 'bolsos-de-lujo-de-gama-alta'))
        expect(r[1]).not.toMatch(/Bolsos/)
        expect(r[3]).not.toMatch(/bolsos/)
    })

    test('si las categorías de playeras no coinciden se anuncia la mayor', () => {
        const r = buildMenuResponses(con('camisetassudaderas-calidad-11', { min_pedido: 40 }))
        expect(r[1]).toMatch(/• Playeras, sudaderas y jerseys: 40 piezas/)
    })

    test('sin datos de Django se usa el texto fijo', () => {
        expect(buildMenuResponses(null)).toBe(MENU_RESPONSES)
        expect(buildMenuResponses([])).toBe(MENU_RESPONSES)
    })

    test('menuReply usa los mínimos que le pasan', () => {
        expect(menuReply('1', con('gorra', { min_pedido: 30 }))).toMatch(/Gorras: 30 piezas/)
        expect(menuReply('1')).toBe(MENU_RESPONSES[1])
    })
})

describe('createMinimosSource', () => {
    test('consulta una vez y reusa el resultado dentro del TTL', async () => {
        let t = 0
        const fetchFn = jest.fn().mockResolvedValue(PROD)
        const src = createMinimosSource({ fetchFn, ttlMs: 1000, now: () => t })
        expect(await src.get()).toBe(PROD)
        t = 999
        await src.get()
        expect(fetchFn).toHaveBeenCalledTimes(1)
        t = 1001
        await src.get()
        expect(fetchFn).toHaveBeenCalledTimes(2)
    })

    test('si Django falla devuelve lo último bueno', async () => {
        let t = 0
        const fetchFn = jest.fn().mockResolvedValueOnce(PROD).mockRejectedValue(new Error('timeout'))
        const src = createMinimosSource({ fetchFn, ttlMs: 10, now: () => t })
        await src.get()
        t = 100
        expect(await src.get()).toBe(PROD)
    })

    test('si Django falla y nunca respondió devuelve null (texto fijo)', async () => {
        const src = createMinimosSource({ fetchFn: () => Promise.reject(new Error('x')), ttlMs: 10, now: () => 0 })
        expect(await src.get()).toBeNull()
    })

    test('tras un fallo no reintenta en cada mensaje', async () => {
        let t = 0
        const fetchFn = jest.fn().mockRejectedValue(new Error('caído'))
        const src = createMinimosSource({ fetchFn, ttlMs: 1000, now: () => t })
        await src.get()
        t = 10
        await src.get()
        expect(fetchFn).toHaveBeenCalledTimes(1)
    })
})
