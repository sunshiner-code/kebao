"""罚款罚金除外判定的边界：否定语可能写在罚款之后。"""

from techins.agents.clause_design.library import _liability_covers_fine


def test_fine_excluded_after_the_word_is_not_treated_as_covered():
    text = "赔偿范围限于经认定文件与损失凭证确认的直接经济赔偿责任；间接损失、利润损失、商誉损失、投资损失、罚款罚金及惩罚性赔偿按责任免除处理。"
    assert _liability_covers_fine(text) is False


def test_fine_before_negation_is_not_treated_as_covered():
    text = "保险人不负责赔偿罚款、罚金及惩罚性赔偿。"
    assert _liability_covers_fine(text) is False


def test_fine_inside_coverage_is_detected():
    text = "被保险人依法应承担的赔偿责任，包括行政罚款与罚金，保险人负责赔偿。"
    assert _liability_covers_fine(text) is True


def test_other_sentence_does_not_mask_coverage():
    text = "保险人负责赔偿行政罚款。罚款罚金及惩罚性赔偿按责任免除处理。"
    assert _liability_covers_fine(text) is True
