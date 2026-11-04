const { mensajeIncompleta, avisoSinStock, mensajeVentaIniciada } = require('./ventaIncompleta')

describe('mensajeVentaIniciada', () => {
    const txt = mensajeVentaIniciada()

    test('conserva el encabezado, el formato general y los comandos', () => {
        expect(txt.startsWith('🏪 *Venta tienda iniciada — Mostrador*\n')).toBe(true)
        expect(txt).toContain('Ingresa ítems: <cantidad> <precio>  o  <descripción> <precio>  o  <cantidad> <descripción> <precio>')
        expect(txt).toContain('📌 _Comandos:_ /items · /quitar <N> · /descuento <CÓDIGO> · /cerrar · /cancelar · /ayuda')
    })

    test('explica cómo registrar tenis con un ejemplo completo', () => {
        expect(txt).toContain('👟 *Tenis:* <modelo> <vía> <color> t<talla> <precio>')
        expect(txt).toContain('Ej: jordan 4 aereo negro total t26 1200')
        expect(txt).toContain('Vía: aereo · maritimo · nacional')
    })

    test('el ejemplo termina en precio y la talla lleva t (lo que exigen el parser y Django)', () => {
        const ejemplo = txt.split('\n').find(l => l.startsWith('Ej: ')).slice(4).split(' ')
        expect(Number(ejemplo[ejemplo.length - 1])).toBeGreaterThan(0)
        expect(ejemplo).toContain('t26')
        expect(/^\d+$/.test(ejemplo[0])).toBe(false)
    })
})

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
