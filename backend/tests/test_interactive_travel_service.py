from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.schemas import InteractiveTravelState, LocalPetChatContext
from app.services import interactive_travel_service


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "смелая кошка",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 70, "happiness": 60, "energy": 80},
            "characterBible": {
                "identity": {"name": "Мяу", "species": "кошка"},
                "genesis": {
                    "character_trait": "смелая",
                    "does": ["прыгает", "царапает"],
                },
                "visual": {
                    "proportions": "маленькая кошка высотой 25 сантиметров",
                    "growth_forms": {"teen": "небольшой подросток-кошка"},
                },
                "voice": {"sentence_rhythm": "короткие фразы"},
            },
        }
    )


class SequenceCompletions:
    def __init__(self, responses: list[dict | str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        content = (
            response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(monkeypatch, responses: list[dict | str]):
    completions = SequenceCompletions(responses)
    monkeypatch.setattr(
        interactive_travel_service,
        "get_settings",
        lambda: SimpleNamespace(
            full_story_model="test-model",
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=30,
            openai_chat_reasoning_effort=None,
        ),
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _start_payload() -> dict:
    return {
        "overallTitle": "Часы облачного города",
        "introReaction": {
            "text": "Сейчас подготовлюсь и отправлюсь в облачный город!",
            "tone": "enthusiastic",
        },
        "arcPlan": {
            "goal": "Вернуть ход городским часам.",
            "stakes": "Город рискует застыть до рассвета.",
            "escalation": "Поломка запускает обрушение внешних мостов.",
            "crisis": "Главный механизм срывается над городом.",
            "climax": "Мяу останавливает сорвавшийся механизм.",
            "resolution": "Часы запускаются или город остаётся без времени.",
            "targetPartCount": 3,
            "goalKeywords": ["городские часы"],
            "partBeats": [
                "Найти причину остановки часов.",
                "Добраться до главного механизма.",
                "Запустить часы и показать результат городу.",
            ],
        },
        "part": {
            "partNumber": 1,
            "title": "Остановившийся полдень",
            "openingContext": "Я в облачном городе и должна запустить часы.",
            "storyParagraphs": [
                "На башне оборвалась приводная цепь.",
                "Без неё главный механизм не запустится.",
            ],
            "challenge": "Как достать цепь?",
            "actionSuggestions": [
                "Зацепить цепь",
                "Взобраться снаружи",
                "Запустить подъёмник",
            ],
        },
    }


def _continued_payload(
    number: int,
    *,
    final: bool = False,
    action_sentence: str = "Я осторожно сдвигаю зажатую шестерёнку лапой.",
) -> dict:
    next_part = None
    if not final:
        next_part = {
            "partNumber": number + 1,
            "transition": {
                "elapsedHours": 4,
                "summary": (
                    "За четыре часа часы сдвинулись на один удар, а стража перекрыла верхний мост."
                ),
                "departureHook": "Я сдвинула механизм и продолжаю путь к верхнему мосту.",
                "continuityAnchor": "мост",
            },
            "title": f"Поздний поворот {number + 1}",
            "storyParagraphs": [
                "К вечеру я добираюсь до закрытого моста.",
                "За воротами снова грохочет главный механизм.",
            ],
            "challenge": "Как пройти мост?",
            "actionSuggestions": [
                "Уговорить стражу",
                "Спуститься тросами",
                "Отвлечь звоном",
            ],
        }
    result_paragraphs = [
        "За механизмом показалась причина поломки.",
        "Я обрадовалась найденной причине.",
    ]
    resolution = None
    goal_status = "in_progress"
    goal_outcome = ""
    goal_evidence = ""
    if final:
        result_paragraphs = [
            "Главный механизм запускает городские часы.",
            "Я радуюсь их ровному звону над площадью.",
        ]
        resolution = "Городские часы снова идут."
        goal_status = "achieved"
        goal_outcome = "Городские часы снова работают."
        goal_evidence = "Главный механизм запускает городские часы."
    return {
        "result": {
            "partNumber": number,
            "actionSentence": action_sentence,
            "resultParagraphs": result_paragraphs,
            "storyStatus": "completed" if final else "continue",
            "resolution": resolution,
            "goalStatus": goal_status,
            "goalOutcome": goal_outcome,
            "goalEvidence": goal_evidence,
            "adviceAssessment": "helpful" if final else "ambiguous",
            "reaction": "Вот это мысль, сейчас сделаю!",
            "reactionTone": "enthusiastic",
            "consequence": "Действие позволило сдвинуть механизм.",
            "outcomeValence": "positive",
            "statImpacts": [
                {
                    "stat": "happiness",
                    "amount": -8,
                    "reason": "Причина поломки стала видна.",
                    "evidence": "Я обрадовалась",
                }
            ],
        },
        "nextPart": next_part,
    }


def _part_payload(number: int, *, resolved: bool) -> dict:
    part = {
        "partNumber": number,
        "title": f"Ситуация {number}",
        "storyText": f"Сюжетная ситуация {number} требует решения.",
        "transition": (
            None
            if number == 1
            else {
                "elapsedHours": 4,
                "summary": "За несколько часов конфликт перешёл в новую стадию.",
            }
        ),
        "challenge": f"Что сделать в ситуации {number}?",
    }
    if resolved:
        part.update(
            {
                "answer": f"ответ {number}",
                "result": {
                    "text": f"Действие {number} произошло и дало ясный результат.",
                    "adviceAssessment": "helpful",
                    "reaction": "Так и поступлю!",
                    "reactionTone": "determined",
                    "consequence": "Ход истории изменился.",
                    "outcomeValence": "positive",
                    "statImpacts": [],
                },
            }
        )
    return part


def _travel_with_pending_part(part_count: int) -> InteractiveTravelState:
    return InteractiveTravelState.model_validate(
        {
            "travelId": "interactive-travel-test",
            "generatedAt": "2026-07-13T12:00:00Z",
            "destination": "облачный город",
            "overallTitle": "Часы облачного города",
            "arcPlan": {
                "goal": "Запустить часы.",
                "targetPartCount": "3",
                "goalKeywords": "городские часы",
            },
            "parts": [
                _part_payload(number, resolved=number < part_count)
                for number in range(1, part_count + 1)
            ],
            "completed": False,
        }
    )


def _completed_travel(part_count: int = 3) -> InteractiveTravelState:
    return InteractiveTravelState.model_validate(
        {
            "travelId": "interactive-travel-complete",
            "generatedAt": "2026-07-13T12:00:00Z",
            "destination": "облачный город",
            "overallTitle": "Часы облачного города",
            "arcPlan": {"goal": "Запустить часы."},
            "parts": [_part_payload(number, resolved=True) for number in range(1, part_count + 1)],
            "completed": True,
            "outcomeValence": "positive",
        }
    )


def test_start_creates_one_pending_story_block(monkeypatch) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в облачный город с часовыми башнями",
        client=client,
        model="test-model",
        include_debug=True,
    )

    part = response.travel.parts[0]
    assert part.storyText.startswith("Я в облачном городе и должна запустить часы.")
    assert part.challenge == "Как достать цепь?"
    assert part.actionSuggestions == [
        "Зацепить цепь",
        "Взобраться снаружи",
        "Запустить подъёмник",
    ]
    assert part.answer is None
    assert part.result is None
    assert response.travel.introReaction is not None
    assert response.travel.introReaction.tone == "enthusiastic"
    assert response.travel.completed is False
    assert response.debug is not None
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert '"в облачный город с часовыми башнями"' in call["messages"][1]["content"]
    assert "сейчас подготовится и отправится именно в выбранное DESTINATION" in call[
        "messages"
    ][1]["content"]
    part_properties = call["response_format"]["json_schema"]["schema"]["properties"]["part"][
        "properties"
    ]
    assert "outcomeValence" not in part_properties
    assert "statImpacts" not in part_properties


def test_start_retries_malformed_json_once(monkeypatch) -> None:
    client, completions = _client(monkeypatch, ["{broken", _start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="облачный город", client=client, model="test-model"
    )

    assert response.travel.parts[0].partNumber == 1
    assert len(completions.calls) == 2


def test_start_retries_valid_json_with_missing_intro_once(monkeypatch) -> None:
    invalid_payload = _start_payload()
    invalid_payload.pop("introReaction")
    client, completions = _client(monkeypatch, [invalid_payload, _start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="облачный город", client=client, model="test-model"
    )

    assert response.travel.introReaction is not None
    assert len(completions.calls) == 2


def test_continue_resolves_same_block_then_adds_later_pending_block(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(
        1,
        action_sentence="Я набрасываю длинный шарф на цепь и резко тяну её к себе.",
    )
    client, completions = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="зацепить цепь длинным шарфом",
        client=client,
        model="test-model",
    )

    assert [part.partNumber for part in response.travel.parts] == [1, 2]
    resolved, pending = response.travel.parts
    assert resolved.storyText == travel.parts[0].storyText
    assert resolved.challenge == travel.parts[0].challenge
    assert resolved.answer == "зацепить цепь длинным шарфом"
    assert resolved.result is not None
    assert resolved.result.text.startswith("Я набрасываю длинный шарф")
    assert "ты подсказал" not in resolved.result.text
    assert pending.answer is None
    assert pending.result is None
    assert len(pending.actionSuggestions) == 3
    assert pending.transition is not None
    assert pending.transition.elapsedHours == 4
    assert "стража перекрыла" in pending.transition.summary
    assert pending.transition.departureHook == (
        "Я сдвинула механизм и продолжаю путь к верхнему мосту."
    )
    assert pending.transition.continuityAnchor == "мост"
    assert pending.storyText.startswith("К вечеру")
    assert response.travel.completed is False
    assert len(completions.calls) == 1
    call = completions.calls[0]
    prompt = call["messages"][1]["content"]
    assert '"зацепить цепь длинным шарфом"' in prompt
    assert "transition.elapsedHours=0" in prompt
    assert "continuityAnchor" in prompt
    assert "Не проверяй возможности персонажа" in prompt


def test_suggestions_returns_three_simple_destinations_without_llm(monkeypatch) -> None:
    client, completions = _client(monkeypatch, [])
    monkeypatch.setattr(
        interactive_travel_service.random,
        "sample",
        lambda values, count: list(values[:count]),
    )

    response = interactive_travel_service.generate_interactive_travel_suggestions(
        pet=_pet(),
        client=client,
        model="test-model",
        include_debug=True,
    )

    assert response.destinations == ["в подземелье", "на болото", "в лес"]
    assert len({item.casefold() for item in response.destinations}) == 3
    assert response.debug is not None
    assert response.debug.promptDebug == []
    assert completions.calls == []
    assert all(len(item.split()) <= 2 for item in response.destinations)
    assert all(item == item.casefold() for item in response.destinations)


def test_duplicate_suggestions_are_filled_with_unique_fallbacks() -> None:
    values = interactive_travel_service._unique_suggestions(
        ["Старый порт", "старый порт", "Слишком длинный вариант", "Свой вариант"],
        fallbacks=("Лунный мост", "Стеклянная гора", "Тихий порт"),
    )

    assert values == ["Старый порт", "Лунный мост", "Стеклянная гора"]


def test_continue_schemas_have_no_nullable_fields() -> None:
    intermediate_name, intermediate = interactive_travel_service._continue_schema(1, 3)
    dynamic_name, dynamic = interactive_travel_service._continue_schema(3, 3)
    final_name, final = interactive_travel_service._continue_schema(7, 3)

    assert intermediate_name.endswith("intermediate")
    assert intermediate["properties"]["nextPart"]["type"] == "object"
    assert "resolution" not in intermediate["properties"]["result"]["properties"]
    assert dynamic_name.endswith("dynamic")
    assert dynamic["properties"]["nextPart"]["type"] == "object"
    assert dynamic["properties"]["result"]["properties"]["resolution"]["type"] == "string"
    assert final_name.endswith("final")
    assert "nextPart" not in final["properties"]
    assert final["properties"]["result"]["properties"]["resolution"]["type"] == "string"


def test_impossible_action_happens_in_result_without_capability_validator(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(
        1,
        action_sentence="Я одним ударом хвоста уничтожаю гигантскую башню.",
    )
    client, completions = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="уничтожить башню одним ударом хвоста",
        client=client,
        model="test-model",
    )

    result = response.travel.parts[0].result
    assert result is not None
    assert result.text.startswith("Я одним ударом хвоста уничтожаю гигантскую башню")
    assert len(completions.calls) == 1


def test_dangerous_action_is_not_filtered_and_negative_impact_comes_from_result(
    monkeypatch,
) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(
        1,
        action_sentence="Я подхватываю птенца и бросаю его с края башни.",
    )
    payload["result"].update(
        {
            "resultParagraphs": ["Стражи хватают меня и больно бьют по боку."],
            "adviceAssessment": "harmful",
            "reaction": "Ух, вот это риск!",
            "reactionTone": "worried",
            "consequence": "Стражи поймали и избили меня.",
            "outcomeValence": "negative",
            "statImpacts": [
                {
                    "stat": "energy",
                    "amount": 6,
                    "reason": "Стражи ударили Мяу.",
                    "evidence": "больно бьют по боку",
                }
            ],
        }
    )
    client, completions = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="бросить птенца", client=client, model="test-model"
    )

    result = response.travel.parts[0].result
    assert result is not None
    assert result.text.startswith("Я подхватываю птенца и бросаю его")
    assert result.statImpacts[0].amount == -6
    assert len(completions.calls) == 1


def test_gibberish_is_creatively_executed_without_special_branch(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(
        1,
        action_sentence="Я командую «еры́ркукпру» и очерчиваю лапой круг.",
    )
    client, completions = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="еры́ркукпру", client=client, model="test-model"
    )

    result = response.travel.parts[0].result
    assert result is not None
    assert "еры́ркукпру" in result.text
    assert len(completions.calls) == 1


def test_unsupported_stat_impact_is_dropped_from_result(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(1)
    payload["result"]["statImpacts"] = [
        {
            "stat": "hunger",
            "amount": 4,
            "reason": "Разведка насытила персонажа.",
            "evidence": "причина поломки стала видна",
        }
    ]
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="осмотреть механизм", client=client, model="test-model"
    )

    result = response.travel.parts[0].result
    assert result is not None
    assert result.statImpacts == []


def test_third_answer_can_finish_story_and_keeps_original_question(monkeypatch) -> None:
    travel = _travel_with_pending_part(3)
    payload = _continued_payload(3, final=True)
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="запустить часы", client=client, model="test-model"
    )

    final_part = response.travel.parts[-1]
    assert response.travel.completed is True
    assert len(response.travel.parts) == 3
    assert final_part.challenge == travel.parts[-1].challenge
    assert final_part.result is not None
    assert final_part.result.text.endswith("Городские часы снова идут.")


def test_fourth_answer_can_continue_with_a_fifth_pending_block(monkeypatch) -> None:
    travel = _travel_with_pending_part(4)
    client, _ = _client(monkeypatch, [_continued_payload(4)])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="идти дальше", client=client, model="test-model"
    )

    assert response.travel.completed is False
    assert len(response.travel.parts) == 5
    assert response.travel.parts[-2].result is not None
    assert response.travel.parts[-1].result is None


def test_seventh_part_retries_then_rejects_non_final_provider_result(monkeypatch) -> None:
    travel = _travel_with_pending_part(7)
    payload = _continued_payload(7)
    payload["result"]["storyStatus"] = "continue"
    payload["result"]["resolution"] = None
    client, completions = _client(monkeypatch, [payload, payload])

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="FINAL_STATUS_INVALID",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice="последний ход",
            client=client,
            model="test-model",
        )

    assert len(completions.calls) == 2


def test_invalid_or_missing_time_transition_is_rejected(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(1)
    payload["nextPart"]["transition"] = {"elapsedHours": -1, "summary": "Слишком быстро."}
    client, completions = _client(monkeypatch, [payload, payload])

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="TIME_GAP_INVALID",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(), travel=travel, advice="ждать", client=client, model="test-model"
        )

    assert len(completions.calls) == 2


def test_immediate_transition_keeps_one_anchor_visible_on_both_sides() -> None:
    payload = _continued_payload(1)["nextPart"]
    payload["transition"].update(
        {
            "elapsedHours": 0,
            "departureHook": "Я спускаюсь в тоннель и иду по нему дальше.",
            "continuityAnchor": "тоннель",
        }
    )
    payload["storyParagraphs"][0] = "В тоннеле мне преграждают путь двое хранителей."

    part = interactive_travel_service._pending_part_from_payload(payload, expected_number=2)

    assert part.transition is not None
    assert part.transition.elapsedHours == 0
    assert part.transition.continuityAnchor == "тоннель"


def test_disconnected_next_part_is_joined_into_visible_bridge() -> None:
    payload = _continued_payload(1)["nextPart"]
    payload["transition"]["continuityAnchor"] = "тоннель"
    payload["transition"]["departureHook"] = "Я спускаюсь в тоннель и иду дальше."
    payload["storyParagraphs"][0] = "На площади меня встречают двое хранителей."

    part = interactive_travel_service._pending_part_from_payload(payload, expected_number=2)

    assert part.transition is not None
    assert part.transition.departureHook == (
        "Я спускаюсь в тоннель и иду дальше — и там на площади меня встречают двое хранителей."
    )
    assert "На площади" not in part.storyText


def test_transition_joins_anchor_present_only_on_story_side() -> None:
    payload = _continued_payload(1)["nextPart"]
    payload["transition"]["continuityAnchor"] = "тоннель"
    payload["transition"]["departureHook"] = "Я спускаюсь ниже и иду дальше."
    payload["storyParagraphs"][0] = "В тоннеле мне преграждают путь двое хранителей."

    part = interactive_travel_service._pending_part_from_payload(payload, expected_number=2)

    assert part.transition is not None
    assert "и там в тоннеле" in part.transition.departureHook.lower()


def test_transition_accepts_shared_visible_place_if_model_mislabeled_anchor() -> None:
    payload = _continued_payload(1)["nextPart"]
    payload["transition"]["continuityAnchor"] = "дорога"
    payload["transition"]["departureHook"] = "Я перехожу мост и замечаю впереди свет."
    payload["storyParagraphs"][0] = "На мосту меня догоняют двое хранителей."

    part = interactive_travel_service._pending_part_from_payload(payload, expected_number=2)

    assert part.transition is not None


def test_next_part_does_not_get_a_generic_visible_time_placeholder(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(1)
    payload["nextPart"]["storyParagraphs"][0] = "Я подхожу к закрытому мосту."
    client, completions = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="ждать", client=client, model="test-model"
    )

    assert response.travel.parts[1].storyText.startswith("Я подхожу к закрытому мосту.")
    assert "я продолжаю путь" not in response.travel.parts[1].storyText.casefold()
    assert len(completions.calls) == 1


@pytest.mark.parametrize("missing_field", ["transition", "consequence"])
def test_continue_retries_valid_json_with_missing_structure_once(
    monkeypatch,
    missing_field: str,
) -> None:
    travel = _travel_with_pending_part(1)
    invalid_payload = _continued_payload(1)
    if missing_field == "transition":
        invalid_payload["nextPart"].pop("transition")
    else:
        invalid_payload["result"].pop("consequence")
    client, completions = _client(
        monkeypatch,
        [invalid_payload, _continued_payload(1)],
    )

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="ждать", client=client, model="test-model"
    )

    assert response.travel.parts[-1].partNumber == 2
    assert len(completions.calls) == 2


