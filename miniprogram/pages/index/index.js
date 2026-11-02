const bank = require('../../data/bank');
const { makePaper, grade, publicQuestion } = require('../../lib/exam');
const districts = ['两江新区', '璧山区', '合川区', '云阳县', '石柱县', '涪陵区', '江津区', '綦江区', '丰都县', '城口县', '北碚区', '大足区', '巴南区', '永川区', '大渡口区', '秀山县', '垫江县', '荣昌区', '长寿区', '九龙坡区', '黔江区', '铜梁区', '渝中区', '沙坪坝区', '南岸区', '潼南区', '忠县', '武隆区', '彭水县', '万州区', '奉节县', '梁平区', '巫山县', '开州区', '巫溪县', '酉阳县', '万盛经开区'];

Page({
  data: {
    stage: 'region', districts, filteredDistricts: districts, district: '', name: '', identity: '',
    agreed: false, candidate: null, personal: false, examTitle: '法治理论知识模拟考试',
    q: null, number: 0, answeredCount: 0, result: null, history: [], error: ''
  },
  onLoad() { this._history = []; },
  searchDistrict(e) {
    const query = e.detail.value.trim();
    this.setData({ filteredDistricts: districts.filter(d => d.includes(query)) });
  },
  chooseDistrict(e) {
    this.setData({ stage: 'login', district: e.currentTarget.dataset.district, name: '', identity: '', agreed: false, error: '' });
  },
  reselect() { this.setData({ stage: 'region', error: '', filteredDistricts: districts }); },
  inputName(e) { this.setData({ name: e.detail.value }); },
  inputIdentity(e) { this.setData({ identity: e.detail.value }); },
  changeAgreement(e) { this.setData({ agreed: e.detail.value.includes('agree') }); },
  login() {
    const name = this.data.name.trim();
    const identity = this.data.identity.trim().toUpperCase();
    if (!name || !/^(?:\d{15}|\d{17}[\dX])$/.test(identity) || !this.data.agreed) {
      this.setData({ error: '请填写姓名、格式正确的证件号，并勾选模拟协议。' });
      return;
    }
    this._identity = identity; // In memory only. No remote login or persistent personal data.
    this.setData({
      candidate: { name, district: this.data.district, maskedId: identity.slice(0, 6) + '********' + identity.slice(-4) },
      stage: 'home', identity: '', name: '', error: '', personal: false
    });
  },
  logout() {
    wx.showModal({ title: '退出模拟账号', content: '确定退出当前模拟考生？', success: r => {
      if (r.confirm) {
        this._identity = null;
        this._paper = null;
        this._answers = {};
        this.setData({ stage: 'region', candidate: null, personal: false, q: null, result: null, error: '' });
      }
    } });
  },
  openPersonal() { this.setData({ personal: true }); },
  closePersonal() { this.setData({ personal: false }); },
  openExam() { this.setData({ stage: 'details', error: '' }); },
  returnHome() { this.setData({ stage: 'home', personal: false, error: '' }); },
  openHistory() {
    this.setData({ stage: 'history', history: this._history.filter(r => r.identity === this._identity).map((r, i) => ({ key: i, score: r.score, seconds: r.seconds, title: r.title })) });
  },
  startExam() {
    this._paper = makePaper(bank);
    this._answers = {};
    this._started = Date.now();
    this._submitted = false;
    this.setData({ stage: 'question', number: 0, answeredCount: 0, error: '', result: null });
    this.refreshQuestion();
  },
  refreshQuestion() {
    this.setData({ q: publicQuestion(this._paper, this.data.number, this._answers) });
  },
  chooseSingle(e) { this.saveAnswer([e.detail.value]); },
  chooseMulti(e) { this.saveAnswer(e.detail.value); },
  saveAnswer(values) {
    this._answers[this.data.number] = values.slice();
    this.setData({ answeredCount: Object.values(this._answers).filter(v => v.length).length, error: '' });
    this.refreshQuestion();
  },
  previous() {
    if (this.data.number > 0) { this.setData({ number: this.data.number - 1 }); this.refreshQuestion(); }
  },
  next() {
    if (this.data.number < 99) { this.setData({ number: this.data.number + 1 }); this.refreshQuestion(); }
  },
  askSubmit() {
    const missing = 100 - Object.values(this._answers).filter(v => v.length).length;
    if (missing) { this.setData({ error: `仍有 ${missing} 题未作答，未交卷。` }); return; }
    wx.showModal({ title: '确认交卷', content: '交卷后独立计算模拟成绩。此操作不会提交真实考试。', success: r => { if (r.confirm) this.submit(); } });
  },
  submit() {
    if (this._submitted) return;
    const score = grade(this._paper, this._answers);
    const seconds = Math.round((Date.now() - this._started) / 1000);
    this._submitted = true;
    this._history.push({ identity: this._identity, score, seconds, title: this.data.examTitle });
    this.setData({ stage: 'result', result: { score, seconds }, error: '' });
  }
});
