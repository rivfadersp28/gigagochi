from __future__ import annotations

import pytest

from app.schemas import LocalPetChatContext
from app.services import travel_video_prototype_service as service


def sample_pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "petId": "pet-prototype",
            "name": "Листик",
            "description": "маленький листолицый питомец",
            "stage": "baby",
            "mood": "happy",
            "stats": {"hunger": 80, "happiness": 90, "energy": 75},
            "characterBible": {"identity": {"name": "Листик"}},
            "assetImages": {
                "baby": {"idle": "https://cdn.example.test/assets/baby-idle.png"},
            },
        }
    )


def test_prototype_job_runs_full_media_pipeline(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "GENERATED_ROOT", tmp_path)
    shots = (
        {
            "setting": "Широкая площадь лунного рынка под бумажными фонарями.",
            "action": "Листик спрыгивает с трамвая, замечает погасший фонарь и поднимает его.",
            "shotType": "wide",
            "transition": "Фонарь вспыхивает и заполняет кадр светом.",
        },
        {
            "setting": "Узкий проход между палатками с механическими птицами.",
            "action": "Свет указывает путь; Листик лавирует между птицами и догоняет вора-тень.",
            "shotType": "medium",
            "transition": "Тень ныряет в круглый люк, камера следует за ней.",
        },
        {
            "setting": "Крошечная обсерватория под прозрачным куполом.",
            "action": "Листик возвращает фонарь в проектор, и над рынком загораются созвездия.",
            "shotType": "close-up",
            "transition": "Камера отъезжает к сияющей панораме рынка.",
        },
    )
    monkeypatch.setattr(
        service,
        "_generate_scenario",
        lambda prompt, pet: (
            "Лунный рынок",
            service._scenario_text(shots),
            shots,
        ),
    )
    image_calls: list[dict[str, object]] = []

    def generate_image(**kwargs) -> bytes:
        image_calls.append(kwargs)
        return f"png-{len(image_calls)}".encode()

    monkeypatch.setattr(service, "generate_background_story_image_bytes", generate_image)
    video_calls: list[dict[str, object]] = []

    def generate_video(image: bytes, **kwargs) -> bytes:
        video_calls.append({"image": image, **kwargs})
        return b"mp4:" + image

    monkeypatch.setattr(service, "generate_background_story_video_bytes", generate_video)

    def concat_video(segment_paths) -> bytes:
        assert [path.read_bytes() for path in segment_paths] == [
            b"mp4:png-1",
            b"mp4:png-2",
            b"mp4:png-3",
        ]
        return b"joined-video"

    monkeypatch.setattr(service, "_concat_video_segments", concat_video)
    delivered: list[tuple[int, bytes]] = []
    monkeypatch.setattr(
        service,
        "send_travel_ready_video",
        lambda telegram_id, video: delivered.append((telegram_id, video)),
    )

    started = service.create_travel_video_prototype(
        telegram_id=62943754,
        prompt="Отправь Листика на лунный рынок",
        request_key="01234567-89ab-4cde-8fab-0123456789ab",
        pet=sample_pet(),
    )
    service.generate_travel_video_prototype(
        job_id=started.jobId,
        telegram_id=62943754,
    )
    ready = service.read_travel_video_prototype(
        started.jobId,
        telegram_id=62943754,
    )

    assert ready.status == "ready"
    assert ready.title == "Лунный рынок"
    assert ready.scenario and "0–5 сек." in ready.scenario
    assert "10–15 сек." in ready.scenario
    assert ready.imageUrl and ready.imageUrl.startswith("/static/generated/")
    assert ready.videoUrl and ready.videoUrl.startswith("/static/generated/")
    assert (tmp_path / started.jobId / service.IMAGE_FILE_NAME).read_bytes() == b"png-1"
    assert (tmp_path / started.jobId / service.VIDEO_FILE_NAME).read_bytes() == b"joined-video"
    assert len(image_calls) == 3
    assert all(
        call["image_size"] == service.TRAVEL_VIDEO_PROTOTYPE_IMAGE_SIZE for call in image_calls
    )
    assert all(
        call["composition_direction"] == service.TRAVEL_VIDEO_PROTOTYPE_COMPOSITION
        for call in image_calls
    )
    assert len(video_calls) == 3
    assert all(
        call["duration_seconds"] == service.TRAVEL_VIDEO_PROTOTYPE_SHOT_DURATION_SECONDS
        for call in video_calls
    )
    assert all(
        call["aspect_ratio"] == service.TRAVEL_VIDEO_PROTOTYPE_ASPECT_RATIO for call in video_calls
    )
    assert shots[0]["action"] in str(video_calls[0]["prompt"])
    assert shots[1]["action"] in str(video_calls[1]["prompt"])
    assert shots[2]["action"] in str(video_calls[2]["prompt"])
    assert delivered == [(62943754, b"joined-video")]


