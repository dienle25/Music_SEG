import random

from visong.noise import corrupt, telex, untelex
from visong.text import (analyzer, char_ngrams, clean, has_diacritics, remove_diacritics, swap_tone,
                         tone_of, with_bigrams)


def test_remove_diacritics_handles_d_bar_and_tones():
    assert remove_diacritics("Đường về nhà") == "Duong ve nha"
    assert not has_diacritics("anh yeu em") and has_diacritics("anh yêu em")


def test_clean_lowercases_and_drops_punctuation():
    assert clean("Hello, Thế_Giới!") == "hello thế giới"


def test_analyzers():
    assert analyzer("syl12")("anh yêu em") == ["anh", "yêu", "em", "anh_yêu", "yêu_em"]
    assert analyzer("nodiac12")("Anh yêu em") == ["anh", "yeu", "em", "anh_yeu", "yeu_em"]
    assert char_ngrams("anh em", 3) == ["#an", "anh", "nh#", "#em", "em#"]
    assert with_bigrams(["a", "b", "c"]) == ["a", "b", "c", "a_b", "b_c"]


def test_tones():
    assert tone_of("người") == "huyen"
    assert swap_tone("hỏi", "hoi", "nga") == "hõi"


def test_telex_round_trip():
    assert telex("người") == "nguwowif"
    assert untelex("nguowif") == ("ngươi", "huyen")
    assert untelex("ddaay") == ("đây", None)
    assert untelex("hello") is None


def test_corrupt_rate_and_determinism():
    text = "anh yêu em nhiều lắm em ơi đừng đi"
    a = corrupt(text, 0.4, random.Random(1))
    b = corrupt(text, 0.4, random.Random(1))
    assert a == b
    noisy, info = a
    assert info["changed"] <= len(text.split())
    assert len(noisy.split()) == len(text.split())
    assert corrupt(text, 0.0, random.Random(1))[0] == text


def test_corrupt_reaches_the_target_number_of_syllables():
    # "thấy nhớ hôm qua sao thật khác hôm nay" once came out unchanged at rate 0.1
    text = "thấy nhớ hôm qua sao thật khác hôm nay"
    for seed in range(200):
        noisy, info = corrupt(text, 0.3, random.Random(seed))
        assert info["changed"] == 3
        assert sum(a != b for a, b in zip(noisy.split(), text.split())) == 3
