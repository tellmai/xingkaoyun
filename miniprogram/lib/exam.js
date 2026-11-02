function shuffle(items, random = Math.random) {
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function makePaper(bank, random = Math.random) {
  if (bank.length < 100 || new Set(bank.map(q => q.number)).size !== bank.length) {
    throw new Error('题库需要至少 100 道题号唯一的试题');
  }
  const normalize = text => text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
  const textKey = q => JSON.stringify([q.kind, normalize(q.stem), Object.values(q.options).map(normalize).sort()]);
  const mappings = new Map();
  bank.forEach(q => {
    const key = textKey(q);
    const correct = JSON.stringify(Array.from(new Set(q.answers.map(label => normalize(q.options[label])))).sort());
    if (mappings.has(key) && mappings.get(key) !== correct) mappings.set(key, null);
    else if (!mappings.has(key)) mappings.set(key, correct);
  });
  const selected = [];
  ['单选', '多选', '判断'].forEach((kind, index) => {
    const group = bank.filter(q => q.kind === kind);
    if (!group.length) throw new Error('题库需要单选、多选、判断三种题型');
    selected.push(...shuffle(group, random).slice(0, index === 0 ? 34 : 33));
  });
  const used = new Set(selected.map(q => q.number));
  selected.push(...shuffle(bank.filter(q => !used.has(q.number)), random).slice(0, 100 - selected.length));
  return shuffle(selected, random).map(q => {
    const originalLabels = Object.keys(q.options);
    const texts = originalLabels.map(label => normalize(q.options[label]));
    const canShuffle = texts.every(Boolean) && new Set(texts).size === texts.length && mappings.get(textKey(q)) !== null;
    if (!canShuffle) return { number: q.number, kind: q.kind, stem: q.stem, options: { ...q.options }, answers: q.answers.slice().sort() };
    const labels = shuffle(originalLabels, random);
    const options = {};
    const answers = [];
    labels.forEach((oldLabel, i) => {
      const label = String.fromCharCode(65 + i);
      options[label] = q.options[oldLabel];
      if (q.answers.includes(oldLabel)) answers.push(label);
    });
    return { number: q.number, kind: q.kind, stem: q.stem, options, answers: answers.sort() };
  });
}

function grade(paper, answers) {
  if (Object.keys(answers).length !== paper.length) throw new Error('答案不完整');
  return paper.reduce((score, q, i) => {
    const chosen = answers[i];
    if (!Array.isArray(chosen) || !chosen.length || new Set(chosen).size !== chosen.length || chosen.some(v => !(v in q.options))) {
      throw new Error('答案包含空值、重复或无效选项');
    }
    return score + (chosen.slice().sort().join(',') === q.answers.slice().sort().join(',') ? 1 : 0);
  }, 0);
}

function publicQuestion(paper, number, answers) {
  const q = paper[number];
  const selected = answers[number] || [];
  return {
    number: number + 1, total: paper.length, kind: q.kind, stem: q.stem,
    options: Object.keys(q.options).map(label => ({ label, text: q.options[label], checked: selected.includes(label) }))
  };
}

module.exports = { shuffle, makePaper, grade, publicQuestion };
