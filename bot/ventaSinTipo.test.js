const { mensajeSinTipo } = require('./ventaSinTipo')

const JORDAN = {
    texto: 'Jordan', qty: 9, precio: 750,
    sugerencias: [
        { tipo_id: 1, nombre: 'JORDAN 4', costo: 680 },
        { tipo_id: 2, nombre: 'Jordan 1', costo: 620 },
    ],
}

describe('mensajeSinTipo', () => {
    test('dice que NO se registró y nombra el texto', () => {
        const { texto } = mensajeSinTipo([JORDAN], 1)
        expect(texto).toContain('NO registrada')
        expect(texto).toContain('«Jordan»')
        expect(texto).toContain('$750')
        expect(texto).toContain('× 9')
    })

    test('numera las sugerencias con su costo', () => {
        const { texto, opciones } = mensajeSinTipo([JORDAN], 1)
        expect(texto).toContain('1. JORDAN 4 — costo $680')
        expect(texto).toContain('2. Jordan 1 — costo $620')
        expect(opciones).toHaveLength(2)
        expect(opciones[0].tipo_id).toBe(1)
    })

    test('sin sugerencias manda al panel y no ofrece números', () => {
        const { texto, opciones } = mensajeSinTipo(
            [{ texto: 'qwx', qty: 1, precio: 100, sugerencias: [] }], 1)
        expect(opciones).toHaveLength(0)
        expect(texto).toContain('no encontré ninguno parecido')
        expect(texto).toContain('/panel/negocio/tipos/')
        expect(texto).not.toContain('Respondé el número')
    })

    test('con varios textos avisa cuántos faltan y resuelve el primero', () => {
        const otro = { texto: 'on', qty: 1, precio: 1100, sugerencias: [] }
        const { texto, opciones } = mensajeSinTipo([JORDAN, otro], 2)
        expect(texto).toContain('«Jordan»')
        expect(texto).toContain('1 texto más')
        expect(opciones).toHaveLength(2)
    })

    test('recuerda que los artículos siguen cargados', () => {
        const { texto } = mensajeSinTipo([JORDAN], 3)
        expect(texto).toContain('3 artículos siguen cargados')
    })
})
