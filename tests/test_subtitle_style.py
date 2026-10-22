from subtitle_style import DEFAULT_SUBTITLE_STYLE, normalize_subtitle_style


def test_defaults_are_complete_and_each_result_is_independent():
    expected = {
        "fontSize": 24,
        "color": "#ffffff",
        "backgroundOpacity": 0.5,
        "position": "bottom",
    }
    assert DEFAULT_SUBTITLE_STYLE == expected
    result = normalize_subtitle_style()
    assert result == expected
    assert normalize_subtitle_style({}) == expected
    assert normalize_subtitle_style("invalid") == expected
    result["fontSize"] = 40
    assert normalize_subtitle_style() == expected
    assert DEFAULT_SUBTITLE_STYLE == expected


def test_valid_values_are_normalized_without_changing_input():
    settings = {
        "fontSize": "32.5",
        "color": "#ABC123",
        "backgroundOpacity": "0.75",
        "position": "top",
        "unrecognized": "discard",
    }
    assert normalize_subtitle_style(settings) == {
        "fontSize": 33,
        "color": "#abc123",
        "backgroundOpacity": 0.75,
        "position": "top",
    }
    assert settings["fontSize"] == "32.5"
    assert settings["color"] == "#ABC123"


def test_numeric_settings_are_clamped_at_both_ends():
    assert normalize_subtitle_style({"fontSize": -1, "backgroundOpacity": -4}) == {
        **DEFAULT_SUBTITLE_STYLE,
        "fontSize": 12,
        "backgroundOpacity": 0,
    }
    assert normalize_subtitle_style({"fontSize": 999, "backgroundOpacity": 9}) == {
        **DEFAULT_SUBTITLE_STYLE,
        "fontSize": 48,
        "backgroundOpacity": 1,
    }


def test_invalid_values_fall_back_to_defaults():
    for number in (None, True, "", "not a number", float("nan"), float("inf")):
        result = normalize_subtitle_style({"fontSize": number, "backgroundOpacity": number})
        assert result["fontSize"] == DEFAULT_SUBTITLE_STYLE["fontSize"]
        assert result["backgroundOpacity"] == DEFAULT_SUBTITLE_STYLE["backgroundOpacity"]
    assert normalize_subtitle_style({"color": "red", "position": "left"}) == DEFAULT_SUBTITLE_STYLE
    assert normalize_subtitle_style({"color": "#fff", "position": None}) == DEFAULT_SUBTITLE_STYLE
    assert normalize_subtitle_style({"position": "middle"})["position"] == "middle"
