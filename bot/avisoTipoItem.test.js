const { avisoTipoItem } = require('./avisoTipoItem')

describe('avisoTipoItem — el aviso en el momento de cargar el ítem', () => {
    test('nombra el texto exacto que no tiene tipo', () => {
        expect(avisoTipoItem('metcon')).toContain('«metcon»')
    })

    test('dice la consecuencia real: la venta no se va a poder cerrar', () => {
        // Desde el bloqueo de venta sin tipo, Django RECHAZA la venta con 409.
        // El aviso viejo hablaba de "costo $0", que ya no es lo que pasa.
        expect(avisoTipoItem('metcon')).toMatch(/no vas a poder cerrar|se va a rechazar/i)
    })

    test('manda a donde se arregla', () => {
        expect(avisoTipoItem('metcon')).toContain('Tipos de artículo')
    })

    test('sin descripción no hay nada que avisar', () => {
        expect(avisoTipoItem('')).toBe('')
        expect(avisoTipoItem(null)).toBe('')
        expect(avisoTipoItem('   ')).toBe('')
    })
})
