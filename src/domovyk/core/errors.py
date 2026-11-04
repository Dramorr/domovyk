"""Доменні помилки. Не HTTP і не Telegram: інтерфейси перекладають їх для користувача."""


class DomainError(Exception):
    """Базова доменна помилка. `code` — стабільний ключ для перекладу в інтерфейсах."""

    code = "domain_error"

    def __init__(self, message: str = "", **details: object) -> None:
        super().__init__(message or self.code)
        self.details = details


class NotFound(DomainError):
    code = "not_found"


class PermissionDenied(DomainError):
    code = "permission_denied"


class InvalidInput(DomainError):
    code = "invalid_input"


class InvalidTransition(DomainError):
    code = "invalid_transition"


class Conflict(DomainError):
    code = "conflict"
