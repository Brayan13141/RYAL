// Lógica de los filtros de Stock (modelo, talla, color). Correr: node --test inventario/tests/js/
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const f = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'inventario_stock.js'));

// Jordan 4 (1): negro 27 ×3, negro 26.5 ×1, rojo 27 ×2 · Air Force (2): negro 26.5 ×1, blanco 25 ×4
const E = [
  { modelo: '1', color: 'negro', talla: '27', n: 3 },
  { modelo: '1', color: 'negro', talla: '26.5', n: 1 },
  { modelo: '1', color: 'rojo', talla: '27', n: 2 },
  { modelo: '2', color: 'negro', talla: '26.5', n: 1 },
  { modelo: '2', color: 'blanco', talla: '25', n: 4 },
];
const sel = (o = {}) => ({ modelo: {}, talla: {}, color: {}, ...o });
const set = (...vs) => Object.fromEntries(vs.map(v => [v, true]));

test('sin filtros cuenta todo por talla y por color', () => {
  assert.deepEqual(f.cuenta(E, sel(), 'talla'), { '27': 5, '26.5': 2, '25': 4 });
  assert.deepEqual(f.cuenta(E, sel(), 'color'), { negro: 5, rojo: 2, blanco: 4 });
});

test('el modelo reduce tallas y colores a los suyos', () => {
  const s = sel({ modelo: set('2') });
  assert.deepEqual(f.cuenta(E, s, 'talla'), { '26.5': 1, '25': 4 });
  assert.deepEqual(f.cuenta(E, s, 'color'), { negro: 1, blanco: 4 });
});

test('varios modelos suman', () => {
  const s = sel({ modelo: set('1', '2') });
  assert.deepEqual(f.cuenta(E, s, 'talla'), { '27': 5, '26.5': 2, '25': 4 });
});

test('el color reduce las tallas', () => {
  assert.deepEqual(f.cuenta(E, sel({ color: set('negro') }), 'talla'), { '27': 3, '26.5': 2 });
  assert.deepEqual(f.cuenta(E, sel({ modelo: set('1'), color: set('rojo') }), 'talla'), { '27': 2 });
});

test('la talla reduce los colores', () => {
  assert.deepEqual(f.cuenta(E, sel({ talla: set('26.5') }), 'color'), { negro: 2 });
});

test('cada filtro no se reduce a sí mismo', () => {
  // Con "negro" marcado, los demás colores siguen ofreciéndose.
  assert.deepEqual(f.cuenta(E, sel({ color: set('negro') }), 'color'), { negro: 5, rojo: 2, blanco: 4 });
});

test('podar desmarca la talla y el color que el modelo no tiene', () => {
  const s = f.podar(E, sel({ modelo: set('2'), talla: set('27', '25'), color: set('rojo', 'blanco') }));
  assert.deepEqual(s.talla, set('25'));
  assert.deepEqual(s.color, set('blanco'));
  assert.deepEqual(s.modelo, set('2'));
});

test('podar no toca lo que sigue disponible ni el estado original', () => {
  const original = sel({ modelo: set('1'), talla: set('27'), color: set('negro') });
  const s = f.podar(E, original);
  assert.deepEqual(s.talla, set('27'));
  assert.deepEqual(s.color, set('negro'));
  assert.notEqual(s, original);
});

test('podar una talla y un color que solo se excluyen entre sí deja al menos uno', () => {
  // Rojo solo existe en 27; si la talla 25 y el rojo están marcados, se cae la talla primero
  // y el rojo sobrevive (con la talla podada el rojo vuelve a tener pares).
  const s = f.podar(E, sel({ talla: set('25'), color: set('rojo') }));
  assert.deepEqual(s.talla, {});
  assert.deepEqual(s.color, set('rojo'));
});

test('pasa respeta los tres filtros', () => {
  const s = sel({ modelo: set('1'), talla: set('27'), color: set('rojo') });
  assert.equal(f.pasa(E[2], s), true);
  assert.equal(f.pasa(E[0], s), false);
  assert.equal(f.pasa(E[0], s, 'color'), true);
});
