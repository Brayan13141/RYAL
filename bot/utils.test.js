const { extractPrice, generateMessage, computeTotal } = require('./utils')

describe('extractPrice', () => {
    test('extrae precio con símbolo $', () => {
        expect(extractPrice('Nike Air Max 🔥 $350 disponible talla 42')).toBe(350)
    })
    test('extrae precio con "Precio:"', () => {
        expect(extractPrice('Jordan 1\nPrecio: $280\n✅ Stock limitado')).toBe(280)
    })
    test('extrae precio con "pesos"', () => {
        expect(extractPrice('Yeezy 350 🔥 450 pesos disponible')).toBe(450)
    })
    test('extrae precio de 3 dígitos sin símbolo', () => {
        expect(extractPrice('Sudadera G5 ✅ talla M/L precio 320')).toBe(320)
    })
    test('retorna null si no hay número de precio válido', () => {
        expect(extractPrice('Hola cómo estás, me confirmás?')).toBeNull()
    })
    test('retorna null si texto es null o vacío', () => {
        expect(extractPrice(null)).toBeNull()
        expect(extractPrice('')).toBeNull()
    })
    test('ignora números fuera del rango válido (< 50 o > 9999)', () => {
        expect(extractPrice('Talla 7 disponible')).toBeNull()
    })
    test('NO confunde números de modelo de tenis con precio (sin marcador)', () => {
        expect(extractPrice('New Balance 550 talla 42')).toBeNull()
        expect(extractPrice('Air Max 270 disponible')).toBeNull()
        expect(extractPrice('Yeezy 350 últimas piezas')).toBeNull()
    })
})

describe('computeTotal', () => {
    test('resta el descuento del precio', () => {
        expect(computeTotal(350, 50)).toBe(300)
    })
    test('nunca devuelve un total negativo', () => {
        expect(computeTotal(350, 500)).toBe(0)
    })
    test('sin descuento devuelve el precio completo', () => {
        expect(computeTotal(350, 0)).toBe(350)
    })
})

describe('generateMessage', () => {
    test('reemplaza precio con símbolo $', () => {
        const result = generateMessage('Nike Air Max 🔥 $350 disponible', 350, 450)
        expect(result).toBe('Nike Air Max 🔥 $450 disponible')
    })
    test('reemplaza precio con "pesos"', () => {
        const result = generateMessage('Jordan 1 - 450 pesos ✅ stock', 450, 550)
        expect(result).toBe('Jordan 1 - 550 pesos ✅ stock')
    })
    test('conserva emojis y resto del texto', () => {
        const original = 'Yeezy 350 🔥🔥 talla 40-44 $280 últimas piezas ✅'
        const result = generateMessage(original, 280, 380)
        expect(result).toContain('🔥🔥')
        expect(result).toContain('$380')
        expect(result).not.toContain('$280')
    })
    test('agrega precio al final si no puede reemplazar', () => {
        const result = generateMessage('Producto sin precio visible en texto', 300, 400)
        expect(result).toContain('$400')
    })
})
