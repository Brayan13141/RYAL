const { extractPrice, computeTotal, markupCaption, cleanCaption, buildRyalForward, buildImageCaption, parseModaArgs } = require('./utils')

// Mensajes reales del proveedor (capturados 2026-06-01)
const MSG_PUMA = `⚜️ *PUMA SUEDE XL*⚜️
ℂ𝔸𝕃𝕀𝔻𝔸𝔻 ℙℝ𝔼𝕄𝕀𝕌𝕄
▪️#2 al 5
▪️ *$320 Mayoreo*
▪️Caja de su marca
▪️Dama & Juvenil
👁️ Ojo: *A partir de una media corrida el precio es de $270 c/p*
Ejemplo como viene la media corrida
1pz del 3
2pz del 4
2pz del 5
1pz del 6`

const MSG_GUESS = `✨𝕊𝕒𝕟𝕕𝕒𝕝𝕚𝕒✨
        GUESS
Mod-calcetín
▪️#3 al 6
▪️ 250 Mayoreo
▪️Dama & Juvenil 👩🏻
👁️ Ojo: A partir de una media corrida el precio es de $200 c/p`

const MSG_MK = `✨MICHAEL KORS ✨
▪️#3 al 6
💲 Mayoreo general: $250
👁️ Ojo: A partir de una media corrida el precio es de $200 c/p`

const MSG_HUGO = `💎BOTAS HUGO💎
▪️#6 al 9
▪️ $550 Mayoreo
👁️ Ojo: A partir de una media corrida el precio es de $500c/p`

