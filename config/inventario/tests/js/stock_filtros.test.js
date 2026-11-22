// Lógica de los filtros de Stock (modelo, talla, color). Correr: node --test inventario/tests/js/
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const f = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'inventario_stock.js'));

// Jordan 4 (1): negro 27 ×3 (2 aéreo + 1 marítimo), negro 26.5 ×1, rojo 27 ×2 · Air Force (2): negro 26.5 ×1, blanco 25 ×4
const E = [
  { modelo: '1', color: 'negro', talla: '27', via: 'aereo', n: 2 },
  { modelo: '1', color: 'negro', talla: '27', via: 'maritimo', n: 1 },
  { modelo: '1', color: 'negro', talla: '26.5', via: 'aereo', n: 1 },
  { modelo: '1', color: 'rojo', talla: '27', via: 'aereo', n: 2 },
  { modelo: '2', color: 'negro', talla: '26.5', via: 'aereo', n: 1 },
  { modelo: '2', color: 'blanco', talla: '25', via: 'aereo', n: 4 },
];
const sel = (o = {}) => ({ modelo: {}, talla: {}, color: {}, via: {}, ...o });
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
  assert.equal(f.pasa(E[3], s), true);
  assert.equal(f.pasa(E[0], s), false);
  assert.equal(f.pasa(E[0], s, 'color'), true);
});

test('la vía reduce tallas y colores', () => {
  assert.deepEqual(f.cuenta(E, sel({ via: set('maritimo') }), 'talla'), { '27': 1 });
});

test('la vía no se reduce a sí misma', () => {
  assert.deepEqual(f.cuenta(E, sel({ via: set('maritimo') }), 'via'), { aereo: 10, maritimo: 1 });
});

test('podar desmarca la vía que el modelo no tiene', () => {
  assert.deepEqual(f.podar(E, sel({ modelo: set('2'), via: set('maritimo') })).via, {});
});

test('podar tolera un sel sin via', () => {
  const s = f.podar(E, { modelo: set('1'), talla: {}, color: {} });
  assert.deepEqual(s.via, {});
  assert.equal(f.pasa(E[0], { modelo: {}, talla: {}, color: {} }), true);
});

test('desglose igual al servidor', () => {
  assert.equal(f.desglose({ aereo: 4 }), 'A');
  assert.equal(f.desglose({ aereo: 4, maritimo: 2 }), 'A4 M2');
  assert.equal(f.desglose({ maritimo: 1, aereo: 0, nacional: 3 }), 'M1 N3');
  assert.equal(f.desglose({}), '');
});

// elegir: qué fichas de la búsqueda se prenden. Gana el grupo más grande con pares juntos;
// a igual tamaño, el que conserva lo escrito primero.
const h = (dim, v) => ({ dim, v });

test('elegir prende todo lo que tiene pares junto', () => {
  assert.deepEqual(f.elegir(E, sel(), [h('modelo', '1'), h('color', 'negro'), h('talla', '27')]), [0, 1, 2]);
});

test('elegir no deja que lo primero escrito tape dos fichas compatibles', () => {
  // 25 solo existe en Air Force blanco; Jordan 4 negro sí existe: se quedan esos dos.
  assert.deepEqual(f.elegir(E, sel(), [h('talla', '25'), h('modelo', '1'), h('color', 'negro')]), [1, 2]);
});

test('elegir a igual tamaño se queda con lo escrito primero', () => {
  // 25 y rojo no tienen pares juntos; cada uno solo sí.
  assert.deepEqual(f.elegir(E, sel(), [h('talla', '25'), h('color', 'rojo')]), [0]);
  assert.deepEqual(f.elegir(E, sel(), [h('color', 'rojo'), h('talla', '25')]), [0]);
});

test('elegir respeta lo marcado a mano', () => {
  // Con Air Force marcado a mano, rojo no tiene pares; 25 sí.
  assert.deepEqual(f.elegir(E, sel({ modelo: set('2') }), [h('color', 'rojo'), h('talla', '25')]), [1]);
});

test('elegir con valores de la misma dimensión los suma', () => {
  assert.deepEqual(f.elegir(E, sel(), [h('talla', '25'), h('talla', '27')]), [0, 1]);
});

test('elegir descarta lo que no tiene ningún par', () => {
  assert.deepEqual(f.elegir(E, sel(), [h('color', 'verde'), h('talla', '27')]), [1]);
  assert.deepEqual(f.elegir(E, sel(), []), []);
});

test('elegir con más de 8 fichas prende de una en una en el orden del texto', () => {
  const hs = [h('talla', '25'), h('modelo', '1')].concat(Array(7).fill(h('talla', '25')));
  assert.deepEqual(f.elegir(E, sel(), hs), [0, 2, 3, 4, 5, 6, 7, 8]);
});
