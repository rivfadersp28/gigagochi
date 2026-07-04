from __future__ import annotations

from fastapi import HTTPException, status

ERROR_MESSAGES = {
    "EMPTY_PROMPT": "Опишите персонажа перед генерацией.",
    "PROMPT_TOO_LONG": "Описание слишком длинное. Сократите его до 300 символов.",
    "MISSING_OPENAI_API_KEY": "На сервере не настроен OpenAI API key.",
    "PET_NOT_FOUND": "Питомец не найден.",
    "PET_NOT_READY": "Питомец еще создается. Подождите немного.",
    "GENERATION_FAILED": "Не удалось сгенерировать персонажа. Попробуйте еще раз.",
    "IMAGE_SAVE_FAILED": "Не удалось сохранить изображение питомца.",
    "CHAT_FAILED": "Не удалось получить ответ питомца. Попробуйте еще раз.",
    "DATABASE_ERROR": "Ошибка базы данных.",
}


def public_error(code: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"code": code, "message": ERROR_MESSAGES.get(code, "Ошибка приложения.")},
    )
