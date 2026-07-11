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

    test('archivo corrupto no revienta — empieza vacío', () => {
        fs.writeFileSync(tmpFile, '{no es json[')
        const store = createWelcomeStore({ filePath: tmpFile })
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(false)
        store.markSeen('a@s.whatsapp.net')
        expect(store.hasSeen('a@s.whatsapp.net')).toBe(true)
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
