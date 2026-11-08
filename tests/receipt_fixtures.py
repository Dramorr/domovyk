"""Вигадані квитанції monobank для тестів (FR-PAY-3). Жодних справжніх квитанцій.

Структура тексту повторює формат 2026 р.: ті самі підписи полів у тому ж порядку.
Підпис банку — вигаданий сертифікат (ECDSA) у контейнері CMS, як у справжніх файлах.
"""

from __future__ import annotations

from html import escape

from tests.signing_fixtures import cades

FAKE_BANK = "АТ «УНІВЕРСАЛ БАНК»"
FAKE_SIGNER = "Тестовий Підписант"


def monobank_pdf(
    *,
    reference: str = "TEST-0000-AAAA-0001",
    amount: str = "18000.00",
    fee: str = "0.00",
    when: str = "04.12.2026 21:04",
    recipient_name: str = "Ірина К.",
    recipient_card: str = "4111111111111111",
) -> bytes:
    from weasyprint import HTML

    lines = [
        "АТ «УНІВЕРСАЛ БАНК»",
        "Адреса: 04080, м.Київ, вул. Тестова, 1",
        f"Квитанція № {reference} від {when.split()[0]}",
        "Відправник",
        "Ім'я Андрій Тестовий",
        "Банк Універсал Банк",
        "Платіжний інструмент UA000000000000000000000000001,",
        "411111******0000",
        "Одержувач",
        f"Ім'я {recipient_name}",
        "Банк одержувач Універсал Банк",
        f"Платіжний інструмент {recipient_card}",
        "Деталі транзакції",
        f"Сума (грн) {amount}",
        f"Комісія (грн) {fee}",
        "Призначення платежу Оренда за грудень",
        f"Дата і час операції {when}",
    ]
    html = "".join(f"<p>{escape(line)}</p>" for line in lines)
    return bytes(HTML(string=f"<html><body>{html}</body></html>").write_pdf())


def signed(pdf: bytes, organization: str = FAKE_BANK) -> bytes:
    """PDF у контейнері CMS із підписом «банку» (вигаданий сертифікат)."""
    return cades(pdf, FAKE_SIGNER, detached=False, tax_id=None, organization=organization)
