from app.services.pet_reply_engine.reply_limits import clamp_reply_text


def test_clamp_reply_text_does_not_keep_schema_truncation_mid_word() -> None:
    reply = "Я слушаю дождь и старые камни. " + "звон " * 60 + "обор"
    limited = reply[:300]

    result = clamp_reply_text(limited, 300)

    assert len(limited) == 300
    assert len(result) < 300
    assert result.endswith("звон…")


def test_clamp_reply_text_preserves_short_reply() -> None:
    assert clamp_reply_text("Я рядом.", 300) == "Я рядом."