// Mensaje real del proveedor con precios de paquete (capturado 2026-07-04)
const MSG_PAQUETE = `*Pantalon de mezclilla*
*AMERICAN EAGLE*
!! *_UNICAS PIEZAS_*!!
*Calidad G5 exportación*
▪️Todo grabado
▪️Botones  grabados
▪️Etiqueta de la marca por dentro
▪️Etiqueta igualita a la original
*Tallas disponibles en cada foto*
normal a la talla

!! *SÚPER PAQUETE EMPRENDEDOR*!!
_Paquete 1: *10* pz  por $4,750  $575c/u_
_Paquete 2: *20* pz $9,200 $560 c/u_
_Paquete 3: *50* pz $22,500 $550 c/u_
*$600  Mayoreo*
*$700 menudeo*`

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
    test('reconoce "Mayoreo" sin símbolo $', () => {
        expect(extractPrice('▪️ 250 Mayoreo')).toBe(250)
    })
    test('en multi-precio devuelve el primero (mayoreo)', () => {
        expect(extractPrice(MSG_PUMA)).toBe(320)
    })
    test('retorna null si no hay número de precio válido', () => {
        expect(extractPrice('Hola cómo estás, me confirmás?')).toBeNull()
    })
    test('retorna null si texto es null o vacío', () => {
        expect(extractPrice(null)).toBeNull()
        expect(extractPrice('')).toBeNull()
    })
    test('ignora números fuera del rango válido (< 50 o > 99999)', () => {
        expect(extractPrice('Talla 7 disponible')).toBeNull()
    })
    test('extrae precio de 5 dígitos con marcador', () => {
        expect(extractPrice('$12000 mayoreo')).toBe(12000)
        expect(extractPrice('precio: $15000')).toBe(15000)
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

describe('markupCaption', () => {
    test('marca TODOS los precios del mensaje (+100)', () => {
        const out = markupCaption(MSG_PUMA, 100)
        expect(out).toContain('$420')   // mayoreo 320 -> 420
        expect(out).toContain('$370')   // media corrida 270 -> 370
        expect(out).not.toContain('$320')
        expect(out).not.toContain('$270')
    })
    test('marca precio "Mayoreo" sin símbolo $', () => {
        expect(markupCaption('▪️ 250 Mayoreo', 100)).toContain('350 Mayoreo')
    })
    test('marca "Mayoreo general: $250"', () => {
        expect(markupCaption(MSG_MK, 100)).toContain('$350')
    })
    test('marca "$500c/p" sin espacio', () => {
        const out = markupCaption(MSG_HUGO, 100)
        expect(out).toContain('$650')     // 550 -> 650
        expect(out).toContain('$600c/p')  // 500 -> 600
    })
    test('NO toca tallas ni números de modelo', () => {
        const out = markupCaption(MSG_PUMA, 100)
        expect(out).toContain('#2 al 5')
        expect(out).toContain('1pz del 3')
        expect(out).toContain('2pz del 4')
    })
    test('NO toca "Modelo-013" ni "Mod-01"', () => {
        expect(markupCaption('Modelo-013\nMod-01', 100)).toBe('Modelo-013\nMod-01')
    })
    test('aplica markup a precio de 5 dígitos', () => {
        expect(markupCaption('$12000 mayoreo', 100)).toContain('$12100')
    })
    test('precio de paquete: total se recalcula proporcional a la cantidad (cantidad x (c/u + markup)), no +markup plano', () => {
        const out = markupCaption(MSG_PAQUETE, 100)
        // c/u ya se marca igual que un precio individual
        expect(out).toContain('$675c/u')   // 575 -> 675
        expect(out).toContain('$660 c/u')  // 560 -> 660
        expect(out).toContain('$650 c/u')  // 550 -> 650
        // total del paquete = cantidad x c/u marcado, no el original +100 plano
        expect(out).toContain('$6,750')    // 10 x 675
        expect(out).toContain('$13,200')   // 20 x 660
        expect(out).toContain('$32,500')   // 50 x 650
        expect(out).not.toContain('$4,750')  // total original sin marcar (bug)
        expect(out).not.toContain('$9,200')
        expect(out).not.toContain('$22,500')
        expect(out).not.toContain('$4,850')  // +100 plano (fórmula incorrecta)
        expect(out).not.toContain('$9,300')
        expect(out).not.toContain('$22,600')
        // precios individuales sin relación a paquete siguen igual
        expect(out).toContain('$700  Mayoreo')
        expect(out).toContain('$800 menudeo')
    })
})

describe('cleanCaption', () => {
    test('quita emojis pictográficos', () => {
        const out = cleanCaption(MSG_PUMA)
        expect(out).not.toContain('⚜')
        expect(out).not.toContain('👁')
    })
    test('conserva las viñetas ▪️', () => {
        expect(cleanCaption(MSG_PUMA)).toContain('▪')
    })
    test('normaliza la fuente decorativa a ASCII', () => {
        const out = cleanCaption(MSG_PUMA)
        expect(out).toContain('CALIDAD PREMIUM')
        expect(out).not.toContain('ℂ𝔸𝕃𝕀𝔻𝔸𝔻')
    })
})

describe('buildRyalForward', () => {
    test('pipeline completo: markup + limpieza + pie Ryal', () => {
        const out = buildRyalForward(MSG_PUMA, 100)
        // precios marcados
        expect(out).toContain('$420')
        expect(out).toContain('$370')
        // limpieza
        expect(out).not.toContain('⚜')
        expect(out).toContain('CALIDAD PREMIUM')
        // viñetas conservadas
        expect(out).toContain('▪')
        // tallas intactas
        expect(out).toContain('#2 al 5')
        // pie de Ryal con sus 2 emojis
        expect(out).toContain('↪️ Reenvía esta imagen con las tallas que quieres para tu pedido.')
        expect(out).toContain('🌐 ryalsneackers.com')
    })
})

describe('buildImageCaption', () => {
    test('incluye el precio con $ y el pie de Ryal', () => {
        const cap = buildImageCaption(400)
        expect(cap).toContain('$400')
        expect(cap).toContain('ryalsneackers.com')
    })

    test('round-trip: extractPrice lee el precio del caption generado', () => {
        expect(extractPrice(buildImageCaption(450))).toBe(450)
    })
})

describe('parseModaArgs', () => {
    test('parsea cliente + cantidad + ganancia sin envío', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', '100'])).toEqual({
            query: 'Victor', cantidad: 12, ganancia: 100, envio: 0,
        })
    })

    test('parsea nombre de cliente con varias palabras', () => {
        expect(parseModaArgs(['Juan', 'García', 'moda', '3', '50'])).toEqual({
            query: 'Juan García', cantidad: 3, ganancia: 50, envio: 0,
        })
    })

    test('parsea ganancia decimal', () => {
        expect(parseModaArgs(['Victor', 'moda', '2', '100.50'])).toEqual({
            query: 'Victor', cantidad: 2, ganancia: 100.5, envio: 0,
        })
    })

    test('parsea envío opcional', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', '100', 'envio=50'])).toEqual({
            query: 'Victor', cantidad: 12, ganancia: 100, envio: 50,
        })
    })

    test('reconoce MODA en mayúsculas', () => {
        expect(parseModaArgs(['Victor', 'MODA', '12', '100'])).toEqual({
            query: 'Victor', cantidad: 12, ganancia: 100, envio: 0,
        })
    })

    test('sin token moda devuelve null', () => {
        expect(parseModaArgs(['Victor', 'García'])).toBeNull()
    })

    test('sin cliente antes de moda devuelve null', () => {
        expect(parseModaArgs(['moda', '12', '100'])).toBeNull()
    })

    test('falta ganancia devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12'])).toBeNull()
    })

    test('cantidad no entera devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12.5', '100'])).toBeNull()
    })

    test('cantidad cero o negativa devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '0', '100'])).toBeNull()
    })

    test('ganancia negativa devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', '-5'])).toBeNull()
    })

    test('ganancia no numérica devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', 'abc'])).toBeNull()
    })

    test('formato de envío inválido devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', '100', 'envio=abc'])).toBeNull()
    })

    test('argumentos extra después de envío devuelve null', () => {
        expect(parseModaArgs(['Victor', 'moda', '12', '100', 'envio=50', 'extra'])).toBeNull()
    })
})
