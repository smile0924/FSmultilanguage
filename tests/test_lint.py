from fslang.lint import ERROR, WARNING, lint_sheet


def _issues(tow_sheet, tow_config):
    return {(i.cell, i.rule): i for i in lint_sheet(tow_sheet, tow_config.placeholders, tow_config.keywords)}


def test_known_problems_in_tree_of_wild(tow_sheet, tow_config):
    issues = _issues(tow_sheet, tow_config)
    # 孟加拉語把 WILD 翻成了 বন্য
    assert issues[("M15", "keyword")].severity == ERROR
    # 土耳其語 WILDI
    assert "WILDI" in issues[("G9", "keyword")].message
    # 泰文修訂版 จะจะ 重複
    assert issues[("D11", "repeat")].severity == WARNING
    # 印地語 एकWILD 沒有空白
    assert issues[("L15", "spacing")].severity == WARNING
    # 孟加拉語開頭空白、連續空白
    assert ("M6", "whitespace") in issues
    assert ("M3", "whitespace") in issues


def test_no_false_alarms_for_clean_rows(tow_sheet, tow_config):
    issues = lint_sheet(tow_sheet, tow_config.placeholders, tow_config.keywords)
    # 第 7、8 列各語言數字寫法不同（2.000 / 2 000 / 2000），都不應該報數字錯誤
    assert not [i for i in issues if i.row in (7, 8) and i.rule == "number"]
    # 泰文在 WILD 前後不用空白，不應報 spacing
    assert not [i for i in issues if i.lang == "th" and i.rule == "spacing"]
    # [WILD] 佔位符各語言都有保留
    assert not [i for i in issues if i.rule == "placeholder"]