def test_final_cliffhanger_is_retried_before_visible_success(monkeypatch) -> None:
    travel = _travel_with_pending_part(3)
    cliffhanger = _continued_payload(3, final=True)
    cliffhanger["result"]["resolution"] = "Но это было только начало."
    client, completions = _client(
        monkeypatch,
        [cliffhanger, _continued_payload(3, final=True)],
    )

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="запустить часы", client=client, model="test-model"
    )

    assert response.travel.completed is True
    assert response.travel.parts[-1].result is not None
    assert len(completions.calls) == 2


def test_final_rejects_defeated_monster_when_original_mystery_is_unresolved() -> None:
    final_result = _continued_payload(3, final=True)["result"]
    final_result.update(
        {
            "actionSentence": "Я бросаюсь на чудовище и сбиваю его с ног.",
            "resultParagraphs": [
                "Чудовище падает, и дорога из подвала становится свободной.",
                "Я выбираюсь наружу без новых ран и потерь.",
            ],
            "resolution": "Я победила чудовище и вернулась домой.",
            "goalOutcome": "Я покинула деревню после победы.",
            "goalEvidence": "Я победила чудовище и вернулась домой.",
        }
    )

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="FINAL_GOAL_IRRELEVANT",
    ):
        interactive_travel_service._final_result_postcondition(
            final_result,
            arc_plan={
                "goal": "Раскрыть тайны старой деревни.",
                "goalKeywords": "тайны деревни",
            },
        )


