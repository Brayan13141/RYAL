// Sugerencias de modelos bajo el buscador de Stock. Correr: node --test inventario/tests/js/sugerencias.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const f = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'inventario_stock.js'));

const MODELOS = [
  { clave: '1', nombre: 'JORDAN 4' }, { clave: '2', nombre: 'Jordan 1' },
  { clave: '3', nombre: 'Nike 270' }, { clave: '4', nombre: 'Café Racer' },
  { clave: '5', nombre: 'Jordan 11' }, { clave: '6', nombre: 'Jordan 3' },
  { clave: '7', nombre: 'Jordan 5' }, { clave: '8', nombre: 'Jordan 6' },
];
const PARES = { 1: 12, 2: 3, 3: 7, 4: 2, 5: 1, 6: 4, 7: 9, 8: 5 };
const claves = (l) => l.map((s) => s.clave);
const JORDANS = ['1', '2', '5', '6', '7', '8'];

test('distancia cuenta transposición como 1', () => {
  assert.equal(f.distancia('jodran', 'jordan'), 1);
  assert.equal(f.distancia('jordn', 'jordan'), 1);
  assert.equal(f.distancia('nike', 'nike'), 0);
});

test('prefijo sugiere', () => {
  const c = claves(f.sugerir(['jor'], MODELOS, PARES, {}));
  assert.ok(c.includes('1') && c.includes('2'));
  assert.ok(!c.includes('3'));
});

test('error de dedo sugiere', () => {
  assert.ok(JORDANS.includes(f.sugerir(['jordn'], MODELOS, PARES, {})[0].clave));
  assert.notEqual(f.sugerir(['jodan'], MODELOS, PARES, {}).length, 0);
});

test('el número exacto ordena', () => {
  const s = f.sugerir(['jordn', '4'], MODELOS, PARES, {});
  assert.equal(s[0].clave, '1');
  assert.deepEqual(s[0].usadas, [0, 1]);
});

test('un número solo no sugiere', () => {
  assert.deepEqual(f.sugerir(['4'], MODELOS, PARES, {}), []);
});

test('sin pares no se sugiere', () => {
  const sinUno = Object.assign({}, PARES);
  delete sinUno['1'];
  assert.ok(!claves(f.sugerir(['jordan'], MODELOS, sinUno, {})).includes('1'));
});

test('ya prendido no se sugiere', () => {
  assert.ok(!claves(f.sugerir(['jordan'], MODELOS, PARES, { 1: true })).includes('1'));
});

test('máximo 5 y más pares primero', () => {
  const s = f.sugerir(['jordan'], MODELOS, PARES, {});
  assert.deepEqual(s.map((x) => x.n), [12, 9, 5, 4, 3]);
});

test('palabra corta sin error', () => {
  assert.ok(JORDANS.includes(f.sugerir(['jo'], MODELOS, PARES, {})[0].clave));
  assert.deepEqual(f.sugerir(['jx'], MODELOS, PARES, {}), []);
});

test('acentos', () => {
  assert.equal(f.sugerir(['cafe'], MODELOS, PARES, {})[0].clave, '4');
});

test('interpretar da sobraPos', () => {
  const r = f.interpretar('jordn 4 negro', { modelos: MODELOS, colores: [{ clave: 'negro', nombre: 'Negro' }], tallas: [] });
  assert.deepEqual(r.sobra, ['jordn', '4']);
  assert.deepEqual(r.sobraPos, [0, 1]);
});

test('reemplaza con espacios de más', () => {
  assert.equal(f.reemplazarPalabras('  jordn   4 ', [0, 1], 'JORDAN 4'), 'JORDAN 4');
});

test('reemplaza palabras separadas', () => {
  assert.equal(f.reemplazarPalabras('jordn negro 4', [0, 2], 'JORDAN 4'), 'JORDAN 4 negro');
});

test('lo elegido lo entiende interpretar', () => {
  const cat = { modelos: MODELOS, colores: [], tallas: [] };
  const r = f.interpretar(f.reemplazarPalabras('cafe racr', [0, 1], 'Café Racer'), cat);
  assert.ok(r.modelo['4']);
  assert.deepEqual(r.sobra, []);
});

test('al elegir un nombre largo se llevan las palabras vecinas que ya son parte de él', () => {
  const cat = { modelos: [{ clave: '1', nombre: 'Jordan 4' }, { clave: '9', nombre: 'Jordan 4 Retro' }], colores: [], tallas: [] };
  const r = f.interpretar('jordan 4 retr', cat);
  const pos = f.ampliarPosiciones('jordan 4 retr', [r.sobraPos[0]], 'Jordan 4 Retro');
  const texto = f.reemplazarPalabras('jordan 4 retr', pos, 'Jordan 4 Retro');
  assert.equal(texto, 'Jordan 4 Retro');
  assert.deepEqual(Object.keys(f.interpretar(texto, cat).modelo), ['9']);
});

test('ampliar no se lleva palabras que no son del nombre', () => {
  assert.deepEqual(f.ampliarPosiciones('negro jordn 4 27', [1, 2], 'JORDAN 4'), [1, 2]);
});
