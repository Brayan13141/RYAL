const { mensajeIncompleta, avisoSinStock } = require('./ventaIncompleta')

describe('mensajeIncompleta', () => {
    test('vacío o inválido → cadena vacía', () => {
        expect(mensajeIncompleta([])).toBe('')
        expect(mensajeIncompleta(undefined)).toBe('')
    })

    test('un detalle', () => {
        const txt = mensajeIncompleta([
            { texto: 'jordan 4 negro', falta: 'talla', mensaje: 'Falta la talla.' },
        ])
        expect(txt).toBe(
            '⏸️ La venta NO se registró:\n'
            + '«jordan 4 negro»: Falta la talla.'
            + '\nCorrige con /quitar N y vuelve a cargar el ítem.')
    })

    test('dos detalles, una línea cada uno', () => {
        const txt = mensajeIncompleta([
            { texto: 'a', falta: 'via', mensaje: 'Falta la vía.' },
            { texto: 'b', falta: 'color', mensaje: 'Falta el color.' },
        ])
        expect(txt).toContain('«a»: Falta la vía.\n«b»: Falta el color.')
        expect(txt.startsWith('⏸️ La venta NO se registró:\n')).toBe(true)
        expect(txt.endsWith('Corrige con /quitar N y vuelve a cargar el ítem.')).toBe(true)
    })
})

describe('avisoSinStock', () => {
    test('vacío → cadena vacía', () => {
        expect(avisoSinStock([])).toBe('')
        expect(avisoSinStock(undefined)).toBe('')
    })

    test('con avisos → separados por salto de línea', () => {
        expect(avisoSinStock([{ mensaje: 'uno' }, { mensaje: 'dos' }]))
            .toBe('\n\nuno\ndos')
    })
})
