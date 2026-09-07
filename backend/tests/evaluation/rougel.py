"""ROUGE-L F1 自实现（评测专用，不引入第三方依赖）。

ROUGE-L 基于最长公共子序列（LCS）衡量生成回答与参考答案的相似度：
P = LCS / len(hyp)，R = LCS / len(ref)，F1 = 2PR / (P + R)。
中文按字符切分；任一输入为空时 F1 = 0.0，取值范围 [0, 1]。
"""


def lcs_len(a: list[str], b: list[str]) -> int:
    """两字符序列的最长公共子序列长度（动态规划，滚动一行省内存）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            # 末字符相等则继承对角线；否则取左/上较大值
            cur[j] = prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def rouge_l_f1(reference: str, hypothesis: str) -> float:
    """参考答案与生成回答的 ROUGE-L F1，范围 [0, 1]；任一为空返回 0.0。"""
    if not reference or not hypothesis:
        return 0.0
    ref, hyp = list(reference), list(hypothesis)
    lcs = lcs_len(ref, hyp)
    if lcs == 0:
        return 0.0
    precision = lcs / len(hyp)
    recall = lcs / len(ref)
    return 2 * precision * recall / (precision + recall)
