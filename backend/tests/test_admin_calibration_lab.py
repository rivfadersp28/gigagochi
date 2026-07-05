from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import calibration_lab_service

LOCAL_HEADERS = {
    "host": "localhost:8000",
    "origin": "http://localhost:3000",
}


def enabled_settings(token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        enable_admin_generation_lab=True,
        admin_generation_lab_token=token,
    )


def admin_client(monkeypatch, tmp_path, *, token: str | None = None) -> TestClient:
    monkeypatch.setattr(
        "app.routers.admin_generation_lab.get_settings",
        lambda: enabled_settings(token),
    )
    monkeypatch.setattr(calibration_lab_service, "CALIBRATION_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        calibration_lab_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_model="test-model"),
    )

    def fake_candidate(
        *,
        description: str,
        run_id: str,
        task_id: str,
        task_type: str,
        candidate_index: int,
        prompt_variant: str,
        include_debug: bool,
        shared_character_bible=None,
        attempt: int = 0,
    ) -> dict:
        del run_id, task_type, shared_character_bible, attempt
        label = chr(ord("a") + candidate_index)
        return {
            "candidateId": f"{task_id}_cand_{label}",
            "promptVariant": prompt_variant,
            "model": "test-model",
            "seed": f"seed-{label}",
            "characterBible": {
                "species": f"вид {label}",
                "personality": "живой и конкретный",
                "lore": {
                    "world": {
                        "story": (
                            f"{description}: дом в мастерской, потому что там есть дело"
                        )
                    },
                    "home": {"story": "живет у ящика с бирками"},
                    "origin": {"story": "учится не теряться"},
                    "relationships": {"story": "есть старший ключник"},
                    "inner_life": {
                        "core_want": "быть полезным",
                        "inner_conflict": "боится ошибиться",
                    },
                    "story_seeds": ["прозвище друга", "скрытая полка"],
                },
            },
            "turns": [],
            "autoScore": 80 + candidate_index,
            "qualityFlags": [],
            "debug": {"enabled": include_debug},
        }

    monkeypatch.setattr(calibration_lab_service, "generate_candidate", fake_candidate)
    return TestClient(app)


def test_calibration_lab_create_run_next_vote_and_exports(monkeypatch, tmp_path) -> None:
    client = admin_client(monkeypatch, tmp_path)

    create_response = client.post(
        "/admin/calibration-lab/runs",
        headers=LOCAL_HEADERS,
        json={
            "taskType": "full_character_pairwise",
            "descriptions": ["маленький дракон", "мягкий робот"],
            "count": 2,
            "candidatesPerTask": 2,
            "promptVariants": ["current", "mixed_cards"],
            "includeDebug": True,
            "autoFilterBadCandidates": True,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["runId"].startswith("cal_")
    assert len(created["taskIds"]) == 2

    status_response = client.get("/admin/calibration-lab/status", headers=LOCAL_HEADERS)
    assert status_response.status_code == 200
    assert status_response.json()["storage"] == "jsonl"
    assert status_response.json()["taskCount"] == 2
    assert status_response.json()["voteCount"] == 0

    next_response = client.get("/admin/calibration-lab/tasks/next", headers=LOCAL_HEADERS)
    assert next_response.status_code == 200
    first_task = next_response.json()
    assert first_task["taskId"] == created["taskIds"][0]
    assert first_task["candidateIds"][0].endswith("_cand_a")
    assert first_task["candidates"][1]["promptVariant"] == "mixed_cards"

    vote_response = client.post(
        "/admin/calibration-lab/votes",
        headers=LOCAL_HEADERS,
        json={
            "taskId": first_task["taskId"],
            "winnerCandidateId": first_task["candidateIds"][0],
            "outcome": "winner",
            "positiveTags": ["живее"],
            "negativeTags": ["слишком сухо"],
            "note": "A держит мир лучше.",
            "latencyMs": 1234,
        },
    )

    assert vote_response.status_code == 200
    vote = vote_response.json()
    assert vote["runId"] == created["runId"]
    assert vote["winnerCandidateId"] == first_task["candidateIds"][0]

    vote_lines = (tmp_path / "votes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(vote_lines) == 1
    assert json.loads(vote_lines[0])["positiveTags"] == ["живее"]

    next_after_vote = client.get("/admin/calibration-lab/tasks/next", headers=LOCAL_HEADERS)
    assert next_after_vote.status_code == 200
    assert next_after_vote.json()["taskId"] == created["taskIds"][1]

    export_votes = client.get("/admin/calibration-lab/export/votes", headers=LOCAL_HEADERS)
    assert export_votes.status_code == 200
    assert export_votes.text.strip()
    assert json.loads(export_votes.text.splitlines()[0])["voteId"] == vote["voteId"]

    export_winners = client.get(
        "/admin/calibration-lab/export/winners?format=json",
        headers=LOCAL_HEADERS,
    )
    assert export_winners.status_code == 200
    winners = export_winners.json()
    assert len(winners) == 1
    assert winners[0]["candidate"]["candidateId"] == first_task["candidateIds"][0]


def test_calibration_lab_next_returns_null_when_all_tasks_voted(monkeypatch, tmp_path) -> None:
    client = admin_client(monkeypatch, tmp_path)

    create_response = client.post(
        "/admin/calibration-lab/runs",
        headers=LOCAL_HEADERS,
        json={
            "taskType": "lore_pairwise",
            "descriptions": ["маленький дракон"],
            "count": 1,
            "candidatesPerTask": 2,
            "promptVariants": ["current", "mixed_cards"],
            "includeDebug": False,
            "autoFilterBadCandidates": False,
        },
    )
    task_id = create_response.json()["taskIds"][0]
    task = client.get(f"/admin/calibration-lab/tasks/{task_id}", headers=LOCAL_HEADERS).json()

    response = client.post(
        "/admin/calibration-lab/votes",
        headers=LOCAL_HEADERS,
        json={
            "taskId": task_id,
            "winnerCandidateId": task["candidateIds"][0],
            "outcome": "winner",
            "positiveTags": [],
            "negativeTags": [],
            "note": "",
            "latencyMs": 1,
        },
    )
    assert response.status_code == 200

    next_response = client.get("/admin/calibration-lab/tasks/next", headers=LOCAL_HEADERS)
    assert next_response.status_code == 200
    assert next_response.json() is None


def test_calibration_lab_reuses_admin_generation_lab_access_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.admin_generation_lab.get_settings",
        lambda: SimpleNamespace(
            enable_admin_generation_lab=False,
            admin_generation_lab_token=None,
        ),
    )
    monkeypatch.setattr(calibration_lab_service, "CALIBRATION_DATA_DIR", tmp_path)
    client = TestClient(app)

    response = client.get("/admin/calibration-lab/status", headers=LOCAL_HEADERS)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ADMIN_GENERATION_LAB_DISABLED"
