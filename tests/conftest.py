import os
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from yunkaoxing.models import Candidate, Question


@pytest.fixture
def candidate():
    return Candidate(2, "黔江区", "合成考生", "110101199001010010")


@pytest.fixture
def bank():
    result = []
    for n in range(1, 151):
        kind = ("单选", "多选", "判断")[(n - 1) % 3]
        options = {"A": "正确", "B": "错误"} if kind == "判断" else {"A": f"甲{n}", "B": f"乙{n}", "C": f"丙{n}"}
        result.append(Question(n, kind, f"合成试题{n}", options, ("A", "C") if kind == "多选" else ("A",)))
    return result
