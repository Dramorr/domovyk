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


class NotRegistered(DomainError):
    code = "not_registered"


class AlreadyRegistered(Conflict):
    code = "already_registered"


class ConsentOutdated(Conflict):
    """Користувач погоджувався з іншою версією правил/політики."""

    code = "consent_outdated"


class NotAdmin(PermissionDenied):
    code = "not_admin"


class NotStaff(PermissionDenied):
    code = "not_staff"


class NotOwner(PermissionDenied):
    """Дія з чужим об'єктом. Для чужого ID відповідаємо як «не знайдено» в інтерфейсах."""

    code = "not_owner"


class TooManyDocuments(InvalidInput):
    code = "too_many_documents"


class PhoneRequired(PermissionDenied):
    code = "phone_required"


class PropertyAlreadyYours(Conflict):
    code = "property_already_yours"


class DuplicateAddress(Conflict):
    """Об'єкт за цією адресою вже є в Домовику (FR-PRP-2)."""

    code = "duplicate_address"


class OwnershipNotVerified(PermissionDenied):
    """Дія потребує підтвердженої власності (FR-PRP-4, правило 5)."""

    code = "ownership_not_verified"


class ServiceUnavailable(DomainError):
    """Зовнішній сервіс (геокодер, Telegram) тимчасово недоступний."""

    code = "service_unavailable"


class ProtectedRole(PermissionDenied):
    """Дію не можна застосувати до адміністратора."""

    code = "protected_role"


class Banned(PermissionDenied):
    code = "banned"


class AccountInactive(PermissionDenied):
    code = "account_inactive"