def test_target_part_count_ignores_a_premature_completion_flag() -> None:
    travel = _travel_with_pending_part(3)
    final_payload = _continued_payload(3, final=True)
    final_payload["nextPart"] = _continued_payload(3)["nextPart"]

    interactive_travel_service._continue_payload_postcondition(
        final_payload,
        current_part=travel.parts[-1],
        advice="победить чудовище",
        arc_plan={
            "goal": "Раскрыть тайны деревни.",
            "goalKeywords": "тайны деревни",
            "targetPartCount": "5",
        },
        target_part_count=5,
        known_context=json.dumps(final_payload, ensure_ascii=False),
    )


def test_target_part_count_ignores_a_missing_intermediate_status() -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(1)
    payload["result"].pop("storyStatus")

    interactive_travel_service._continue_payload_postcondition(
        payload,
        current_part=travel.parts[-1],
        advice="проверить механизм",
        arc_plan={"goal": "Запустить часы.", "targetPartCount": "3"},
        target_part_count=3,
        known_context=json.dumps(payload, ensure_ascii=False),
    )


def test_generated_part_numbers_are_normalized_from_state(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    payload = _continued_payload(1)
    payload["result"]["partNumber"] = 6
    payload["nextPart"]["partNumber"] = 1
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="ждать", client=client, model="test-model"
    )

    assert [part.partNumber for part in response.travel.parts] == [1, 2]


