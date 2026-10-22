from subs_parser import Cue, CueIndex, cue_at, parse_srt, parse_vtt, srt_to_vtt


def test_parse_srt_and_lookup_after_seek():
    cues = parse_srt(
        """1\n00:00:01,000 --> 00:00:03,500\nHello\nworld\n\n2\n00:00:04,000 --> 00:00:05,000\nLater\n"""
    )

    assert cues == [
        Cue(1.0, 3.5, "Hello\nworld", "1"),
        Cue(4.0, 5.0, "Later", "2"),
    ]
    assert cue_at(cues, 0.99) is None
    assert cue_at(cues, 1.0).text == "Hello\nworld"
    assert cue_at(cues, 3.5) is None
    assert CueIndex(cues).lookup(4.25).text == "Later"


def test_parse_vtt_supports_identifiers_settings_and_notes():
    cues = parse_vtt(
        """WEBVTT\n\nNOTE\nignored metadata\n\nintro\n00:00:00.500 --> 00:00:02.25 align:start\nWelcome\n\n00:03.000 --> 00:04.000\nSecond\n"""
    )

    assert len(cues) == 2
    assert cues[0].identifier == "intro"
    assert cues[0].start == 0.5
    assert cues[0].end == 2.25
    assert cues[1].text == "Second"


def test_srt_to_vtt_converts_timestamps_and_header():
    converted = srt_to_vtt("1\n00:01:02,5 --> 00:01:03,050\nText\n")

    assert converted.startswith("WEBVTT\n\n")
    assert "00:01:02.500 --> 00:01:03.050" in converted
    assert converted.endswith("Text\n")
