/**
 * Regla de enrutamiento de esta instancia (bot-p3, el número de ventas extra).
 *
 * Atiende UNA sola cosa: los mensajes PROPIOS del Grupo Pedidos.
 *
 * Que solo procese `fromMe` no es una restricción, es el motivo de existir de
 * esta instancia. El bot no puede descifrar a terceros — es el bug abierto de
 * Baileys #1769, la migración de WhatsApp a LID — pero los mensajes de su
 * propia cuenta no necesitan ese apretón de manos. Con una instancia por
 * operador, cada uno da comandos desde su número sin depender de sesiones
 * cruzadas que hoy no se establecen.
 *
 * Y hay una segunda razón, de dinero: bot-p2 recibe estos mismos mensajes de
 * grupo. Si las dos instancias procesaran a los ajenos, el día que el
 * descifrado se arregle una venta se registraría DOS veces. Filtrar por
 * `fromMe` lo vuelve imposible por construcción, no por suerte.
 */
function shouldHandleOrders(msg, ordersGid) {
    if (!ordersGid) return false              // feature deshabilitada sin el env
    if (!msg?.message) return false           // sin contenido no hay comando
    if (!msg.key?.fromMe) return false        // ajeno: lo atiende su propia instancia
    return msg.key.remoteJid === ordersGid
}

module.exports = { shouldHandleOrders }
