"""Текст договору з шаблону (FR-CTR-1). HTML → PDF робить адаптер `pdf`.

Шаблони версіонуються: `templates/<template_code>.html`. Зміна тексту договору — нова
версія шаблону (lease_v2), а не правка старої: підписані договори мають лишатися
відтворюваними зі знімка даних.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)

_MONTHS = (
    "січня",
    "лютого",
    "березня",
    "квітня",
    "травня",
    "червня",
    "липня",
    "серпня",
    "вересня",
    "жовтня",
    "листопада",
    "грудня",
)
_CURRENCY = {"UAH": "грн", "USD": "доларів США", "EUR": "євро"}
_PAY = {
    "card": "на банківську картку Наймодавця",
    "iban": "на поточний рахунок Наймодавця (IBAN)",
    "jar": "на рахунок Наймодавця (банка monobank)",
    "cash": "готівкою Наймодавцеві",
}
_UTILITIES = {
    "tenant_direct": (
        "Наймач самостійно й своєчасно оплачує комунальні послуги напряму постачальникам "
        "за показниками лічильників або нарахуваннями."
    ),
    "landlord": (
        "Наймодавець оплачує комунальні послуги постачальникам, а Наймач компенсує "
        "Наймодавцеві їхню вартість щомісяця разом із платою за найм."
    ),
    "included": "Вартість комунальних послуг входить у плату за найм.",
}
_WHO = {"tenant": "Наймач", "landlord": "Наймодавець"}


def date_label(value: date) -> str:
    return f"«{value.day:02d}» {_MONTHS[value.month - 1]} {value.year} р."


def months_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "місяць"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "місяці"
    return "місяців"


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def render_contract(snapshot: dict[str, Any]) -> str:
    """HTML договору з `data_snapshot`. Усе потрібне для тексту має бути у знімку."""
    terms = snapshot["terms"]
    start = date.fromisoformat(snapshot["start_date"])
    end = date.fromisoformat(snapshot["end_date"])
    apt = snapshot.get("apartment")
    address = f"м. {snapshot['city']}, {snapshot['street']}, буд. {snapshot['house_number']}"
    if apt:
        address += f", кв. {apt}"
    if snapshot.get("unit_kind") == "room":
        object_label = f"кімнату «{snapshot.get('unit_name') or ''}» у {_KIND_IN[snapshot['kind']]}"
    else:
        object_label = _KIND[snapshot["kind"]]
    context = {
        "number": snapshot["number"],
        "city": snapshot["city"],
        "date_signed_label": "дата — за останнім кваліфікованим електронним підписом",
        "landlord_name": snapshot["landlord_name"],
        "tenant_name": snapshot["tenant_name"],
        "object_label": object_label,
        "address": address,
        "area": snapshot.get("area"),
        "rooms": snapshot.get("rooms"),
        "start_label": date_label(start),
        "end_label": date_label(end),
        "months": snapshot["months"],
        "months_word": months_word(int(snapshot["months"])),
        "rent": money(int(snapshot["rent_amount"])),
        "currency_label": _CURRENCY.get(snapshot["currency"], snapshot["currency"]),
        "due_day": snapshot["rent_due_day"],
        "pay_label": _PAY[snapshot["pay_method"]],
        "deposit": money(int(snapshot["deposit_amount"]))
        if snapshot.get("deposit_amount")
        else None,
        "utilities_text": _UTILITIES[terms.get("utilities_payer", "tenant_direct")],
        "visits_notice_hours": terms.get("visits_notice_hours", 24),
        "minor_repairs_text": _WHO[terms.get("minor_repairs", "tenant")],
        "major_repairs_text": _WHO[terms.get("major_repairs", "landlord")],
        "pets_allowed": bool(terms.get("pets_allowed")),
        "residence_registration": bool(terms.get("residence_registration")),
        "sublet_allowed": bool(terms.get("sublet_allowed")),
        "early_termination_notice_days": terms.get("early_termination_notice_days", 30),
        "co_tenants": ", ".join(snapshot.get("co_tenants") or []),
        "template_code": snapshot["template_code"],
        "version": snapshot["version"],
        "generated_label": date_label(date.fromisoformat(snapshot["generated_on"])),
    }
    return _ENV.get_template(f"{snapshot['template_code']}.html").render(**context)


_KIND = {"apartment": "квартиру", "house": "житловий будинок"}
_KIND_IN = {"apartment": "квартирі", "house": "житловому будинку"}
