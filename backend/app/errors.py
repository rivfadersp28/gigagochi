from __future__ import annotations

from fastapi import HTTPException, status

ERROR_MESSAGES = {
    "EMPTY_PROMPT": "Опишите персонажа перед генерацией.",
    "PROMPT_TOO_LONG": "Описание слишком длинное. Сократите его до 300 символов.",
    "MISSING_OPENAI_API_KEY": "Сервис временно недоступен. Попробуйте позже.",
    "MISSING_KANDINSKY_API_KEY": "Сервис временно недоступен. Попробуйте позже.",
    "PET_NOT_FOUND": "Питомец не найден.",
    "PET_NOT_READY": "Питомец еще создается. Подождите немного.",
    "GENERATION_FAILED": "Не удалось сгенерировать персонажа. Попробуйте еще раз.",
    "IMAGE_SAVE_FAILED": "Не получилось подготовить питомца. Попробуйте ещё раз.",
    "CHAT_FAILED": "Не удалось получить ответ питомца. Попробуйте еще раз.",
    "DATABASE_ERROR": "Сервис временно недоступен. Попробуйте позже.",
}


def public_error(
    code: str,
    http_status: int = status.HTTP_400_BAD_REQUEST,
    *,
    include_diagnostic: bool = False,
) -> HTTPException:
    detail: dict[str, object] = {
        "code": code,
        "message": ERROR_MESSAGES.get(code, "Не получилось выполнить действие. Попробуйте снова."),
    }
    if include_diagnostic:
        detail["diagnostic"] = {"code": code}
    return HTTPException(
        status_code=http_status,
        detail=detail,
    )
