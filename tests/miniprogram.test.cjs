const test = require('node:test');
const assert = require('node:assert/strict');
const bank = require('../miniprogram/data/bank');
const { makePaper, grade, publicQuestion } = require('../miniprogram/lib/exam');

test('100 unique questions, three kinds, shuffled options retain answer texts', () => {
  const paper = makePaper(bank);
  assert.equal(paper.length, 100);
  assert.equal(new Set(paper.map(q => q.number)).size, 100);
  assert.equal(new Set(paper.map(q => q.kind)).size, 3);
  paper.forEach(q => {
    const original = bank.find(v => v.number === q.number);
    assert.deepEqual(q.answers.map(a => q.options[a]).sort(), original.answers.map(a => original.options[a]).sort());
  });
});

test('independent score, exact multi-select, answer key not exposed on public question', () => {
  const paper = makePaper(bank);
  const answers = Object.fromEntries(paper.map((q, i) => [i, q.answers]));
  assert.equal(grade(paper, answers), 100);
  const multi = paper.findIndex(q => q.kind === '多选');
  answers[multi] = answers[multi].slice(0, 1);
  assert.equal(grade(paper, answers), 99);
  assert.equal(publicQuestion(paper, 0, answers).answers, undefined);
  delete answers[multi];
  assert.throws(() => grade(paper, answers), /不完整/);
});

test('duplicate option texts retain their original labels during simulation', () => {
  const q = { number: 433, kind: '多选', stem: '重复选项合成回归题', options: { A: '甲', B: '相同', C: '丙', D: '相同' }, answers: ['A', 'B', 'D'] };
  const source = bank.slice(0, 99).concat(q);
  const paper = makePaper(source, () => 0.7);
  const actual = paper.find(item => item.number === 433);
  assert.deepEqual(actual.options, q.options);
  assert.deepEqual(actual.answers, q.answers);
});

test('globally conflicting answer mappings also keep their original option layouts', () => {
  const first = { number: 9001, kind: '单选', stem: '相同题干', options: { A: '甲', B: '乙', C: '丙' }, answers: ['A'] };
  const second = { ...first, number: 9002, options: { A: '乙', B: '甲', C: '丙' } };
  const paper = makePaper(bank.slice(0, 98).concat(first, second));
  [first, second].forEach(source => {
    const actual = paper.find(q => q.number === source.number);
    assert.deepEqual(actual.options, source.options);
    assert.deepEqual(actual.answers, source.answers);
  });
});

test('native mini-program Page: login, full exam, history and logout', () => {
  let page;
  global.Page = definition => { page = definition; };
  global.wx = { showModal: options => options.success({ confirm: true }) };
  require('../miniprogram/pages/index/index');
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = patch => Object.assign(page.data, patch);
  page.onLoad();
  page.chooseDistrict({ currentTarget: { dataset: { district: '黔江区' } } });
  page.inputName({ detail: { value: '合成测试考生' } });
  page.inputIdentity({ detail: { value: '110101199001010010' } });
  page.changeAgreement({ detail: { value: ['agree'] } });
  page.login();
  assert.equal(page.data.stage, 'home');
  assert.equal(page.data.identity, '');
  page.openExam();
  page.startExam();
  page.askSubmit();
  assert.match(page.data.error, /100/);
  for (let i = 0; i < 100; i++) {
    page.saveAnswer(page._paper[i].answers);
    if (i < 99) page.next();
  }
  page.askSubmit();
  assert.equal(page.data.stage, 'result');
  assert.equal(page.data.result.score, 100);
  page.submit();
  assert.equal(page._history.length, 1);
  page.openHistory();
  assert.equal(page.data.history.length, 1);
  assert.equal(page.data.history[0].identity, undefined);
  page.logout();
  assert.equal(page.data.candidate, null);
  assert.equal(page._identity, null);
  assert.equal(page.data.stage, 'region');
});
