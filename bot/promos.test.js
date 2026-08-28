const { matchPromo, GORRAS_PROMO } = require('./promos')

describe('matchPromo', () => {
    test('reconoce /gorras', () => {
        expect(matchPromo('/gorras')).toBe(GORRAS_PROMO)
    })

    test('es insensible a mayúsculas y espacios alrededor', () => {
        expect(matchPromo('/GORRAS')).toBe(GORRAS_PROMO)
        expect(matchPromo('  /Gorras  ')).toBe(GORRAS_PROMO)
    })

    test('NO dispara si el comando va dentro de otro texto', () => {
        expect(matchPromo('necesito /gorras urgente')).toBeNull()
        expect(matchPromo('/gorras nuevas')).toBeNull()
    })

    test('no revienta con entradas vacías o no-string', () => {
        expect(matchPromo('')).toBeNull()
        expect(matchPromo(null)).toBeNull()
        expect(matchPromo(undefined)).toBeNull()
        expect(matchPromo(123)).toBeNull()
    })

    test('un comando desconocido devuelve null', () => {
        expect(matchPromo('/tenis')).toBeNull()
    })

    // Anti-bucle: el bot publica GORRAS_PROMO en el mismo grupo que escucha.
    // Ese mensaje propio vuelve a entrar al handler; si matcheara, se publicaría
    // otra vez y así infinitamente, haciendo spam al grupo de clientes.
    test('el propio texto de la promo NO matchea', () => {
        expect(matchPromo(GORRAS_PROMO)).toBeNull()
    })
})

describe('GORRAS_PROMO', () => {
    test('lleva los datos de venta que aprobó Bryan', () => {
        expect(GORRAS_PROMO).toContain('*$330 Mayoreo*')
        expect(GORRAS_PROMO).toContain('A partir de 50 piezas el precio se reduce $50 c/p')
        expect(GORRAS_PROMO).toContain('*New Era* o *Barbas y Dandy*')
        expect(GORRAS_PROMO).toContain('ryalsneackers.com')
    })

    // Regresión: el cierre de gorras es propio, NO el RYAL_FOOTER de utils.js.
    // Ese pide "las tallas" y las gorras son de talla ajustable.
    test('el cierre no pide tallas', () => {
        expect(GORRAS_PROMO).toContain('🔥 Reenvía la imagen del modelo que quieres para tu pedido 🔥')
        expect(GORRAS_PROMO).not.toContain('las tallas')
    })
})