def test_repeated_request_key_reuses_the_same_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "GENERATED_ROOT", tmp_path)
    request_key = "01234567-89ab-4cde-8fab-0123456789ab"

    first = service.create_travel_video_prototype(
        telegram_id=62943754,
        prompt="Первый запрос",
        request_key=request_key,
        pet=sample_pet(),
    )
    repeated = service.create_travel_video_prototype(
        telegram_id=62943754,
        prompt="Изменённый запрос не должен создать вторую генерацию",
        request_key=request_key,
        pet=sample_pet(),
    )

    assert repeated.jobId == first.jobId
    assert repeated.prompt == "Первый запрос"
    assert len(list(tmp_path.glob("travel-video-prototype-*"))) == 1


def test_interrupted_job_reuses_saved_assets_on_resume(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "GENERATED_ROOT", tmp_path)
    shots = (
        {
            "setting": "Первая мастерская у моря.",
            "action": "Листик находит карту и открывает дверь в порт.",
            "shotType": "wide",
            "transition": "Карта превращается в волну.",
        },
        {
            "setting": "Парусник среди бумажных волн.",
            "action": "Листик удерживает парус и направляет корабль к маяку.",
            "shotType": "medium",
            "transition": "Свет маяка заполняет кадр.",
        },
        {
            "setting": "Тихий остров под звёздами.",
            "action": "Листик ставит карту в рамку и встречает рассвет.",
            "shotType": "close-up",
            "transition": "Камера поднимается к небу.",
        },
    )
    scenario_calls = 0
    image_calls = 0
    video_calls = 0

    def scenario(_prompt, _pet):
        nonlocal scenario_calls
        scenario_calls += 1
        return "Морской путь", service._scenario_text(shots), shots

    def image(**_kwargs):
        nonlocal image_calls
        image_calls += 1
        return f"image-{image_calls}".encode()

    def video(image_bytes, **_kwargs):
        nonlocal video_calls
        video_calls += 1
        if video_calls == 2:
            raise KeyboardInterrupt
        return b"clip:" + image_bytes

    monkeypatch.setattr(service, "_generate_scenario", scenario)
    monkeypatch.setattr(service, "generate_background_story_image_bytes", image)
    monkeypatch.setattr(service, "generate_background_story_video_bytes", video)
    monkeypatch.setattr(service, "_concat_video_segments", lambda _paths: b"joined")
    monkeypatch.setattr(service, "send_travel_ready_video", lambda *_args: None)
    started = service.create_travel_video_prototype(
        telegram_id=62943754,
        prompt="К морю",
        request_key="01234567-89ab-4cde-8fab-0123456789ab",
        pet=sample_pet(),
    )

    with pytest.raises(KeyboardInterrupt):
        service.generate_travel_video_prototype(
            job_id=started.jobId,
            telegram_id=62943754,
        )
    service.generate_travel_video_prototype(
        job_id=started.jobId,
        telegram_id=62943754,
    )

    assert scenario_calls == 1
    assert image_calls == 3
    assert video_calls == 4
    assert (
        service.read_travel_video_prototype(
            started.jobId,
            telegram_id=62943754,
        ).status
        == "ready"
    )


def test_ready_video_delivery_failure_is_best_effort(monkeypatch, caplog) -> None:
    def fail_delivery(_telegram_id: int, _video: bytes) -> None:
        raise RuntimeError("Telegram unavailable")

    monkeypatch.setattr(service, "send_travel_ready_video", fail_delivery)

    delivered = service._send_ready_video_best_effort(
        job_id="travel-video-prototype-0123456789abcdef0123456789abcdef",
        telegram_id=62943754,
        video=b"joined-video",
    )

    assert delivered is False
    assert "travel video Telegram delivery failed" in caplog.text
