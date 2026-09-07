"""ROUGE-L 单元测试：已知句子对的精确值 / 边界（空串、相同、无重叠）。"""
import pytest

from tests.evaluation.rougel import lcs_len, rouge_l_f1


def test_identical_strings_score_one():
    # 完全相同 → F1 = 1
    assert rouge_l_f1("正式员工每年享有15天带薪年假", "正式员工每年享有15天带薪年假") == 1.0


def test_both_empty_is_zero():
    # 双空串无 LCS，返回 0 而不是除零
    assert rouge_l_f1("", "") == 0.0


def test_one_side_empty_is_zero():
    # 任一为空 → 0
    assert rouge_l_f1("参考答案", "") == 0.0
    assert rouge_l_f1("", "生成回答") == 0.0


def test_no_overlap_is_zero():
    # 完全无公共字符 → 0
    assert rouge_l_f1("甲乙丙丁", "子丑寅卯") == 0.0


def test_known_pair_exact_value():
    # 我喜(欢)看(电影) vs 我喜(爱)看(影片)：LCS = 「我喜看影」=4，P=R=4/6 → F1 = 2/3
    assert rouge_l_f1("我喜欢看电影", "我喜爱看影片") == pytest.approx(2 / 3)


def test_known_pair_interval():
    # 参考答案与包含关键数字的同义回答 → 高但不满分
    ref = "年假需提前 3 个工作日在 OA 系统提交申请。"
    hyp = "请年假需要提前 3 个工作日提交申请。"
    score = rouge_l_f1(ref, hyp)
    assert 0.5 < score <= 1.0


def test_hypothesis_longer_than_reference():
    # hyp 更长时精确率被稀释，但召回为 1 → 0 < F1 < 1
    score = rouge_l_f1("年假15天", "关于年假的规定：正式员工每年享有15天带薪年假。")
    assert 0.0 < score < 1.0


def test_lcs_len_basic():
    assert lcs_len(list("ABCBDAB"), list("BDCABA")) == 4  # 经典例：BCBA/BCAB 等
    assert lcs_len([], list("abc")) == 0
