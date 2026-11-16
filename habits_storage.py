"""Хранилище SQLite. Только локально, без сети."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import habits_logic as logic
from habits_logic import Goal, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    daily_target REAL NOT NULL CHECK(daily_target > 0),
    unit TEXT NOT NULL,
    created_on TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    log_date TEXT NOT NULL,
    value REAL NOT NULL CHECK(value > 0),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_goal_date ON activity_logs(goal_id, log_date);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # --- цели ---
    @staticmethod
    def _goal(r) -> Goal:
        return Goal(r["id"], r["name"], r["daily_target"], r["unit"],
                    date.fromisoformat(r["created_on"]), bool(r["archived"]))

    def add_goal(self, name, target, unit, today: date) -> int:
        name, target, unit = logic.validate_goal(name, target, unit)
        # сравнение в Python: SQLite lower() не понимает кириллицу
        if any(g.name.casefold() == name.casefold() for g in self.list_goals()):
            raise ValidationError("Цель с таким названием уже существует")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO goals(name, daily_target, unit, created_on) VALUES(?,?,?,?)",
                (name, target, unit, today.isoformat()))
        return cur.lastrowid

    def list_goals(self, include_archived: bool = False) -> list:
        q = "SELECT * FROM goals" + ("" if include_archived else " WHERE archived=0") + " ORDER BY id"
        return [self._goal(r) for r in self.conn.execute(q)]

    def get_goal(self, goal_id: int):
        r = self.conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return self._goal(r) if r else None

    def delete_goal(self, goal_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))

    # --- записи ---
    def add_log(self, goal_id: int, value, day: date, today: date, note: str = "") -> int:
        if self.get_goal(goal_id) is None:
            raise ValidationError("Цель не найдена")
        value = logic.validate_log_value(value)
        day = logic.validate_log_date(day, today)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO activity_logs(goal_id, log_date, value, note, created_at) VALUES(?,?,?,?,?)",
                (goal_id, day.isoformat(), value, (note or "")[:200], datetime.now().isoformat()))
        return cur.lastrowid

    def totals_for_goal(self, goal_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT log_date, SUM(value) s FROM activity_logs WHERE goal_id=? GROUP BY log_date",
            (goal_id,))
        return {date.fromisoformat(r["log_date"]): r["s"] for r in rows}

    # --- настройки ---
    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_setting(self, key: str, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    # --- для резервных копий ---
    def export_rows(self):
        goals = [dict(r) for r in self.conn.execute("SELECT * FROM goals ORDER BY id")]
        logs = [dict(r) for r in self.conn.execute(
            "SELECT id, goal_id, log_date, value, note, created_at FROM activity_logs ORDER BY id")]
        return goals, logs

    def replace_all(self, goals: list, logs: list) -> None:
        """Атомарная замена всех данных: при любой ошибке всё откатывается."""
        with self.conn:
            self.conn.execute("DELETE FROM activity_logs")
            self.conn.execute("DELETE FROM goals")
            self.conn.executemany(
                "INSERT INTO goals(id,name,daily_target,unit,created_on,archived) VALUES(?,?,?,?,?,?)",
                [(g["id"], g["name"], g["daily_target"], g["unit"], g["created_on"],
                  int(g.get("archived", 0))) for g in goals])
            self.conn.executemany(
                "INSERT INTO activity_logs(id,goal_id,log_date,value,note,created_at) VALUES(?,?,?,?,?,?)",
                [(l["id"], l["goal_id"], l["log_date"], l["value"], l.get("note", ""),
                  l.get("created_at", "")) for l in logs])