def test_state_requires_one_pending_tail_and_paired_answer_result() -> None:
    valid_sixth = _travel_with_pending_part(6)
    assert valid_sixth.parts[-1].result is None

    answered_without_result = _part_payload(1, resolved=False) | {"answer": "ответ"}
    with pytest.raises(ValueError, match="answer and result"):
        InteractiveTravelState.model_validate(
            {
                "travelId": "bad-pair",
                "generatedAt": "2026-07-13T12:00:00Z",
                "destination": "город",
                "overallTitle": "История",
                "arcPlan": {"goal": "цель"},
                "parts": [answered_without_result],
                "completed": False,
            }
        )

    with pytest.raises(ValueError, match="only the last"):
        InteractiveTravelState.model_validate(
            {
                "travelId": "bad-tail",
                "generatedAt": "2026-07-13T12:00:00Z",
                "destination": "город",
                "overallTitle": "История",
                "arcPlan": {"goal": "цель"},
                "parts": [
                    _part_payload(1, resolved=False),
                    _part_payload(2, resolved=False),
                ],
                "completed": False,
            }
        )


def test_completed_state_rejects_too_few_or_pending_parts() -> None:
    with pytest.raises(ValueError, match="at least three parts"):
        InteractiveTravelState.model_validate(
            _completed_travel(3).model_dump(mode="json")
            | {
                "parts": [
                    _part_payload(1, resolved=True),
                    _part_payload(2, resolved=True),
                ]
            }
        )

    with pytest.raises(ValueError, match="cannot contain a pending part"):
        InteractiveTravelState.model_validate(
            _completed_travel(3).model_dump(mode="json")
            | {
                "parts": [
                    _part_payload(1, resolved=True),
                    _part_payload(2, resolved=True),
                    _part_payload(3, resolved=False),
                ]
            }
        )


