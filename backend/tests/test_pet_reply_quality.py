from __future__ import annotations

from app.services.pet_reply_engine.quality import quality_report_for_reply


def test_quality_rejects_unsupported_preference_list() -> None:
    report = quality_report_for_reply(
        question="что ты любишь?",
        reply="я люблю теплый утренний туман и синие лейки. короткие просьбы тоже.",
        lore={
            "home": {"story": "На моховой полке Кап спрятал его после кошки."},
            "inner_life": {"likes": ["моховая полка"]},
        },
        used_fallback=False,
    )

    assert not report["passed"]
    assert "unsupported_preference" in report["flags"]
    assert "no_lore_anchor" in report["flags"]
    assert report["axes"]["directness"] < 100
    assert report["axes"]["lore_grounding"] < 100


def test_quality_accepts_grounded_preference_answer() -> None:
    report = quality_report_for_reply(
        question="что ты любишь?",
        reply=(
            "мне нравится моховая полка, потому что там Кап спрятал меня "
            "после той кошки."
        ),
        lore={
            "home": {"story": "На моховой полке Кап спрятал его после кошки."},
            "relationships": {"friends": [{"name": "Кап"}]},
        },
        used_fallback=False,
    )

    assert report["passed"]
    assert report["flags"] == []
    assert report["axes"]["no_assistant_leak"] == 100


def test_quality_marks_generic_lore_answer() -> None:
    report = quality_report_for_reply(
        question="расскажи подробнее про дом",
        reply="я рядом",
        lore={"home": {"story": "Дом стоит на нижней полке теплицы номер четыре."}},
        used_fallback=False,
    )

    assert not report["passed"]
    assert "generic_reply" in report["flags"]
    assert "too_short_for_lore" in report["flags"]


def test_quality_marks_assistant_leak_and_generic_comfort() -> None:
    report = quality_report_for_reply(
        question="мне грустно",
        reply="Я ассистент, я всегда рядом, чем могу помочь?",
        lore=None,
        used_fallback=False,
    )

    assert not report["passed"]
    assert "no_assistant_leak" in report["flags"]
    assert "no_generic_comfort" in report["flags"]
    assert report["axes"]["no_assistant_leak"] == 0
    assert report["axes"]["no_generic_comfort"] == 0
