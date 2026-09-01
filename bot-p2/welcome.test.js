const fs = require('fs')
const os = require('os')
const path = require('path')

const { WELCOME_MESSAGE, MENU_RESPONSES, menuReply, isGreetableJid, createWelcomeStore } = require('./welcome')

describe('menuReply', () => {
    test('opciones 1-3 devuelven su respuesta', () => {
        expect(menuReply('1')).toBe(MENU_RESPONSES[1])
        expect(menuReply('2')).toBe(MENU_RESPONSES[2])
        expect(menuReply('3')).toBe(MENU_RESPONSES[3])
    })

    test('tolera espacios alrededor', () => {
        expect(menuReply('  2  ')).toBe(MENU_RESPONSES[2])
    })

    test('"menu" y "menú" re-muestran la bienvenida', () => {
        expect(menuReply('menu')).toBe(WELCOME_MESSAGE)
        expect(menuReply('MENÚ')).toBe(WELCOME_MESSAGE)
    })

    test('texto libre, vacío u opción inexistente devuelven null', () => {
        expect(menuReply('hola')).toBeNull()
        expect(menuReply('4')).toBeNull()
        expect(menuReply('12')).toBeNull()
        expect(menuReply('')).toBeNull()
        expect(menuReply(null)).toBeNull()
    })

    test('número que es talla o precio no dispara menú', () => {
        expect(menuReply('26')).toBeNull()
        expect(menuReply('450')).toBeNull()
    })
})

describe('isGreetableJid', () => {
    test('privados normales y @lid son saludables', () => {
        expect(isGreetableJid('5214451112233@s.whatsapp.net')).toBe(true)
        expect(isGreetableJid('123456789@lid')).toBe(true)
    })

    test('grupos, broadcast y newsletter NO', () => {
        expect(isGreetableJid('120363424079631765@g.us')).toBe(false)
        expect(isGreetableJid('status@broadcast')).toBe(false)
        expect(isGreetableJid('999@newsletter')).toBe(false)
        expect(isGreetableJid('')).toBe(false)
        expect(isGreetableJid(null)).toBe(false)
    })
})