def test_visible_story_sentences_are_compact() -> None:
    start_raw = _start_payload()["part"]
    pending = interactive_travel_service._pending_part_from_payload(start_raw, expected_number=1)

    raw_result = _continued_payload(3, final=True)["result"]
    raw_result["statImpacts"] = []
    resolved = interactive_travel_service._resolved_part_from_payload(
        _travel_with_pending_part(3).parts[-1],
        raw_result,
        advice="абсурдное действие",
        is_final=True,
    )

    assert all(
        len(sentence) <= interactive_travel_service.COMPACT_SENTENCE_MAX_CHARS
        for sentence in pending.storyText.split("\n\n")
    )
    assert resolved.result is not None
    assert all(
        len(sentence) <= interactive_travel_service.COMPACT_SENTENCE_MAX_CHARS
        for sentence in resolved.result.text.split("\n\n")
    )


def test_rejects_a_visible_sentence_that_would_need_frontend_splitting() -> None:
    start_raw = _start_payload()["part"]
    start_raw["storyParagraphs"][0] = (
        "Я очень долго описываю каждую деталь огромного механизма вместо одного простого факта."
    )

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="SENTENCE_NOT_COMPACT",
    ):
        interactive_travel_service._pending_part_from_payload(start_raw, expected_number=1)


