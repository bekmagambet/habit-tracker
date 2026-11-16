"""Резервное копирование и восстановление (JSON + контрольная сумма SHA-256)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime

import habits_logic as logic
from habits_storage import Storage

APP_ID = "habit-tracker"
BACKUP_VERSION = 1


class BackupError(Exception):
    """Файл копии повреждён, не подходит или не может быть записан."""


def _checksum(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def export_data(storage: Storage, now: datetime | None = None) -> dict:
    goals, logs = storage.export_rows()
    payload = {"goals": goals, "logs": logs}
    return {"app": APP_ID, "version": BACKUP_VERSION,
            "created_at": (now or datetime.now()).isoformat(timespec="seconds"),
            "data": payload, "checksum": _checksum(payload)}


def validate_backup(data) -> tuple:
    """Полная проверка структуры. Возвращает (goals, logs) или бросает BackupError."""
    if not isinstance(data, dict) or data.get("app") != APP_ID:
        raise BackupError("Это не файл резервной копии приложения")
    if data.get("version") != BACKUP_VERSION:
        raise BackupError(f"Неподдерживаемая версия копии: {data.get('version')}")
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise BackupError("В файле нет данных")
    if data.get("checksum") != _checksum(payload):
        raise BackupError("Контрольная сумма не совпала: файл повреждён или изменён")
    goals, logs = payload.get("goals"), payload.get("logs")
    if not isinstance(goals, list) or not isinstance(logs, list):
        raise BackupError("Неверная структура данных")
    try:
        goal_ids = set()
        for g in goals:
            gid = g["id"]
            if not isinstance(gid, int) or gid <= 0 or gid in goal_ids:
                raise BackupError("Некорректные или повторяющиеся id целей")
            goal_ids.add(gid)
            logic.validate_goal(g["name"], g["daily_target"], g["unit"])
            date.fromisoformat(g["created_on"])
        log_ids = set()
        for l in logs:
            lid = l["id"]
            if not isinstance(lid, int) or lid <= 0 or lid in log_ids:
                raise BackupError("Некорректные или повторяющиеся id записей")
            log_ids.add(lid)
            if l["goal_id"] not in goal_ids:
                raise BackupError("Запись ссылается на несуществующую цель")
            logic.validate_log_value(l["value"])
            date.fromisoformat(l["log_date"])
    except BackupError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"Повреждённые данные в копии: {exc}") from None
    return goals, logs


def write_backup_file(storage: Storage, directory: str, now: datetime | None = None,
                      prefix: str = "backup", keep: int = 20) -> str:
    now = now or datetime.now()
    try:
        os.makedirs(directory, exist_ok=True)
        base = f"{prefix}_{now:%Y%m%d_%H%M%S}"
        path, n = os.path.join(directory, base + ".json"), 1
        while os.path.exists(path):
            path = os.path.join(directory, f"{base}_{n}.json")
            n += 1
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(export_data(storage, now), f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # атомарно: половинчатого файла не бывает
    except OSError as exc:
        raise BackupError(f"Не удалось записать копию: {exc}") from None
    for old in list_backups(directory)[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def list_backups(directory: str) -> list:
    """Пути к копиям, новые первыми."""
    if not os.path.isdir(directory):
        return []
    names = [n for n in os.listdir(directory) if n.endswith(".json")]
    return [os.path.join(directory, n) for n in sorted(names, reverse=True)]


def read_backup_file(path: str) -> tuple:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise BackupError("Файл не найден") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BackupError("Файл не является корректным JSON") from None
    except OSError as exc:
        raise BackupError(f"Не удалось прочитать файл: {exc}") from None
    return validate_backup(data)


def restore_backup(storage: Storage, path: str, backup_dir: str) -> None:
    """Проверяет копию, делает страховочную копию текущих данных, затем заменяет данные."""
    goals, logs = read_backup_file(path)          # 1. сначала проверка — БД не трогаем
    write_backup_file(storage, backup_dir, prefix="before_restore")  # 2. страховка
    storage.replace_all(goals, logs)              # 3. атомарная замена
