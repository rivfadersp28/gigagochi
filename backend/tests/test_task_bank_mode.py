from types import SimpleNamespace

from app.services import task_bank_mode


def test_task_bank_mode_defaults_to_hard_and_persists_switch(monkeypatch, tmp_path) -> None:
    mode_path = tmp_path / "task-bank-mode.txt"
    monkeypatch.setattr(
        task_bank_mode,
        "get_settings",
        lambda: SimpleNamespace(interactive_travel_task_bank_mode_path=str(mode_path)),
    )

    assert task_bank_mode.read_task_bank_mode() == "hard"
    assert task_bank_mode.write_task_bank_mode("easy") == "easy"
    assert task_bank_mode.read_task_bank_mode() == "easy"
    assert mode_path.read_text(encoding="utf-8") == "easy\n"


def test_invalid_persisted_task_bank_mode_falls_back_to_hard(monkeypatch, tmp_path) -> None:
    mode_path = tmp_path / "task-bank-mode.txt"
    mode_path.write_text("unknown\n", encoding="utf-8")
    monkeypatch.setattr(
        task_bank_mode,
        "get_settings",
        lambda: SimpleNamespace(interactive_travel_task_bank_mode_path=str(mode_path)),
    )

    assert task_bank_mode.read_task_bank_mode() == "hard"
