"""Анкета об'єкта та рівень її заповненості (FR-PRP-8, ADR 0003). Чиста логіка без I/O.

Поля зібрано з фільтрів ЛУН, DIM.RIA й OLX (листопад 2026) і доповнено тим, що
на тих платформах власники пишуть лише в описі: комуналка взимку й улітку,
що працює під час блекауту, лічильники. Версія анкети — `PROFILE_VERSION`:
анкета ще не фінальна, поля додаватимуться.

Чому це відсіює ріелторів: посередник із десятками об'єктів не знає (і не хоче
заповнювати) деталей кожної квартири — скільки платили за КП узимку, чи є
окремий лічильник тепла, що працює при відключеннях. Власник знає.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

PROFILE_VERSION = 1


# --------------------------------------------------------------- будинок


class BuildingType(StrEnum):
    """Тип будинку, як його називають у Львові."""

    AUSTRIAN_POLISH = "austrian_polish"  # «австрійський» / «польський» (до 1939)
    STALINKA = "stalinka"
    KHRUSHCHOVKA = "khrushchovka"
    CZECH = "czech"  # «чешка»
    SOVIET_PANEL = "soviet_panel"  # «совмін», серійні панельні 70–90-х
    GOSTYNKA = "gostynka"
    NEW_BUILD = "new_build"
    PRIVATE_HOUSE = "private_house"
    OTHER = "other"


class WallMaterial(StrEnum):
    BRICK = "brick"
    PANEL = "panel"
    MONOLITH_FRAME = "monolith_frame"
    BLOCK = "block"
    WOOD = "wood"
    OTHER = "other"


class Parking(StrEnum):
    NONE = "none"
    STREET = "street"
    YARD = "yard"  # у дворі / на закритій території
    UNDERGROUND = "underground"
    GARAGE = "garage"


class Shelter(StrEnum):
    """Укриття (як на ЛУН): у будинку чи поруч."""

    NONE = "none"
    BASEMENT = "basement"
    UNDERGROUND_PARKING = "underground_parking"
    DEDICATED = "dedicated"  # спеціалізоване укриття в будинку / ЖК
    NEARBY = "nearby"  # громадське укриття поруч (до ~5 хв)


# --------------------------------------------------------- квартира, стан


class Layout(StrEnum):
    SEPARATE = "separate"  # усі кімнати ізольовані
    ADJACENT = "adjacent"  # суміжні / прохідні
    MIXED = "mixed"  # суміжно-роздільне
    STUDIO = "studio"
    KITCHEN_STUDIO = "kitchen_studio"  # кухня-вітальня + ізольовані кімнати
    MULTILEVEL = "multilevel"


class BathroomType(StrEnum):
    SEPARATE = "separate"
    COMBINED = "combined"
    SEVERAL = "several"


class Repair(StrEnum):
    """Стан (як на OLX/DIM.RIA)."""

    DESIGNER = "designer"
    EURO = "euro"
    COSMETIC = "cosmetic"
    LIVABLE = "livable"  # житловий стан
    NEEDS_REPAIR = "needs_repair"


class Furnishing(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class WindowsView(StrEnum):
    YARD = "yard"
    STREET = "street"
    BOTH = "both"


# ------------------------------------------------------ інженерія й комуналка


class Heating(StrEnum):
    CENTRAL = "central"  # місто (Львівтеплоенерго)
    BUILDING_BOILER = "building_boiler"  # котельня будинку / ЖК («автономне»)
    INDIVIDUAL_GAS = "individual_gas"  # свій газовий котел
    INDIVIDUAL_ELECTRIC = "individual_electric"
    OTHER = "other"


class HotWater(StrEnum):
    CENTRAL = "central"
    HEATING_BOILER = "heating_boiler"  # двоконтурний котел
    ELECTRIC_BOILER = "electric_boiler"
    GAS_HEATER = "gas_heater"  # газова колонка


class Stove(StrEnum):
    GAS = "gas"
    ELECTRIC = "electric"
    INDUCTION = "induction"


class Internet(StrEnum):
    NONE = "none"
    CABLE = "cable"
    FIBER = "fiber"  # оптика (працює довше при блекауті)
    MOBILE = "mobile"


class ApartmentBackupPower(StrEnum):
    """Резервне живлення самої квартири (ЛУН: «резервне живлення квартири»)."""

    NONE = "none"
    POWER_STATION = "power_station"  # EcoFlow тощо
    INVERTER = "inverter"  # інвертор з акумулятором, підключений до мережі квартири
    GENERATOR = "generator"


# ---------------------------------------------------------------- правила


class PetsPolicy(StrEnum):
    """Детальніше за «так/ні» (як на OLX): власник одразу каже, кого можна."""

    NO = "no"
    CATS = "cats"
    SMALL_DOGS = "small_dogs"  # до 10 кг
    ANY = "any"
    DISCUSS = "discuss"


class ChildrenPolicy(StrEnum):
    YES = "yes"
    NO = "no"
    DISCUSS = "discuss"


class SmokingPolicy(StrEnum):
    NO = "no"
    BALCONY = "balcony"
    YES = "yes"


# --------------------------------------------------- переліки (множинний вибір)


class Appliance(StrEnum):
    FRIDGE = "fridge"
    WASHING_MACHINE = "washing_machine"
    DRYER = "dryer"
    DISHWASHER = "dishwasher"
    OVEN = "oven"
    MICROWAVE = "microwave"
    HOB = "hob"  # варильна поверхня
    HOOD = "hood"
    KETTLE = "kettle"
    COFFEE_MACHINE = "coffee_machine"
    TV = "tv"
    VACUUM = "vacuum"
    IRON = "iron"


class Comfort(StrEnum):
    AIR_CONDITIONER = "air_conditioner"
    FLOOR_HEATING = "floor_heating"
    WATER_FILTER = "water_filter"
    BATHTUB = "bathtub"
    SHOWER = "shower"
    WARDROBE_ROOM = "wardrobe_room"  # гардеробна
    STORAGE_ROOM = "storage_room"  # комора
    WORKSPACE = "workspace"  # робоче місце
    SECURITY_DOOR = "security_door"  # броньовані двері
    ALARM = "alarm"  # сигналізація


class BuildingFeature(StrEnum):
    CLOSED_TERRITORY = "closed_territory"
    VIDEO_SURVEILLANCE = "video_surveillance"
    CONCIERGE = "concierge"
    PLAYGROUND = "playground"
    BICYCLE_STORAGE = "bicycle_storage"
    INTERCOM = "intercom"


class BlackoutReady(StrEnum):
    """Що в будинку працює без світла (ЛУН/OLX «автономність при блекауті»)."""

    ELEVATOR = "elevator"
    WATER = "water"
    HEATING = "heating"
    INTERNET = "internet"
    LIGHTING = "lighting"  # світло в під'їзді / укритті


class Accessibility(StrEnum):
    """Безбар'єрність (DIM.RIA)."""

    STEP_FREE_ENTRANCE = "step_free_entrance"
    WIDE_DOORS = "wide_doors"  # двері ширше 90 см
    STEP_FREE_SHOWER = "step_free_shower"
    GRAB_BARS = "grab_bars"
    STEP_FREE_SHELTER = "step_free_shelter"