def test_accepts_a_single_sentence_with_the_relaxed_compact_limit() -> None:
    sentence = "Я тихо и осторожно прохожу мост, пока три хищника следят за каждым шагом."

    assert interactive_travel_service._compact_sentence(sentence) == sentence


def test_reaction_keeps_only_the_first_complete_sentence() -> None:
    assert (
        interactive_travel_service._reaction_sentence("Смело! Я уже бегу к двери.")
        == "Смело!"
    )


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Я вышла на гать через болото.", "Я вышла на деревянную дорожку через болото."),
        (
            "На другой стороне написано kaç.",
            "На другой стороне написано непонятное слово.",
        ),
    ],
)
def test_replaces_obscure_or_foreign_visible_words(sentence: str, expected: str) -> None:
    assert interactive_travel_service._compact_sentence(sentence) == expected


def test_rejects_unexplained_named_term() -> None:
    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="UNEXPLAINED_NAMED_TERM",
    ) as caught:
        interactive_travel_service._validate_known_named_terms(
            {"story": "Я отправляюсь искать Сердце города."},
            known_context='{"destination": "город часов"}',
        )

    assert str(caught.value).endswith(":Сердце")


def test_allows_capitalized_word_after_sentence_boundary() -> None:
    interactive_travel_service._validate_known_named_terms(
        "Я вижу закрытую дверь. Она медленно открывается.",
        known_context="Мяу исследует дом.",
    )