describe('createWelcomeStore', () => {
    let tmpFile

    beforeEach(() => {
        tmpFile = path.join(os.tmpdir(), `welcome-test-${Date.now()}-${Math.random()}.json`)
    })

    afterEach(() => {
        if (fs.existsSync(tmpFile)) fs.unlinkSync(tmpFile)
    })

    test('JID nuevo no está visto; tras markSeen sí', () => {
        const store = createWelcomeStore({ filePath: tmpFile })
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(false)
        store.markSeen('a@s.whatsapp.net')
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(true)
    })

    test('persiste entre instancias (restart del bot)', () => {
        const s1 = createWelcomeStore({ filePath: tmpFile })
        s1.markSeen('a@s.whatsapp.net')
        const s2 = createWelcomeStore({ filePath: tmpFile })
        expect(s2.hasSeen('a@s.whatsapp.net')).toBe(true)
        expect(s2.hasSeen('b@s.whatsapp.net')).toBe(false)
    })

    // REESCRITO (antes: 'archivo corrupto no revienta — empieza vacío').
    // Ese test afirmaba que un store corrupto arrancaba VACÍO, y eso es
    // justo el desastre: con 523 contactos ya saludados, arrancar vacío
    // significa volver a saludarlos a todos. Un archivo ilegible no es una
    // instalación nueva — es una que perdió sus datos, y la diferencia
    // importa. Sigue sin reventar, que era lo que el test protegía.
    test('archivo corrupto no revienta, pero queda SELLADO: no saluda a nadie', () => {
        fs.writeFileSync(tmpFile, '{no es json[')
        const store = createWelcomeStore({ filePath: tmpFile })
        expect(store.isSealed()).toBe(true)
        expect(store.hasSeen('cualquiera@lid')).toBe(true)
    })

    test('archivo AUSENTE sí arranca vacío — es una instalación nueva', () => {
        const store = createWelcomeStore({ filePath: tmpFile })
        expect(store.isSealed()).toBe(false)
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(false)
    })

    test('sembrar en bulk levanta el sello', () => {
        fs.writeFileSync(tmpFile, '{no es json[')
        const store = createWelcomeStore({ filePath: tmpFile })
        store.markSeenBulk(['a@lid', 'b@lid'])
        expect(store.isSealed()).toBe(false)
        expect(store.hasSeen('a@lid')).toBe(true)
        expect(store.hasSeen('c@lid')).toBe(false)
    })

    test('sin filePath funciona en memoria', () => {
        const store = createWelcomeStore({})
        store.markSeen('a@s.whatsapp.net')
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(true)
    })

    test('respeta maxEntries expulsando el más viejo', () => {
        const store = createWelcomeStore({ filePath: tmpFile, maxEntries: 2 })
        store.markSeen('a@x')
        store.markSeen('b@x')
        store.markSeen('c@x')
        expect(store.size()).toBe(2)
        expect(store.hasSeen('a@x')).toBe(false)
        expect(store.hasSeen('c@x')).toBe(true)
    })

    test('markSeen duplicado no duplica', () => {
        const store = createWelcomeStore({ filePath: tmpFile })
        store.markSeen('a@x')
        store.markSeen('a@x')
        expect(store.size()).toBe(1)
    })

    test('markSeenBulk marca varios JIDs y persiste', () => {
        const s1 = createWelcomeStore({ filePath: tmpFile })
        s1.markSeenBulk(['a@s.whatsapp.net', 'b@s.whatsapp.net', 'a@s.whatsapp.net'])
        expect(s1.size()).toBe(2)
        const s2 = createWelcomeStore({ filePath: tmpFile })
        expect(s2.hasSeen('a@s.whatsapp.net')).toBe(true)
        expect(s2.hasSeen('b@s.whatsapp.net')).toBe(true)
    })

    test('markSeenBulk con lista vacía o undefined no rompe', () => {
        const store = createWelcomeStore({ filePath: tmpFile })
        store.markSeenBulk([])
        store.markSeenBulk(undefined)
        expect(store.size()).toBe(0)
    })
})


describe('isGreetableJid — números internos', () => {
    // Los privados llegan como `@lid` y la key del mensaje NO trae el teléfono
    // (verificado en produccion: {remoteJid:'154211253772535@lid', fromMe, id}).
    // Por eso la exclusión es por LID exacto y no por número.
    const INTERNOS = new Set(['154211253772535@lid'])

    test('un LID interno NO es saludable', () => {
        expect(isGreetableJid('154211253772535@lid', INTERNOS)).toBe(false)
    })

    test('un LID de cliente sigue siendo saludable', () => {
        expect(isGreetableJid('99999999999@lid', INTERNOS)).toBe(true)
    })

    test('sin lista de internos se comporta como antes', () => {
        expect(isGreetableJid('154211253772535@lid')).toBe(true)
        expect(isGreetableJid('120363411985798072@g.us')).toBe(false)
    })
})

describe('isGreetableJid usado como callback de .filter()', () => {
    // bot.js hace `.filter(isGreetableJid)` y Array.filter pasa
    // (elemento, indice, array). El segundo parametro llega como NUMERO, no
    // como Set, asi que la exclusion de internos no puede asumir que le pasan
    // un Set: con indice >= 1 un `.has()` a secas revienta con TypeError.
    test('no revienta y filtra bien cuando se pasa directo a .filter', () => {
        const jids = ['a@lid', 'b@lid', 'c@s.whatsapp.net', 'x@g.us', 'y@broadcast']
        expect(jids.filter(isGreetableJid)).toEqual(['a@lid', 'b@lid', 'c@s.whatsapp.net'])
    })
})
