"""Интерфейс на Flet (0.28.x). Вся логика — в пакете habits, здесь только экраны."""
from __future__ import annotations

import os
from datetime import date, timedelta

import flet as ft

import habits_backup as backup
import habits_logic as logic
from habits_storage import Storage

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def app_dir() -> str:
    # На Android Flet задаёт приватную папку приложения в FLET_APP_STORAGE_DATA
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.join(os.path.expanduser("~"), ".habit_tracker")
    os.makedirs(base, exist_ok=True)
    return base


def main(page: ft.Page):
    page.title = "Трекер активности"
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL)

    root = app_dir()
    store = Storage(os.path.join(root, "habits.db"))
    backup_dir = os.path.join(root, "backups")
    state = {"tab": 0, "goal_id": None, "week": 0}
    body = ft.Container(expand=True, padding=16)

    def toast(msg: str):
        page.open(ft.SnackBar(ft.Text(msg)))

    def refresh():
        body.content = views[state["tab"]]()
        page.update()

    def ask(title, fields, on_ok, ok_label="Сохранить"):
        """Диалог с полями; ошибки валидации показываются в самом диалоге."""
        err = ft.Text("", color=ft.Colors.RED)

        def submit(e):
            try:
                on_ok(*[f.value for f in fields])
            except (logic.ValidationError, backup.BackupError) as ex:
                err.value = str(ex)
                page.update()
                return
            page.close(dlg)
            refresh()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text(title),
            content=ft.Column([*fields, err], tight=True),
            actions=[ft.TextButton("Отмена", on_click=lambda e: page.close(dlg)),
                     ft.FilledButton(ok_label, on_click=submit)])
        page.open(dlg)

    # ---------- экран 1: Сегодня ----------
    def add_log_dialog(g):
        val = ft.TextField(label=f"Сколько ({g.unit})", keyboard_type=ft.KeyboardType.NUMBER, autofocus=True)
        ask(f"Запись: {g.name}", [val],
            lambda v: store.add_log(g.id, v, date.today(), date.today()), "Добавить")

    def today_view():
        goals, today = store.list_goals(), date.today()
        if not goals:
            return ft.Text("Пока нет целей. Добавьте первую на вкладке «Цели».", size=16)
        cards = []
        for g in goals:
            totals = store.totals_for_goal(g.id)
            t = totals.get(today, 0.0)
            streak = logic.current_streak(totals, g.daily_target, today)
            pct = logic.progress_percent(t, g.daily_target)
            cards.append(ft.Card(content=ft.Container(padding=12, content=ft.Column([
                ft.Row([ft.Text(g.name, size=18, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(f"🔥 {streak}", size=16)]),
                ft.ProgressBar(value=pct / 100),
                ft.Row([ft.Text(f"{t:g} / {g.daily_target:g} {g.unit}  ({pct}%)", expand=True),
                        ft.FilledTonalButton("+ Добавить", on_click=lambda e, g=g: add_log_dialog(g))]),
            ]))))
        return ft.Column([ft.Text("Сегодня", size=24, weight=ft.FontWeight.BOLD), *cards],
                         scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------- экран 2: Цели ----------
    def add_goal_dialog(e=None):
        name = ft.TextField(label="Название (например, Ходьба)")
        target = ft.TextField(label="Дневная норма", keyboard_type=ft.KeyboardType.NUMBER)
        unit = ft.TextField(label="Единица (шагов, мин, км)")
        ask("Новая цель", [name, target, unit],
            lambda n, t, u: store.add_goal(n, t, u, date.today()))

    def delete_goal_dialog(g):
        def do(e):
            store.delete_goal(g.id)
            page.close(dlg)
            refresh()
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Удалить цель?"),
            content=ft.Text(f"«{g.name}» и вся история будут удалены безвозвратно."),
            actions=[ft.TextButton("Отмена", on_click=lambda e: page.close(dlg)),
                     ft.FilledButton("Удалить", on_click=do)])
        page.open(dlg)

    def goals_view():
        rows = [ft.ListTile(title=ft.Text(g.name), subtitle=ft.Text(f"{g.daily_target:g} {g.unit} в день"),
                            trailing=ft.IconButton(ft.Icons.DELETE, on_click=lambda e, g=g: delete_goal_dialog(g)))
                for g in store.list_goals()]
        return ft.Column([
            ft.Row([ft.Text("Цели", size=24, weight=ft.FontWeight.BOLD, expand=True),
                    ft.FilledButton("Добавить", icon=ft.Icons.ADD, on_click=add_goal_dialog)]),
            *(rows or [ft.Text("Список пуст")])], scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------- экран 3: Недельный отчёт ----------
    def report_view():
        goals, today = store.list_goals(), date.today()
        if not goals:
            return ft.Text("Нет целей для отчёта.", size=16)
        if state["goal_id"] not in [g.id for g in goals]:
            state["goal_id"] = goals[0].id
        g = next(x for x in goals if x.id == state["goal_id"])
        totals = store.totals_for_goal(g.id)
        r = logic.weekly_report(totals, g.daily_target, today + timedelta(weeks=state["week"]), today)

        def pick(e):
            state["goal_id"] = int(e.control.value)
            refresh()

        def shift(n):
            state["week"] = min(0, state["week"] + n)  # в будущее листать нельзя
            refresh()

        chg = "—" if r.change_percent is None else f"{r.change_percent:+g}%"
        day_rows = [ft.Row([ft.Text(DAYS[i], width=30),
                            ft.ProgressBar(value=0 if x.in_future else min(1, x.value / g.daily_target), expand=True),
                            ft.Text("✓" if x.met else ("·" if x.in_future else f"{x.value:g}"), width=50)])
                    for i, x in enumerate(r.days)]
        return ft.Column([
            ft.Text("Недельный отчёт", size=24, weight=ft.FontWeight.BOLD),
            ft.Dropdown(value=str(g.id), on_change=pick,
                        options=[ft.dropdown.Option(key=str(x.id), text=x.name) for x in goals]),
            ft.Row([ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=lambda e: shift(-1)),
                    ft.Text(f"{r.week_start:%d.%m} – {r.week_start + timedelta(days=6):%d.%m.%Y}", expand=True,
                            text_align=ft.TextAlign.CENTER),
                    ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=lambda e: shift(1))]),
            *day_rows,
            ft.Divider(),
            ft.Text(f"Выполнено дней: {r.days_met} из {r.days_counted} ({r.completion_percent}%)"),
            ft.Text(f"Всего: {r.total:g} {g.unit}, в среднем {r.average_per_day:.1f} в день"),
            ft.Text(f"Лучший день: {DAYS[r.best_day.weekday()] if r.best_day else '—'}"),
            ft.Text(f"К прошлой неделе: {chg}"),
            ft.Text(f"Лучшая серия за всё время: {logic.longest_streak(totals, g.daily_target)} дн."),
        ], scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------- экран 4: Резервные копии ----------
    def backup_view():
        files = backup.list_backups(backup_dir)
        picker = ft.Dropdown(label="Копия для восстановления",
                             options=[ft.dropdown.Option(key=p, text=os.path.basename(p)) for p in files])
        info = ft.Text(f"Папка копий: {backup_dir}", size=12, selectable=True)

        def make(e):
            try:
                p = backup.write_backup_file(store, backup_dir)
                toast(f"Копия создана: {os.path.basename(p)}")
                refresh()
            except backup.BackupError as ex:
                toast(str(ex))

        def restore(e):
            if not picker.value:
                toast("Выберите копию")
                return

            def do(ev):
                page.close(dlg)
                try:
                    backup.restore_backup(store, picker.value, backup_dir)
                    toast("Данные восстановлены")
                except backup.BackupError as ex:
                    toast(f"Ошибка: {ex}")
                refresh()

            dlg = ft.AlertDialog(
                modal=True, title=ft.Text("Восстановить данные?"),
                content=ft.Text("Текущие данные будут заменены. Перед этим создаётся страховочная копия."),
                actions=[ft.TextButton("Отмена", on_click=lambda ev: page.close(dlg)),
                         ft.FilledButton("Восстановить", on_click=do)])
            page.open(dlg)

        return ft.Column([
            ft.Text("Резервные копии", size=24, weight=ft.FontWeight.BOLD),
            ft.FilledButton("Создать копию", icon=ft.Icons.BACKUP, on_click=make),
            picker,
            ft.OutlinedButton("Восстановить", icon=ft.Icons.RESTORE, on_click=restore),
            info], scroll=ft.ScrollMode.AUTO, expand=True)

    views = [today_view, goals_view, report_view, backup_view]

    def on_nav(e):
        state["tab"] = e.control.selected_index
        refresh()

    page.navigation_bar = ft.NavigationBar(on_change=on_nav, destinations=[
        ft.NavigationBarDestination(icon=ft.Icons.TODAY, label="Сегодня"),
        ft.NavigationBarDestination(icon=ft.Icons.FLAG, label="Цели"),
        ft.NavigationBarDestination(icon=ft.Icons.BAR_CHART, label="Отчёт"),
        ft.NavigationBarDestination(icon=ft.Icons.BACKUP, label="Копии"),
    ])
    page.add(ft.SafeArea(body, expand=True))
    refresh()


if __name__ == "__main__":
    ft.app(target=main)