class Meter(StrEnum):
    """Окремі лічильники квартири — потрібні для комуналки (M5)."""

    ELECTRICITY = "electricity"
    WATER = "water"
    GAS = "gas"
    HEAT = "heat"


# ------------------------------------------------------ рівень заповненості


class Section(StrEnum):
    BASICS = "basics"
    BUILDING = "building"
    APARTMENT = "apartment"
    UTILITIES = "utilities"
    BLACKOUT = "blackout"
    EQUIPMENT = "equipment"
    RULES = "rules"


@dataclass(frozen=True)
class FieldWeight:
    key: str
    section: Section
    weight: int
    important: bool = False  # «важливе питання»: показуємо першим у списку незаповненого


# Вага = наскільки відповідь допомагає орендарю вирішити й наскільки її знає лише
# власник. Важливі питання — ті, про які орендарі питають першими і які ріелтор
# зазвичай не знає. Базові поля (кімнати, площа, поверх) — з картки об'єкта.
FIELDS: tuple[FieldWeight, ...] = (
    # базове
    FieldWeight("rooms", Section.BASICS, 2),
    FieldWeight("area_total", Section.BASICS, 3, important=True),
    FieldWeight("floor", Section.BASICS, 2),
    FieldWeight("description", Section.BASICS, 3),
    # будинок
    FieldWeight("building_type", Section.BUILDING, 2),
    FieldWeight("wall_material", Section.BUILDING, 1),
    FieldWeight("year_built", Section.BUILDING, 1),
    FieldWeight("floors_total", Section.BUILDING, 2),
    FieldWeight("has_elevator", Section.BUILDING, 2),
    FieldWeight("parking", Section.BUILDING, 1),
    FieldWeight("shelter", Section.BUILDING, 4, important=True),
    FieldWeight("building_features", Section.BUILDING, 1),
    # квартира
    FieldWeight("area_living", Section.APARTMENT, 1),
    FieldWeight("area_kitchen", Section.APARTMENT, 1),
    FieldWeight("layout", Section.APARTMENT, 2),
    FieldWeight("bathroom", Section.APARTMENT, 2),
    FieldWeight("ceiling_height", Section.APARTMENT, 1),
    FieldWeight("balconies", Section.APARTMENT, 1),
    FieldWeight("windows_view", Section.APARTMENT, 1),
    FieldWeight("repair", Section.APARTMENT, 3, important=True),
    FieldWeight("first_rental", Section.APARTMENT, 1),
    # комуналка
    FieldWeight("heating", Section.UTILITIES, 4, important=True),
    FieldWeight("hot_water", Section.UTILITIES, 2),
    FieldWeight("stove", Section.UTILITIES, 2),
    FieldWeight("internet", Section.UTILITIES, 2),
    FieldWeight("meters", Section.UTILITIES, 3, important=True),
    FieldWeight("utilities_winter_uah", Section.UTILITIES, 4, important=True),
    FieldWeight("utilities_summer_uah", Section.UTILITIES, 3, important=True),
    # блекаут
    FieldWeight("apartment_backup_power", Section.BLACKOUT, 3, important=True),
    FieldWeight("blackout_ready", Section.BLACKOUT, 3, important=True),
    # обладнання
    FieldWeight("furnishing", Section.EQUIPMENT, 3, important=True),
    FieldWeight("appliances", Section.EQUIPMENT, 3),
    FieldWeight("comfort", Section.EQUIPMENT, 2),
    FieldWeight("accessibility", Section.EQUIPMENT, 1),
    # правила
    FieldWeight("pets", Section.RULES, 3, important=True),
    FieldWeight("children", Section.RULES, 2, important=True),
    FieldWeight("smoking", Section.RULES, 1),
    FieldWeight("max_occupants", Section.RULES, 2),
    FieldWeight("residence_registration", Section.RULES, 2, important=True),
)
FIELD_KEYS = frozenset(f.key for f in FIELDS)
DESCRIPTION_MIN = 80  # опис коротший — не рахується: «здам квартиру» нічого не каже


class CompletenessLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Completeness:
    percent: int
    level: CompletenessLevel
    answered: int
    total: int
    missing_important: tuple[str, ...]
    sections: dict[str, int]  # відсоток по розділах


def is_answered(key: str, value: object) -> bool:
    """None — «не відповів». False, 0 і порожній перелік — це відповідь («ні»/«нічого»)."""
    if value is None:
        return False
    if key == "description":
        return isinstance(value, str) and len(value.strip()) >= DESCRIPTION_MIN
    return not (isinstance(value, str) and not value.strip())


def completeness(
    answers: Mapping[str, object], *, medium_from: int, high_from: int
) -> Completeness:
    """Рівень заповненості анкети. Пороги рівнів — з конфігурації (не з коду)."""
    total_weight = sum(f.weight for f in FIELDS)
    got = 0
    answered = 0
    missing: list[str] = []
    section_total: dict[str, int] = {}
    section_got: dict[str, int] = {}
    for f in FIELDS:
        section_total[f.section] = section_total.get(f.section, 0) + f.weight
        if is_answered(f.key, answers.get(f.key)):
            got += f.weight
            answered += 1
            section_got[f.section] = section_got.get(f.section, 0) + f.weight
        elif f.important:
            missing.append(f.key)
    percent = round(100 * got / total_weight)
    if percent >= high_from:
        level = CompletenessLevel.HIGH
    elif percent >= medium_from:
        level = CompletenessLevel.MEDIUM
    else:
        level = CompletenessLevel.LOW
    sections = {s: round(100 * section_got.get(s, 0) / w) for s, w in section_total.items()}
    return Completeness(percent, level, answered, len(FIELDS), tuple(missing), sections)
