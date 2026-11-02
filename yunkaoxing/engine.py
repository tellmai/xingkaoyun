"""Serial, verified mini-program workflow; injectable desktop port for offline tests."""
import re
import random
import threading
import time

from .page import PageError, parse_question, parse_score


class Stopped(Exception):
    pass


class LoginError(PageError):
    """A person-specific login could not be confirmed; later people can still run."""
    pass


class Control:
    def __init__(self):
        self.stop = threading.Event()
        self.after_current = threading.Event()
        self.paused = threading.Event()
        self.resume = threading.Event()
        self.dispatch_paused = threading.Event()

    def check(self):
        # A stop-mode dialog belongs to the assistant. Pause current inputs as
        # well as dispatch so foreground automation cannot steal that dialog.
        while self.dispatch_paused.is_set() and not self.stop.is_set():
            self.stop.wait(.05)
        if self.stop.is_set():
            raise Stopped("已停止，当前小程序页面保留")

    def sleep(self, seconds):
        if self.stop.wait(max(0, seconds)):
            self.check()


class MiniProgramSession:
    def __init__(self, port, profile, options, control, report=lambda _: None, wait_operator=None, clock=time.monotonic):
        self.port, self.p, self.options, self.control = port, profile, options, control
        self.report, self.clock = report, clock
        self.wait_operator = wait_operator or self._unattended

    @staticmethod
    def _unattended(message):
        raise PageError(message)

    def snapshot(self):
        self.control.check()
        return self.port.snapshot()

    def wait(self, predicate, label, timeout=None):
        deadline = self.clock() + (self.options.timeout_seconds if timeout is None else timeout)
        last_read_error = None
        while self.clock() < deadline:
            try:
                snapshot = self.snapshot()
            except PageError as exc:
                # WeChat briefly replaces its page document during navigation.
                # Only a read failure is retried; a predicate's login rejection
                # must still reach the batch as the current person's result.
                last_read_error = exc
                self.control.sleep(.2)
                continue
            if predicate(snapshot):
                return snapshot
            self.control.sleep(.2)
        detail = f"（{last_read_error}）" if last_read_error else ""
        raise PageError(label + "超时；请检查小程序后重试" + detail)

    def click_named(self, names, kinds=("Button", "Hyperlink")):
        s = self.snapshot()
        self.port.click(s, s.find(names, kinds))

    def _on_login(self, s):
        # A district bottom sheet leaves the entire login form in the same
        # document. Backdrop fields must not be treated as the active page.
        return not self._on_region(s) and any(n.text in self.p.login_buttons for n in s.nodes)

    def _on_home(self, s):
        return any(n.kind == "Text" and n.text in self.p.home_markers for n in s.nodes)

    def _on_region(self, s):
        return any(n.kind == "Text" and n.text.strip() in ("选择区县", "选择考区") for n in s.nodes)

    @staticmethod
    def _district_core(value):
        value = re.sub(r"\s+", "", value)
        for suffix in ("土家族苗族自治县", "苗族土家族自治县", "土家族自治县", "苗族自治县", "自治县", "区", "县"):
            if value.endswith(suffix) and len(value) > len(suffix):
                return value[:-len(suffix)]
        return value

    @classmethod
    def _same_district(cls, requested, observed):
        left, right = re.sub(r"\s+", "", requested), re.sub(r"\s+", "", observed)
        return left == right or cls._district_core(left) == cls._district_core(right)

    def _district_present(self, s, requested):
        """Match a complete district field/token, including combined text labels."""
        for node in s.nodes:
            if node.kind != "Text":
                continue
            value = re.sub(r"^(?:当前考区|所在考区|考区)\s*[:：]?\s*", "", node.text.strip())
            for token in re.split(r"[\s/／:：、，,|>›（）()]+", value):
                if token and self._same_district(requested, token):
                    return True
        return False

    def _district_button(self, s, requested):
        if not self._on_region(s):
            return None
        choices = []
        for node in s.nodes:
            if node.kind != "Button" or not node.enabled:
                continue
            text = node.text.strip()
            for rank, template in enumerate(self.p.district_buttons):
                before, after = template.split("{district}")
                if text.startswith(before) and (not after or text.endswith(after)):
                    actual = text[len(before):len(text) - len(after) if after else None].strip()
                    if actual and self._same_district(requested, actual):
                        choices.append((rank, actual, node))
            # Some accessible providers omit the ARIA "选择" prefix.
            if text.endswith(("区", "县")) and self._same_district(requested, text):
                choices.append((len(self.p.district_buttons), text, node))
        if not choices:
            return None
        exact = [item for item in choices if re.sub(r"\s+", "", item[1]) == re.sub(r"\s+", "", requested)]
        if exact:
            choices = exact
        districts = {item[1] for item in choices}
        if len(districts) != 1:
            raise LoginError(f"区县简称「{requested}」对应多个选项，请在身份表中填写完整区县")
        best = min(item[0] for item in choices)
        nodes = {item[2].key: item[2] for item in choices if item[0] == best}
        if len(nodes) != 1:
            raise LoginError(f"区县「{requested}」的选择按钮不唯一，未选择考区")
        return next(iter(nodes.values()))

    def _region_search(self, s):
        edits = [n for n in s.nodes if n.kind == "Edit" and n.enabled]
        matches = [n for n in edits if n.text.strip() in self.p.district_search or n.value.strip() in self.p.district_search]
        if len(matches) == 1:
            return matches[0]
        if self._on_region(s):
            # Once a query is entered, its accessible name can lose the
            # placeholder. Scope to the observed picker heading, excluding
            # the two login inputs that remain behind a native bottom sheet.
            headings = [i for i, n in enumerate(s.nodes) if n.kind == "Text" and n.text.strip() in ("选择区县", "选择考区")]
            picker_edits = [n for n in s.nodes[headings[-1] + 1:] if n.kind == "Edit" and n.enabled]
            if len(picker_edits) == 1:
                return picker_edits[0]
            if len(edits) == 1:
                return edits[0]
        return None

    def _poll_district(self, requested, seconds=2):
        deadline = self.clock() + min(seconds, self.options.timeout_seconds)
        while self.clock() < deadline:
            try:
                s = self.snapshot()
            except PageError:
                self.control.sleep(.15)
                continue
            target = self._district_button(s, requested)
            if target is not None:
                return s, target
            self.control.sleep(.15)
        return None

    def _select_district(self, requested):
        s = self.wait(self._on_region, "等待区县选择页")
        target = self._district_button(s, requested)
        if target is None:
            search = self._region_search(s)
            if search is not None:
                self.report(f"正在搜索考区：{requested}")
                try:
                    self.port.edit(s, search, self._district_core(requested))
                    found = self._poll_district(requested)
                    if found:
                        s, target = found
                except PageError:
                    # If this WeChat version does not expose a settable search,
                    # continue with the actual list instead of guessing a row.
                    pass
            if target is None:
                s = self.snapshot()
                search = self._region_search(s)
                if search is not None:
                    try:
                        self.port.edit(s, search, "")
                        self.control.sleep(.2)
                    except PageError:
                        pass
                scroll = getattr(self.port, "scroll_region", None)
                if scroll is not None:
                    self.report(f"正在滚动查找考区：{requested}")
                    # Three pages up cover the 37-district picker from its end;
                    # then scan down. Same title/page-key is not evidence that
                    # scrolling failed: each viewport is inspected afresh.
                    for direction in ("up",) * 3 + ("down",) * 9:
                        s = self.snapshot()
                        target = self._district_button(s, requested)
                        if target is not None:
                            break
                        if not self._on_region(s):
                            raise LoginError("滚动期间已离开区县选择页，未选择考区")
                        sent = scroll(s, direction=direction)
                        self.control.sleep(.2)
                        s = self.snapshot()
                        target = self._district_button(s, requested)
                        if target is not None:
                            break
                        if sent is False and direction == "down":
                            break
        if target is None:
            raise LoginError(f"未找到身份表中的区县「{requested}」；已检查可见列表、搜索及可用滚动")
        self.port.click(s, target)
        self.wait(lambda v: self._on_login(v) and self._district_present(v, requested), f"进入「{requested}」考生登录")

    def _login_error_text(self, s):
        messages = [n.text.strip() for n in s.nodes if n.kind == "Text" and any(error in n.text for error in self.p.login_errors)]
        return "；".join(dict.fromkeys(messages))[:400]

    def _login_error_dialog(self, s):
        # AppIndex native overlays follow the business page in the flat UIA
        # snapshot. An inline error before an unrelated modal is not evidence
        # that the modal's generic confirmation belongs to that login error.
        titles = [i for i, n in enumerate(s.nodes) if n.kind == "Text" and n.text.strip() in self.p.login_error_titles]
        dialogs = []
        for number, start in enumerate(titles):
            end = titles[number + 1] if number + 1 < len(titles) else len(s.nodes)
            bodies = [i for i in range(start + 1, end) if s.nodes[i].kind == "Text"
                      and any(error in s.nodes[i].text for error in self.p.login_errors)]
            if not bodies:
                continue
            closes = [n for n in s.nodes[bodies[-1] + 1:end] if n.kind in ("Button", "Text")
                      and n.enabled and n.text.strip() in self.p.login_error_close
                      and not (self._on_region(s) and n.text.strip() == "关闭")]
            dialogs.append((s.nodes[start].text.strip(), closes))
        if len(dialogs) > 1:
            raise LoginError("登录错误提示不唯一，未关闭其他提示")
        return dialogs[0] if dialogs else None

    def _login_error_dismiss(self, s):
        dialog = self._login_error_dialog(s)
        if dialog is None:
            return None
        closes = dialog[1]
        buttons = [n for n in closes if n.kind == "Button"]
        closes = buttons or closes
        if len(closes) > 1:
            raise LoginError("登录错误提示的关闭入口不唯一，未继续操作")
        return closes[0] if closes else None

    def _clear_login_error(self, s):
        error = self._login_error_text(s)
        if not error:
            return s
        dialog = self._login_error_dialog(s)
        if dialog is not None:
            title = dialog[0]
            self.report("清理登录失败提示：" + error)
            def title_present(v):
                return any(n.kind == "Text" and n.text.strip() == title for n in v.nodes)
            s = self.wait(lambda v: not title_present(v) or self._login_error_dismiss(v) is not None, "等待登录提示关闭入口")
            if title_present(s):
                self.port.click(s, self._login_error_dismiss(s))
                s = self.wait(lambda v: not title_present(v), "关闭登录提示")
        else:
            # Leave an unrelated notice untouched even when a previous inline
            # login error remains readable in its backdrop.
            for i, node in enumerate(s.nodes):
                if node.kind == "Text" and node.text.strip() in self.p.login_error_titles:
                    if any(n.kind in ("Text", "Button") and n.enabled and n.text.strip() in self.p.login_error_close
                           for n in s.nodes[i + 1:]):
                        raise LoginError("页面另有无法确认的提示，未关闭或继续输入")
        if self._on_login(s) and self._login_error_text(s):
            reselect = [n for n in s.nodes if n.kind == "Button" and n.text in self.p.reselect and n.enabled]
            if len(reselect) == 1:
                self.port.click(s, reselect[0])
                s = self.wait(self._on_region, "清理上次登录提示并返回区县选择")
        return s

    def _logout_prompt_present(self, s):
        return any(n.kind == "Text" and n.text.strip() in self.p.logout_prompts for n in s.nodes)

    def _logout_confirmation(self, s):
        matches = [n for n in s.nodes if n.kind == "Button" and n.enabled and n.text.strip() in self.p.logout_confirm]
        if not matches and self._logout_prompt_present(s):
            # WeChat's native modal exposes Group->Text rather than Buttons.
            # A generic confirmation is only safe inside this observed prompt.
            matches = [n for n in s.nodes if n.kind in ("Button", "Text") and n.enabled and n.text.strip() == "确定"]
        if len(matches) > 1:
            raise LoginError("退出提示中无法唯一确认「确定」入口")
        return matches[0] if matches else None

    def _logout_complete(self, s):
        return ((self._on_login(s) or self._on_region(s))
                and not self._logout_prompt_present(s) and self._logout_confirmation(s) is None)

    def _logout(self):
        s = self.snapshot()
        if self._logout_prompt_present(s) or self._logout_confirmation(s) is not None:
            s = self.wait(lambda v: self._logout_confirmation(v) is not None, "等待退出确认入口")
            self.port.click(s, self._logout_confirmation(s))
            self.wait(self._logout_complete, "确认退出当前考生")
            return
        if self._logout_complete(s):
            return
        closes = [n for n in s.nodes if n.kind == "Button" and n.text in self.p.personal_close]
        if len(closes) == 1:
            self.port.click(s, closes[0])
            self.wait(lambda v: not any(n.text in self.p.personal_close for n in v.nodes), "关闭个人资料")
            s = self.snapshot()
        logout = [n for n in s.nodes if n.kind == "Button" and n.text in self.p.logout_buttons and n.enabled]
        if not logout:
            homes = [n for n in s.nodes if n.kind == "Button" and n.text in self.p.home_buttons and n.enabled]
            if len(homes) == 1:
                self.port.click(s, homes[0])
                self.wait(self._on_home, "返回考生首页")
                s = self.snapshot()
                logout = [n for n in s.nodes if n.kind == "Button" and n.text in self.p.logout_buttons and n.enabled]
        if len(logout) != 1:
            raise LoginError("无法确认退出当前考生的入口，下名考生开始前将重新核对页面")
        self.port.click(s, logout[0])
        s = self.wait(lambda v: self._logout_complete(v) or self._logout_confirmation(v) is not None, "退出当前考生")
        if self._logout_complete(s):
            return
        self.port.click(s, self._logout_confirmation(s))
        self.wait(self._logout_complete, "确认退出当前考生")

    def logout(self):
        """End a verified session before another identity may start."""
        self.report("退出当前考生")
        self._logout()
        s = self.wait(lambda v: self._on_login(v) or self._on_region(v), "核对考生已退出")
        if self.options.mode == "login" and self._on_login(s):
            self.click_named(self.p.reselect)
            self.wait(self._on_region, "退出后返回区县选择")
        self.report("已退出当前考生")

    def recover_login(self):
        """Get to a known login/region page; never infer that an unknown account is safe."""
        s = self._clear_login_error(self.snapshot())
        if self._on_login(s) or self._on_region(s):
            return
        self._logout()

    def _login_field(self, s, names, label):
        matches = [n for n in s.nodes if n.kind == "Edit" and n.enabled and (n.text.strip() in names or n.value.strip() in names)]
        if len(matches) == 1:
            return matches[0]
        edits = [n for n in s.nodes if n.kind == "Edit" and n.enabled]
        if self._on_login(s):
            # Accessible names can change to the entered value. Use the nearby
            # observed label, not a hard-coded input index across the document.
            sections = {"姓名": [], "身份证号": []}
            active = None
            for node in s.nodes:
                if node.kind == "Text":
                    text = node.text.strip().rstrip("：:").strip()
                    if text in sections:
                        active = text
                elif node.kind == "Edit" and node.enabled and active is not None:
                    sections[active].append(node)
            candidates = sections[label]
            def compatible(nodes, captions, field_label):
                if not 1 <= len(nodes) <= 2:
                    return False
                values = set()
                for node in nodes:
                    value = node.value.strip()
                    if not value or value in captions:
                        value = node.text.strip()
                    if value and value not in set(captions) | {field_label}:
                        values.add(value)
                return len(values) <= 1
            safe_sections = (compatible(sections["姓名"], self.p.name_inputs, "姓名")
                             and compatible(sections["身份证号"], self.p.id_inputs, "身份证号"))
            if len(candidates) == 1 and safe_sections:
                return candidates[0]
            # A nested edit can be exposed alongside its parent. Prefer the
            # innermost equivalent representation only when their values agree.
            if len(candidates) == 2 and len(edits) <= 4 and safe_sections:
                values = {n.value.strip() for n in candidates if n.value.strip()}
                if len(values) <= 1:
                    return candidates[-1]
        raise LoginError(f"无法唯一定位{label}输入框，未继续输入")

    def _field_ready(self, s, names, label):
        try:
            self._login_field(s, names, label)
            return self._on_login(s)
        except LoginError:
            return False

    def _field_matches(self, s, names, label, expected):
        try:
            field = self._login_field(s, names, label)
        except LoginError:
            return False
        placeholders = set(names)
        values = [field.value.strip(), field.text.strip()]
        values = [value for value in values if value and value not in placeholders]
        if label == "身份证号":
            return any(value.upper() == expected.strip().upper() for value in values)
        return expected.strip() in values

    def _fill_login_field(self, names, label, value, district):
        s = self.wait(lambda v: self._district_present(v, district) and self._field_ready(v, names, label), f"等待{label}输入框")
        self.port.edit(s, self._login_field(s, names, label), value)
        self.wait(lambda v: self._on_login(v) and self._field_matches(v, names, label, value), f"{label}填写回读")

    @staticmethod
    def _text_field_present(s, label, expected):
        for node in s.nodes:
            if node.kind != "Text":
                continue
            text = node.text.strip()
            if text == expected:
                return True
            stripped = re.sub(r"^" + re.escape(label) + r"\s*[:：]?\s*", "", text)
            if stripped != text and stripped.strip() == expected:
                return True
        return False

    def _identity_matches(self, s, candidate):
        # A full ID or the exact site's common masking formats must be visible alongside the name.
        masks = {candidate.id_number.upper(), candidate.masked_id.upper(),
                 candidate.id_number[:6] + "********" + candidate.id_number[-4:]}
        return (self._text_field_present(s, "姓名", candidate.name)
                and self._district_present(s, candidate.district)
                and any(self._text_field_present(s, "身份证号", mask) for mask in masks))

    def verify_identity(self, candidate):
        s = self.snapshot()
        if self._identity_matches(s, candidate):
            closes = [n for n in s.nodes if n.text in self.p.personal_close and n.kind == "Button"]
            if len(closes) == 1:
                self.port.click(s, closes[0])
                self.wait(lambda v: not any(n.text in self.p.personal_close for n in v.nodes), "关闭个人资料")
            return True
        if not self._text_field_present(s, "姓名", candidate.name):
            return False
        if any(n.text in self.p.personal_buttons for n in s.nodes):
            self.click_named(self.p.personal_buttons)
            def personal_ready(v):
                shown = any(n.kind == "Button" and n.text in self.p.personal_close for n in v.nodes)
                identity = any(n.kind == "Text" and re.search(r"(?<![0-9])(?:[0-9]{3,6}\*{4,12}[0-9]{4}|[0-9]{15}|[0-9]{17}[0-9Xx])(?![0-9])", n.text) for n in v.nodes)
                return shown and identity
            s = self.wait(personal_ready, "打开个人资料并读取证件信息")
            matches = self._identity_matches(s, candidate)
            self.click_named(self.p.personal_close)
            self.wait(lambda v: not any(n.text in self.p.personal_close for n in v.nodes), "关闭个人资料")
            return matches
        return False

    def login(self, candidate):
        self.report("核对小程序和当前考生")
        s = self._clear_login_error(self.wait(lambda v: bool(v.nodes), "等待小程序页面就绪"))
        fresh_login = self.options.mode == "login"
        if not fresh_login and self._on_home(s) and self.verify_identity(candidate):
            self.report("当前账号与导入人员一致")
            return
        s = self.snapshot()
        if not self._on_login(s) and not self._on_region(s):
            self._logout()
        s = self.snapshot()
        if self._on_login(s):
            if fresh_login or not self._district_present(s, candidate.district):
                self.click_named(self.p.reselect)
                self.wait(self._on_region, "返回区县选择")
        s = self.snapshot()
        if not self._on_login(s):
            self._select_district(candidate.district)
        for names, value, label in ((self.p.name_inputs, candidate.name, "姓名"), (self.p.id_inputs, candidate.id_number, "身份证号")):
            self._fill_login_field(names, label, value.upper() if label == "身份证号" else value, candidate.district)
        s = self.snapshot()
        agreement = s.find(self.p.agreement, ("CheckBox",))
        self.port.select(s, agreement, True)
        def form_verified(v):
            agreements = [n for n in v.nodes if n.kind == "CheckBox" and n.text.strip() in self.p.agreement]
            return (self._on_login(v) and self._district_present(v, candidate.district)
                    and self._field_matches(v, self.p.name_inputs, "姓名", candidate.name)
                    and self._field_matches(v, self.p.id_inputs, "身份证号", candidate.id_number)
                    and len(agreements) == 1 and agreements[0].selected is True)
        s = self.wait(form_verified, "核对姓名、身份证号、考区与登录协议")
        previous_error = self._login_error_text(s)
        response_cleared = not previous_error
        last_error = previous_error
        self.click_named(self.p.login_buttons)
        def finished(v):
            nonlocal response_cleared, last_error
            error_text = self._login_error_text(v)
            last_error = error_text
            if not error_text:
                response_cleared = True
            if error_text and (response_cleared or error_text != previous_error):
                raise LoginError("小程序登录失败：" + error_text)
            return (not self._on_login(v) and self._on_home(v)
                    and self._text_field_present(v, "姓名", candidate.name))
        try:
            s = self.wait(finished, "身份验证")
        except LoginError:
            raise
        except PageError as exc:
            if previous_error and last_error and str(exc).startswith("身份验证超时"):
                raise LoginError("身份验证超时，页面仍显示：" + last_error) from exc
            raise
        if not self.verify_identity(candidate):
            raise PageError("已进入考生页，但姓名和证件信息不足以核对身份，已暂停后续考试")
        self.report("登录成功，已核对考生身份")

    def open_exam(self, candidate):
        if not self.verify_identity(candidate):
            raise PageError("开始考试前身份核对失败")
        s = self.snapshot()
        if any(marker in s.text for marker in self.p.no_exam_markers):
            raise PageError("当前考生暂无考试与学习安排；未开始作答，未提交考试")
        title = s.find([self.options.exam_title], ("Button", "Hyperlink", "ListItem"))
        self.port.click(s, title)
        s = self.wait(lambda v: self.options.exam_title in v.text, "进入指定考试")
        self.report("已核对考试名称：" + self.options.exam_title)
        self.click_named(self.p.start_buttons)
        self.wait(lambda v: self._is_question(v), "打开试题")

    @staticmethod
    def _is_question(s):
        try:
            parse_question(s)
            return True
        except PageError:
            return False

    def _verified_question(self, index, expected_number=None):
        while True:
            self.control.check()
            try:
                s = self.snapshot()
                question = parse_question(s)
                if expected_number is not None and question.number != expected_number:
                    raise PageError("当前题号与预期不一致，不能继续自动作答")
                match = index.match(question.kind, question.stem, question.options)
                if any(n.selected is None for n in question.controls.values()):
                    raise PageError("小程序没有暴露可核对的选项状态，需更新页面适配")
                return s, question, match
            except (PageError, ValueError) as exc:
                self.wait_operator(str(exc))

    def answer_exam(self, index, started=None):
        started = self.clock() if started is None else started
        answered, total = {}, None
        # Complete each page with a read-back before moving to the next question.
        s, question, match = self._verified_question(index)
        if question.number != 1:
            raise PageError("当前试卷不是从第 1 题开始；请人工返回第 1 题后重试")
        total = question.total
        if total > self.options.max_questions:
            raise PageError("试卷题数超过设置上限")
        wrong = set()
        if self.options.simulate_scores:
            if total != 100:
                raise PageError("模拟分数计划只支持 100 题模拟卷")
            target = random.SystemRandom().randint(max(self.options.minimum_score, self.options.score_min), self.options.score_max)
            wrong = set(random.SystemRandom().sample(range(1, total + 1), 100 - target))
            self.report(f"本地模拟目标 {target} 分；由模拟器独立评分")
        for number in range(1, total + 1):
            s, q, match = self._verified_question(index, number)
            if q.total != total:
                raise PageError("试卷总题数发生变化")
            signature = q.signature
            expected = match.answers
            if number in wrong:
                expected = next((tuple([label]) for label in q.options if {label} != set(match.answers)), ())
            # Radios replace the current selection; checkboxes require symmetric updates.
            labels = list(q.controls) if q.kind == "多选" else list(expected)
            for label in labels:
                s, current, _ = self._verified_question(index, number)
                if current.signature != signature:
                    raise PageError("作答期间题干或选项发生变化")
                self.port.select(s, current.controls[label], label in expected)
            _, verified, _ = self._verified_question(index, number)
            selected = tuple(sorted(k for k, n in verified.controls.items() if n.selected))
            if selected != expected:
                raise PageError("选项回读与题库答案不一致，未进入下一题")
            answered[number] = (signature, expected)
            self.report(f"第 {number}/{total} 题已回读核对 · 题库第 {match.source_number} 题")
            if number < total:
                self.click_named(self.p.next_buttons)
                self.wait(lambda v: self._question_number(v) == number + 1, "切换下一题")
        # A second pass catches lost server state and previously reverted selections.
        self.report("全部试题已作答，开始逐题复核")
        for number in range(total, 0, -1):
            _, q, match = self._verified_question(index, number)
            selected = tuple(sorted(k for k, n in q.controls.items() if n.selected))
            if (q.signature, selected) != answered[number]:
                raise PageError("第二次复核不一致，已停止交卷")
            if number > 1:
                self.click_named(self.p.previous_buttons)
                self.wait(lambda v: self._question_number(v) == number - 1, "复核上一题")
        remaining = (0 if self.options.fastest else self.options.minimum_seconds) - (self.clock() - started)
        if remaining > 0:
            self.report(f"复核完成，等待最短用时：约 {remaining / 60:.1f} 分钟")
            self.control.sleep(remaining)
        if self.options.auto_submit:
            self.click_named(self.p.submit_buttons)
            s = self.snapshot()
            confirms = [n for n in s.nodes if n.text in self.p.submit_confirm and n.kind == "Button"]
            if len(confirms) == 1:
                self.port.click(s, confirms[0])
            self.report("已请求交卷，正在核对服务端成绩")
        else:
            self.wait_operator("已逐题复核，请在小程序中手动交卷，成绩出现后点击「继续核对」")
        s = self.wait(lambda v: parse_score(v, self.p.result_markers) is not None, "核对交卷结果")
        return {"score": parse_score(s, self.p.result_markers), "total": total,
                "elapsed_seconds": round(self.clock() - started, 2), "reason": "已读取小程序成绩页"}

    @staticmethod
    def _question_number(snapshot):
        try:
            return parse_question(snapshot).number
        except PageError:
            return None
