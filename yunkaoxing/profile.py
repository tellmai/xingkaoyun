import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SiteProfile:
    window_title: str = "云考星"
    process_name: str = "WeChatAppEx.exe"
    document_name: str = "Page-Frame"
    app_id: str = "wx153b2d4f2609e62d"
    district_search: list[str] = field(default_factory=lambda: ["搜索区县名称或拼音"])
    district_buttons: list[str] = field(default_factory=lambda: ["选择{district}", "继续进入{district}考区"])
    reselect: list[str] = field(default_factory=lambda: ["重新选择 ›", "重新选择"])
    name_inputs: list[str] = field(default_factory=lambda: ["请输入真实姓名"])
    id_inputs: list[str] = field(default_factory=lambda: ["请输入15位或18位身份证号"])
    agreement: list[str] = field(default_factory=lambda: ["同意用户服务协议和隐私政策"])
    login_buttons: list[str] = field(default_factory=lambda: ["安全登录"])
    logout_buttons: list[str] = field(default_factory=lambda: ["退出", "退出登录", "切换考生"])
    logout_confirm: list[str] = field(default_factory=lambda: ["确定退出", "确认退出"])
    logout_prompts: list[str] = field(default_factory=lambda: ["确定退出当前考生账号吗？", "确定退出当前考生账号吗?"])
    home_markers: list[str] = field(default_factory=lambda: ["考试服务", "考生首页", "考试列表", "我的考试"])
    personal_buttons: list[str] = field(default_factory=lambda: ["人 个人资料 ›"])
    personal_close: list[str] = field(default_factory=lambda: ["关闭个人资料"])
    no_exam_markers: list[str] = field(default_factory=lambda: ["当前暂无考试与学习安排"])
    start_buttons: list[str] = field(default_factory=lambda: ["开始考试", "进入考试", "继续考试"])
    next_buttons: list[str] = field(default_factory=lambda: ["下一题"])
    previous_buttons: list[str] = field(default_factory=lambda: ["上一题"])
    submit_buttons: list[str] = field(default_factory=lambda: ["交卷", "提交试卷"])
    submit_confirm: list[str] = field(default_factory=lambda: ["确认交卷", "确认提交"])
    result_markers: list[str] = field(default_factory=lambda: ["考试成绩", "考试完成", "交卷成功"])
    login_errors: list[str] = field(default_factory=lambda: ["未查询到", "身份信息不匹配", "登录失败", "未报名", "姓名或身份证号错误", "身份证号码格式不正确"])
    home_buttons: list[str] = field(default_factory=lambda: ["返回考生首页", "返回首页"])
    login_error_close: list[str] = field(default_factory=lambda: ["确定", "知道了", "关闭"])
    login_error_titles: list[str] = field(default_factory=lambda: ["提示", "温馨提示", "登录失败", "登录提示", "身份验证失败", "验证失败", "错误提示"])

    def validate(self):
        """The window name is configurable; the verified application is unchanged."""
        for key, value in asdict(self).items():
            if isinstance(getattr(type(self)(), key), list):
                if not isinstance(value, list) or not value or any(not isinstance(s, str) or not s.strip() for s in value):
                    raise ValueError(f"适配字段 {key} 必须是非空文本列表")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"适配字段 {key} 必须是非空文本")
        if self.process_name.casefold() != "wechatappex.exe" or self.app_id != type(self)().app_id:
            raise ValueError("适配目标必须为已核对的云考星微信小程序；修改窗口名称不改变应用身份")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        allowed = set(cls.__dataclass_fields__)
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("适配文件含有未知字段")
        profile = cls(**data)
        profile.validate()
        return profile
