// Lock por sesion de Baileys: impide que 2 procesos usen el mismo .baileys_auth
// a la vez. Dos procesos sobre la misma sesion provocan un 401 conflict en
// WhatsApp que invalida el login (hay que re-escanear el QR).
//
// El lockfile es hermano del directorio de auth y guarda el PID dueno:
//   /root/app/bot/.baileys_auth      -> /root/app/bot/.baileys_auth.lock
// Asi persona1 y persona2 (dirs distintos) no se bloquean entre si, pero un
// `node bot.js` manual en el mismo dir que el servicio systemd activo si lo hace.
const fs = require('fs')
const path = require('path')

// Ruta del lockfile para un directorio de auth dado.
function lockPathFor(authDir) {
    return path.resolve(authDir) + '.lock'
}

// True si existe un proceso con ese PID (senal 0 = solo comprueba existencia).
function isAlive(pid) {
    try {
        process.kill(pid, 0)
        return true
    } catch (e) {
        // EPERM = existe pero sin permiso para senalarlo -> sigue vivo
        return e.code === 'EPERM'
    }
}

// Decide que hacer con el lockfile sin tocar el estado. Testeable de forma pura.
//   'free'    -> no hay lock, se puede crear
//   'reclaim' -> hay lock pero es nuestro o de un proceso muerto (huerfano)
//   'blocked' -> hay lock de otro proceso vivo
function lockStatus(lockFile, myPid = process.pid) {
    if (!fs.existsSync(lockFile)) return { action: 'free' }
    const owner = parseInt(fs.readFileSync(lockFile, 'utf8').trim(), 10)
    if (!owner || owner === myPid) return { action: 'reclaim', owner }
    if (isAlive(owner)) return { action: 'blocked', owner }
    return { action: 'reclaim', owner }
}

// Adquiere el lock para authDir o termina el proceso con un mensaje claro.
// Registra la liberacion del lock al salir (incluye SIGINT/SIGTERM de systemd).
function acquireAuthLock(authDir) {
    const lockFile = lockPathFor(authDir)
    const status = lockStatus(lockFile)

    if (status.action === 'blocked') {
        console.error(
            `\n[X] Ya hay un proceso (PID ${status.owner}) usando la sesion ` +
            `"${path.basename(authDir)}".\n` +
            `    Correr dos a la vez invalida el login de WhatsApp (401 conflict).\n` +
            `    Si es el servicio systemd, detenlo primero:\n` +
            `      systemctl stop bot-persona1   (o bot-persona2)\n`
        )
        process.exit(1)
    }

    // 'free' o 'reclaim' (huerfano/propio): escribimos nuestro PID.
    fs.writeFileSync(lockFile, String(process.pid))

    const release = () => {
        try {
            const owner = parseInt(fs.readFileSync(lockFile, 'utf8').trim(), 10)
            if (owner === process.pid) fs.unlinkSync(lockFile)
        } catch (_) { /* ya no existe o ilegible: nada que hacer */ }
    }
    process.on('exit', release)
    for (const sig of ['SIGINT', 'SIGTERM']) {
        process.on(sig, () => { release(); process.exit(0) })
    }
    return lockFile
}

module.exports = { acquireAuthLock, lockStatus, lockPathFor, isAlive }
