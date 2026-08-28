const { avisoSinTipo } = require('./avisoSinTipo')

describe('avisoSinTipo', () => {
    test('sin huérfanos no agrega nada al mensaje', () => {
        expect(avisoSinTipo([])).toBe('')
        expect(avisoSinTipo(undefined)).toBe('')
        expect(avisoSinTipo(null)).toBe('')
    })

    test('nombra el artículo que no reconoció', () => {
        expect(avisoSinTipo(['Jordan'])).toContain('Jordan')
    })

    test('dice que el costo quedó en cero', () => {
        // Es lo único que el vendedor necesita entender: la venta se registró,
        // pero la ganancia de esa línea está inflada hasta que alguien la nombre.
        expect(avisoSinTipo(['Jordan'])).toContain('$0')
    })

    test('lista todos los artículos sueltos', () => {
        const texto = avisoSinTipo(['Jordan', 'yezzy'])
        expect(texto).toContain('Jordan')
        expect(texto).toContain('yezzy')
    })

    test('no repite el mismo artículo', () => {
        const texto = avisoSinTipo(['Jordan', 'Jordan'])
        expect(texto.match(/Jordan/g)).toHaveLength(1)
    })

    test('ignora entradas vacías', () => {
        expect(avisoSinTipo(['', '   '])).toBe('')
    })

    test('dice dónde se arregla', () => {
        expect(avisoSinTipo(['Jordan'])).toMatch(/tipos/i)
    })

    test('corta la lista cuando son muchos, sin ocultar cuántos son', () => {
        const muchos = ['a1', 'b2', 'c3', 'd4', 'e5', 'f6', 'g7']
        const texto = avisoSinTipo(muchos)
        expect(texto).toContain('a1')
        expect(texto).toContain('7')
        expect(texto.length).toBeLessThan(400)
    })
})
