const { shouldHandleOrders } = require('./routing')

const ORDERS = '120363411985798072@g.us'
const OTRO_GRUPO = '120363424079631765@g.us'

// Un mensaje tal como lo entrega Baileys: lo unico que importa acá es la key.
const msg = ({ fromMe, remoteJid, participant }) => ({
    key: { fromMe, remoteJid, participant },
    message: { conversation: '/ayuda' },
})

describe('shouldHandleOrders', () => {
    test('procesa los mensajes PROPIOS del Grupo Pedidos', () => {
        expect(shouldHandleOrders(
            msg({ fromMe: true, remoteJid: ORDERS }), ORDERS,
        )).toBe(true)
    })

    test('IGNORA a los demas participantes del Grupo Pedidos', () => {
        // La propiedad que define a esta instancia. bot-p2 tambien recibe este
        // mensaje; si las dos lo procesaran, la venta se registraria DOS veces.
        expect(shouldHandleOrders(
            msg({ fromMe: false, remoteJid: ORDERS, participant: '100000000000001@lid' }),
            ORDERS,
        )).toBe(false)
    })

    test('ignora los mensajes propios de OTROS grupos', () => {
        expect(shouldHandleOrders(
            msg({ fromMe: true, remoteJid: OTRO_GRUPO }), ORDERS,
        )).toBe(false)
    })

    test('ignora los chats privados, propios o ajenos', () => {
        // Esta instancia no atiende clientes: no saluda, no responde privados.
        expect(shouldHandleOrders(
            msg({ fromMe: true, remoteJid: '5215550000003@s.whatsapp.net' }), ORDERS,
        )).toBe(false)
        expect(shouldHandleOrders(
            msg({ fromMe: false, remoteJid: '100000000000001@lid' }), ORDERS,
        )).toBe(false)
    })

    test('sin ORDERS_GROUP_ID configurado no procesa nada', () => {
        expect(shouldHandleOrders(msg({ fromMe: true, remoteJid: ORDERS }), undefined)).toBe(false)
        expect(shouldHandleOrders(msg({ fromMe: true, remoteJid: ORDERS }), '')).toBe(false)
    })

    test('ignora un mensaje sin contenido', () => {
        expect(shouldHandleOrders(
            { key: { fromMe: true, remoteJid: ORDERS } }, ORDERS,
        )).toBe(false)
    })
})
