"""Telegram bot: on-demand reports via commands, scheduled daily/monthly reports, arrival notices."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from functools import wraps
from html import escape

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from ..config import Config
from ..domain.events import Event
from ..domain.history import HISTORY_USAGE, HistoryArgsError, default_period, find_employees, parse_history_args
from ..domain.worktime import workday_of
from ..hikvision import Device
from ..reports import daily_report, employee_report, monthly_report
from ..services.collector import Collector
from ..storage import Database

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Splits on line breaks so long reports fit Telegram's message size limit."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


# Inline-button payload for the employee history: "emp:<employee_no>:<first>:<last>" (max 64 bytes).
EMPLOYEE_CALLBACK = "emp"


def employee_keyboard(people: list[tuple[str, str]], first: date, last: date) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(name or f"#{no}", callback_data=f"{EMPLOYEE_CALLBACK}:{no}:{first}:{last}")
        for no, name in people
    ]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def previous_month(day: date) -> tuple[int, int]:
    prev = day.replace(day=1) - timedelta(days=1)
    return prev.year, prev.month


def build_application(config: Config, db: Database, device: Device) -> Application:
    assert config.telegram_token
    collector = Collector(device, db, config)

    def today() -> date:
        return workday_of(datetime.now(config.tz), config.tz, config.day_cutoff)

    async def send_html(chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            await app.bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)

    async def send_month(chat_id: int, year: int, month: int, until: date | None = None) -> None:
        text, xlsx = monthly_report(db, config, year, month, until=until)
        await send_html(chat_id, text)
        await app.bot.send_document(chat_id, document=xlsx, filename=f"attendance-{year}-{month:02d}.xlsx")

    async def broadcast(send: Callable[[int], Awaitable[None]]) -> None:
        if not config.report_chat_ids:
            log.warning("no TELEGRAM_REPORT_CHAT_IDS configured; scheduled report not sent")
        for chat_id in config.report_chat_ids:
            try:
                await send(chat_id)
            except Exception as exc:
                log.warning("sending report to chat %s failed: %s", chat_id, exc)

    # --- access control -------------------------------------------------------

    def is_allowed(update: Update) -> bool:
        user, chat = update.effective_user, update.effective_chat
        return bool((user and user.id in config.admin_ids) or (chat and chat.id in config.report_chat_ids))

    def restricted(handler: Handler) -> Handler:
        @wraps(handler)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not is_allowed(update):
                await update.effective_message.reply_text("Not authorized. Send /start to see your ids.")
                return
            await handler(update, context)

        return wrapper

    # --- commands -------------------------------------------------------------

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, chat = update.effective_user, update.effective_chat
        status = "✅ authorized" if is_allowed(update) else (
            "⛔ not authorized — add the chat id to TELEGRAM_REPORT_CHAT_IDS or your user id to TELEGRAM_ADMIN_IDS"
        )
        await update.effective_message.reply_text(
            f"Chat id: <code>{chat.id}</code>\nUser id: <code>{user.id if user else '—'}</code>\n{status}\n\n"
            "/today — today's attendance\n/yesterday — yesterday's attendance\n"
            "/day YYYY-MM-DD — a specific day\n/month [YYYY-MM] — monthly summary + Excel\n"
            "/employee NAME [DAYS | FROM [TO]] — one employee's history (default: 7 days)\n"
            "/status — terminal connection status",
            parse_mode=ParseMode.HTML,
        )

    @restricted
    async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await send_html(update.effective_chat.id, daily_report(db, config, today()))

    @restricted
    async def cmd_yesterday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await send_html(update.effective_chat.id, daily_report(db, config, today() - timedelta(days=1)))

    @restricted
    async def cmd_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            day = date.fromisoformat(context.args[0])
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Usage: /day YYYY-MM-DD")
            return
        await send_html(update.effective_chat.id, daily_report(db, config, day))

    @restricted
    async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current = today()
        try:
            year, month = (map(int, context.args[0].split("-"))) if context.args else (current.year, current.month)
            date(year, month, 1)
        except ValueError:
            await update.effective_message.reply_text("Usage: /month YYYY-MM")
            return
        until = current if (year, month) == (current.year, current.month) else None
        await send_month(update.effective_chat.id, year, month, until)

    async def send_employee(chat_id: int, employee_no: str, first: date, last: date) -> None:
        name = db.employees(active_only=False).get(employee_no, "")
        await send_html(chat_id, employee_report(db, config, employee_no, name, first, last))

    @restricted
    async def cmd_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not context.args:
            people = sorted(db.employees().items(), key=lambda p: (p[1].casefold(), p[0]))
            if not people:
                await message.reply_text("No employees synced from the terminal yet.")
                return
            first, last = default_period(today())
            await message.reply_text("Choose an employee (last 7 days):",
                                     reply_markup=employee_keyboard(people, first, last))
            return
        try:
            query, first, last = parse_history_args(context.args, today())
        except HistoryArgsError as exc:
            await message.reply_text(f"{exc}\n\n{HISTORY_USAGE}")
            return

        matches = find_employees(db.employees(active_only=False), query)
        if not matches:
            await message.reply_text(f'No employee matches "{query}".')
        elif len(matches) == 1:
            await send_employee(update.effective_chat.id, matches[0][0], first, last)
        else:
            await message.reply_text(f'Several employees match "{query}":',
                                     reply_markup=employee_keyboard(matches, first, last))

    @restricted
    async def on_employee_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        _, employee_no, first, last = query.data.rsplit(":", 3)
        await send_employee(update.effective_chat.id, employee_no,
                            date.fromisoformat(first), date.fromisoformat(last))

    @restricted
    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        def ago(moment: datetime | None) -> str:
            return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else "never"

        lines = [
            f"Terminal: <code>{escape(config.device_url)}</code>",
            f"Live stream: {'🟢 connected' if collector.stream_connected else '🔴 disconnected'}",
            f"Last stream message: {ago(collector.last_stream_message)}",
            f"Last log sync: {ago(collector.last_reconcile)}",
            f"Employees: {len(db.employees())}",
        ]
        if collector.last_error:
            lines.append(f"Last error: {escape(collector.last_error)}")
        await send_html(update.effective_chat.id, "\n".join(lines))

    # --- scheduled jobs -------------------------------------------------------

    async def job_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
        day = today() - timedelta(days=1 if config.daily_report_for == "yesterday" else 0)
        text = daily_report(db, config, day)
        await broadcast(lambda chat_id: send_html(chat_id, text))

    async def job_monthly(context: ContextTypes.DEFAULT_TYPE) -> None:
        year, month = previous_month(today())
        await broadcast(lambda chat_id: send_month(chat_id, year, month))

    async def notify_arrival(event: Event) -> None:
        text = (f"🟢 <b>{escape(event.name or '#' + event.employee_no)}</b> arrived at "
                f"{event.time.astimezone(config.tz):%H:%M}")
        await broadcast(lambda chat_id: send_html(chat_id, text))

    if config.notify_arrivals:
        collector.on_arrival = notify_arrival

    # --- lifecycle ------------------------------------------------------------

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("today", "Today's attendance"),
            BotCommand("yesterday", "Yesterday's attendance"),
            BotCommand("day", "Attendance for YYYY-MM-DD"),
            BotCommand("month", "Monthly summary + Excel (YYYY-MM)"),
            BotCommand("employee", "One employee's history (default: 7 days)"),
            BotCommand("status", "Terminal connection status"),
        ])
        task = asyncio.create_task(collector.run(), name="collector")
        task.add_done_callback(
            lambda t: t.cancelled() or t.exception() is None or log.error("collector stopped: %r", t.exception())
        )
        application.bot_data["collector_task"] = task

    async def post_shutdown(application: Application) -> None:
        task = application.bot_data.get("collector_task")
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await device.aclose()

    app = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    for name, handler in {
        "start": cmd_start, "help": cmd_start, "today": cmd_today, "yesterday": cmd_yesterday,
        "day": cmd_day, "month": cmd_month, "status": cmd_status,
        "employee": cmd_employee, "history": cmd_employee,
    }.items():
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(on_employee_button, pattern=f"^{EMPLOYEE_CALLBACK}:"))

    app.job_queue.run_daily(job_daily, time=config.daily_report_time.replace(tzinfo=config.tz), name="daily-report")
    app.job_queue.run_monthly(job_monthly, when=config.monthly_report_time.replace(tzinfo=config.tz), day=1,
                              name="monthly-report")
    return app
