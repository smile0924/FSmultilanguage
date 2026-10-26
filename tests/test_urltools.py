from fslang.urltools import detect_lang_in_url, suggest_game_codes


def test_detect_query_param():
    d = detect_lang_in_url("https://qa-go.object1.net/touch/fsnew/game.jsp?gameId=TreeOfWild&lang=en&token=abc")
    assert d.template == "https://qa-go.object1.net/touch/fsnew/game.jsp?gameId=TreeOfWild&lang={game_lang}&token=abc"
    assert d.found == "lang=en" and d.sample == "en"


def test_detect_named_param_with_numeric_value():
    assert detect_lang_in_url("https://h/x?lang=1&g=2").template == "https://h/x?lang={game_lang}&g=2"


def test_detect_value_path_and_fragment():
    assert detect_lang_in_url("https://h/x?g=2&locale=en-US").template == "https://h/x?g=2&locale={game_lang}"
    assert detect_lang_in_url("https://h/games/th/index.html").template == "https://h/games/{game_lang}/index.html"
    assert detect_lang_in_url("https://h/#/play?gid=3&language=EN").template == "https://h/#/play?gid=3&language={game_lang}"


def test_detect_existing_placeholder_and_none():
    assert detect_lang_in_url("https://h/x?lang={lang}").template == "https://h/x?lang={lang}"
    assert detect_lang_in_url("https://qa-go.object1.net/touch/fsnew/list.jsp") is None
    assert detect_lang_in_url("https://h/x?id=12&l=5") is None


def test_suggest_game_codes_follows_style():
    codes = ["en", "th", "ja", "ko"]
    assert suggest_game_codes("en", codes) == {"en": "en", "th": "th", "ja": "ja", "ko": "ko"}
    assert suggest_game_codes("EN", codes)["ja"] == "JA"
    assert suggest_game_codes("en-US", codes) == {"en": "en-US", "th": "th-TH", "ja": "ja-JP", "ko": "ko-KR"}
    assert suggest_game_codes("en_us", codes)["ko"] == "ko_kr"
    assert suggest_game_codes("jp", codes)["ja"] == "jp"
