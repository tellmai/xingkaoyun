from .models import BankImport, Candidate, IdentityImport, Question


def demo_data():
    people = [Candidate(i + 2, district, f"演示考生{i + 1:02d}", f"11010119900101{i + 10:04d}")
              for i, district in enumerate(["黔江区", "渝中区", "两江新区", "南岸区", "北碚区", "江津区"])]
    questions = []
    for n in range(1, 151):
        kind = ("单选", "多选", "判断")[(n - 1) % 3]
        options = {"A": "正确", "B": "错误"} if kind == "判断" else {"A": "演示选项甲", "B": "演示选项乙", "C": "演示选项丙"}
        questions.append(Question(n, kind, f"第 {n} 道合成演示题，仅用于软件测试。", options, ("A", "C") if kind == "多选" else ("A",), (1,)))
    return IdentityImport(people, [], "合成演示"), BankImport(questions, [], 1)
