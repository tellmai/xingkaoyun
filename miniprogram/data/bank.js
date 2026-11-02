// Synthetic questions only. Replace with exported bank data to exercise another bank.
module.exports = Array.from({ length: 150 }, (_, i) => {
  const number = i + 1;
  const kind = ['单选', '多选', '判断'][i % 3];
  return {
    number, kind, stem: `第 ${number} 道合成演示题，仅用于软件测试。`,
    options: kind === '判断' ? { A: '正确', B: '错误' } : { A: '演示选项甲', B: '演示选项乙', C: '演示选项丙' },
    answers: kind === '多选' ? ['A', 'C'] : ['A']
  };
});