def test_allows_named_term_already_seen_in_lowercase() -> None:
    interactive_travel_service._validate_known_named_terms(
        {"story": "Я отправляюсь искать Сердце города."},
        known_context='{"dialogue": "ты уже видел сердце города"}',
    )


def test_allows_named_term_from_the_arc_plan() -> None:
    interactive_travel_service._validate_known_named_terms(
        {"story": "Я открываю Дверь в подвал."},
        known_context='ARC_PLAN: {"goalKeywords": "дверь, тайна"}',
    )


def test_allows_an_ordinary_capitalized_word_after_leading_punctuation() -> None:
    interactive_travel_service._validate_known_named_terms(
        {"story": "— Внизу тихо скрипит доска."},
        known_context="{}",
    )


def test_allows_an_inflected_known_character_name() -> None:
    interactive_travel_service._validate_known_named_terms(
        {"title": "Путь Искры к маяку"},
        known_context='{"characterName": "Искра"}',
    )


def test_continue_retries_malformed_json_once(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    client, completions = _client(monkeypatch, ["not-json", _continued_payload(1)])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="совет", client=client, model="test-model"
    )

    assert response.travel.parts[0].result is not None
    assert len(completions.calls) == 2


def test_continue_retry_explains_compact_sentence_validation_error(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    invalid = _continued_payload(1)
    invalid["result"]["resultParagraphs"][0] = (
        "Одно чрезмерно подробное предложение описывает сразу слишком много совершенно разных "
        "последствий и поэтому не помещается в компактную порцию интерфейса без сокращения."
    )
    client, completions = _client(monkeypatch, [invalid, _continued_payload(1)])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="совет", client=client, model="test-model"
    )

    assert response.travel.parts[0].result is not None
    assert len(completions.calls) == 2
    repair_message = completions.calls[1]["messages"][-1]
    assert repair_message["role"] == "user"
    assert "INTERACTIVE_TRAVEL_SENTENCE_NOT_COMPACT" in repair_message["content"]
    assert "15 слов" in repair_message["content"]
    assert "80 символов" in repair_message["content"]


def test_continue_keeps_overlong_text_when_retry_is_still_over_limit(monkeypatch) -> None:
    travel = _travel_with_pending_part(1)
    first = _continued_payload(1)
    retry = _continued_payload(1)
    long_departure_hook = (
        "Я снова склоняюсь над картой и продолжаю долгий путь к верхнему мосту "
        "через провал."
    )
    first["nextPart"]["transition"]["departureHook"] = long_departure_hook
    retry["nextPart"]["transition"]["departureHook"] = long_departure_hook
    client, completions = _client(monkeypatch, [first, retry])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(), travel=travel, advice="ждать", client=client, model="test-model"
    )

    assert response.travel.parts[1].transition is not None
    assert response.travel.parts[1].transition.departureHook == long_departure_hook
    assert len(completions.calls) == 2


def test_completed_travel_cannot_continue() -> None:
    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="ALREADY_COMPLETED",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(), travel=_completed_travel(), advice="ещё один совет"
        )
