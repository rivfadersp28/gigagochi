from app.services.pet_reply_engine.lite_generator import _limit_push_reply_sentences
from app.services.pet_reply_engine.reply_limits import clamp_reply_text


def test_clamp_reply_text_does_not_keep_schema_truncation_mid_word() -> None:
    reply = "Я слушаю дождь и старые камни. " + "звон " * 60 + "обор"
    limited = reply[:300]

    result = clamp_reply_text(limited, 300)

    assert len(limited) == 300
    assert len(result) < 300
    assert result.endswith("звон…")


def test_clamp_reply_text_removes_terminal_period() -> None:
    assert clamp_reply_text("Я рядом.", 300) == "Я рядом"


def test_clamp_reply_text_keeps_internal_periods_and_other_endings() -> None:
    assert clamp_reply_text("Я рядом. Я слушаю.", 300) == "Я рядом. Я слушаю"
    assert clamp_reply_text("Ты рядом?", 300) == "Ты рядом?"
    assert clamp_reply_text("Я думаю…", 300) == "Я думаю…"
    assert clamp_reply_text("Я думаю...", 300) == "Я думаю..."


def test_clamp_reply_text_removes_period_before_closing_quote() -> None:
    assert clamp_reply_text("«Я рядом.»", 300) == "«Я рядом»"


def test_clamp_reply_text_removes_period_at_natural_truncation_break() -> None:
    reply = f"{'слово ' * 30}конец. {'хвост ' * 30}"

    result = clamp_reply_text(reply, 220)

    assert result.endswith("конец")


def test_push_reply_keeps_at_most_two_short_sentences() -> None:
    result = _limit_push_reply_sentences(
        "Я скучаю. Загляни ко мне? Я уже подготовил новую историю!"
    )

    assert result == "Я скучаю. Загляни ко мне?"
    assert len(result) <= 120
