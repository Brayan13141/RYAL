const fs = require('fs')
const os = require('os')
const path = require('path')
const { lockStatus, lockPathFor, isAlive } = require('./lock')

let tmpDir
beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lock-test-'))
})
afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true })
})

describe('lockPathFor', () => {
    test('el lockfile es hermano del dir de auth', () => {
        expect(lockPathFor('/root/app/bot/.baileys_auth'))
            .toBe(path.resolve('/root/app/bot/.baileys_auth') + '.lock')
    })
    test('dirs distintos -> lockfiles distintos (persona1 vs persona2)', () => {
        expect(lockPathFor('/root/app/bot/.baileys_auth'))
            .not.toBe(lockPathFor('/root/app/bot-p2/.baileys_auth'))
    })
})

describe('isAlive', () => {
    test('el propio proceso esta vivo', () => {
        expect(isAlive(process.pid)).toBe(true)
    })
    test('un PID inexistente no esta vivo', () => {
        expect(isAlive(2147483646)).toBe(false)
    })
})

describe('lockStatus', () => {
    const lockFile = () => path.join(tmpDir, '.baileys_auth.lock')

    test('sin lockfile -> free', () => {
        expect(lockStatus(lockFile()).action).toBe('free')
    })

    test('lock de un proceso vivo ajeno -> blocked', () => {
        // process.pid esta vivo; nos hacemos pasar por otro PID distinto
        fs.writeFileSync(lockFile(), String(process.pid))
        const status = lockStatus(lockFile(), process.pid + 1)
        expect(status.action).toBe('blocked')
        expect(status.owner).toBe(process.pid)
    })

    test('lock de un proceso muerto -> reclaim (huerfano)', () => {
        fs.writeFileSync(lockFile(), '2147483646')
        expect(lockStatus(lockFile()).action).toBe('reclaim')
    })

    test('lock propio -> reclaim', () => {
        fs.writeFileSync(lockFile(), String(process.pid))
        expect(lockStatus(lockFile()).action).toBe('reclaim')
    })

    test('lockfile vacio/corrupto -> reclaim', () => {
        fs.writeFileSync(lockFile(), '')
        expect(lockStatus(lockFile()).action).toBe('reclaim')
    })
})
