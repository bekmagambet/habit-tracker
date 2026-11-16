"""Бизнес-логика трекера активности (без UI и без БД — только чистые функции).

Правила:
* цель = ежедневная норма (например, 30 минут или 8000 шагов);
* день «засчитан», если сумма записей за день >= нормы;
* серия = число засчитанных дней подряд. Серия НЕ обрывается, пока сегодняшний
  день не закончился: если сегодня норма ещё не выполнена, серия считается от вчера.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional

MAX_NAME_LEN = 40
MAX_UNIT_LEN = 15
MAX_VALUE = 100_000.0
MAX_BACKDAYS = 365
EPS = 1e-9


class ValidationError(ValueError):
    """Некорректные пользовательские данные."""


@dataclass(frozen=True)
class Goal:
    id: int
    name: str
    daily_target: float
    unit: str
    created_on: date
    archived: bool = False


@dataclass(frozen=True)
class DayResult:
    day: date
    value: float
    met: bool
    in_future: bool


@dataclass(frozen=True)
class WeeklyReport:
    week_start: date
    days: list
    total: float
    days_met: int
    days_counted: int
    completion_percent: int
    average_per_day: float
    best_day: Optional[date]
    previous_total: float
    change_percent: Optional[float]


# ---------- валидация ----------
def _to_number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{field}: ожидается число")
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            raise ValidationError(f"{field}: значение не указано")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field}: ожидается число") from None
    if not math.isfinite(num):
        raise ValidationError(f"{field}: недопустимое число")
    if num <= 0:
        raise ValidationError(f"{field}: должно быть больше нуля")
    if num > MAX_VALUE:
        raise ValidationError(f"{field}: не больше {MAX_VALUE:g}")
    return num


def validate_goal(name, target, unit):
    """Возвращает (name, target, unit) в нормализованном виде или бросает ValidationError."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("Название цели не указано")
    name = " ".join(name.split())
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"Название длиннее {MAX_NAME_LEN} символов")
    if not isinstance(unit, str) or not unit.strip():
        raise ValidationError("Единица измерения не указана")
    unit = unit.strip()
    if len(unit) > MAX_UNIT_LEN:
        raise ValidationError(f"Единица длиннее {MAX_UNIT_LEN} символов")
    return name, _to_number(target, "Норма"), unit


def validate_log_value(value) -> float:
    return _to_number(value, "Значение")


def validate_log_date(day, today: date) -> date:
    if not isinstance(day, date):
        raise ValidationError("Дата указана неверно")
    if day > today:
        raise ValidationError("Нельзя вносить записи в будущее")
    if (today - day).days > MAX_BACKDAYS:
        raise ValidationError(f"Нельзя вносить записи старше {MAX_BACKDAYS} дней")
    return day


# ---------- расчёты ----------
def sum_by_day(entries: Iterable) -> dict:
    """[(date, value), ...] -> {date: сумма}."""
    totals: dict = {}
    for day, value in entries:
        totals[day] = totals.get(day, 0.0) + float(value)
    return totals


def _check_target(target: float) -> None:
    if (isinstance(target, bool) or not isinstance(target, (int, float))
            or not math.isfinite(target) or target <= 0):
        raise ValidationError("Норма должна быть положительным числом")


def is_met(total: float, target: float) -> bool:
    _check_target(target)
    return total + EPS >= target


def progress_percent(total: float, target: float) -> int:
    """0..100; 100 только если норма реально выполнена."""
    if is_met(total, target):
        return 100
    return max(0, int(total / target * 100))


def current_streak(totals: Mapping, target: float, today: date) -> int:
    _check_target(target)
    day = today if is_met(totals.get(today, 0.0), target) else today - timedelta(days=1)
    count = 0
    while is_met(totals.get(day, 0.0), target):
        count += 1
        day -= timedelta(days=1)
    return count


def longest_streak(totals: Mapping, target: float) -> int:
    _check_target(target)
    met_days = sorted(d for d, v in totals.items() if is_met(v, target))
    best = run = 0
    prev = None
    for d in met_days:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best


def week_start(day: date) -> date:
    """Понедельник недели, в которую входит day."""
    return day - timedelta(days=day.weekday())


def weekly_report(totals: Mapping, target: float, any_day: date, today: date) -> WeeklyReport:
    _check_target(target)
    start = week_start(any_day)
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        v = totals.get(d, 0.0)
        future = d > today
        days.append(DayResult(d, v, (not future) and is_met(v, target), future))
    counted = [x for x in days if not x.in_future]
    total = sum(x.value for x in counted)
    met = sum(1 for x in counted if x.met)
    best = None
    if counted:
        top = max(counted, key=lambda x: (x.value, -x.day.toordinal()))
        best = top.day if top.value > 0 else None
    prev_start = start - timedelta(days=7)
    prev_total = sum(totals.get(prev_start + timedelta(days=i), 0.0) for i in range(7))
    change = None if prev_total <= 0 else round((total - prev_total) / prev_total * 100, 1)
    return WeeklyReport(
        week_start=start, days=days, total=total, days_met=met,
        days_counted=len(counted),
        completion_percent=round(met / len(counted) * 100) if counted else 0,
        average_per_day=total / len(counted) if counted else 0.0,
        best_day=best, previous_total=prev_total, change_percent=change,
    )
