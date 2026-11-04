"""Persistent, process-safe API attempt accounting and bounded retries."""

import asyncio
import json
import sqlite3
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx


class BudgetExceeded(RuntimeError):
    """An API attempt cannot be made within the configured budget."""


class RequestBudget:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        rpm: int = 25,
        daily_limit: int = 1200,
        run_limit: int = 300,
        max_wait_seconds: float = 90,
    ) -> None:
        if min(rpm, daily_limit, run_limit) < 1 or max_wait_seconds < 0:
            raise ValueError("Budget limits must be positive and wait non-negative")
        self.path = Path(path)
        self.run_id = run_id
        self.rpm = rpm
        self.daily_limit = daily_limit
        self.run_limit = run_limit
        self.max_wait_seconds = max_wait_seconds
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (run_id TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_at ON attempts(at);
                CREATE INDEX IF NOT EXISTS attempts_run ON attempts(run_id);
                CREATE TABLE IF NOT EXISTS usage (
                    run_id TEXT NOT NULL, at REAL NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL
                );
            """)

    def _connect(self, *, timeout: float = 5) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=timeout)

    @staticmethod
    def _day_start(now: float) -> float:
        return (
            datetime.fromtimestamp(now, UTC)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )

    async def acquire(self) -> None:
        deadline = time.monotonic() + self.max_wait_seconds
        while True:
            now = time.time()
            try:
                with self._connect(timeout=0) as db:
                    db.execute("BEGIN IMMEDIATE")
                    day = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE at >= ? AND at < ?",
                        (self._day_start(now), self._day_start(now) + 86400),
                    ).fetchone()[0]
                    run = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (self.run_id,)
                    ).fetchone()[0]
                    if day >= self.daily_limit or run >= self.run_limit:
                        raise BudgetExceeded("Daily or run request limit exhausted")
                    minute = db.execute(
                        "SELECT COUNT(*), MIN(at) FROM attempts WHERE at > ?", (now - 60,)
                    ).fetchone()
                    if minute[0] < self.rpm:
                        db.execute("INSERT INTO attempts VALUES (?, ?)", (self.run_id, now))
                        return
                    delay = max(0.001, minute[1] + 60 - now)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                delay = 0.01
            remaining = deadline - time.monotonic()
            if remaining <= 0 or delay > remaining:
                raise BudgetExceeded("Request rate limit exceeds maximum wait")
            await asyncio.sleep(delay)

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token usage must be non-negative")
        with self._connect() as db:
            db.execute(
                "INSERT INTO usage VALUES (?, ?, ?, ?)",
                (self.run_id, time.time(), input_tokens, output_tokens),
            )

    def snapshot(self) -> dict:
        now = time.time()
        start = self._day_start(now)
        with self._connect() as db:
            day_attempts = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE at >= ? AND at < ?", (start, start + 86400)
            ).fetchone()[0]
            run_attempts = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (self.run_id,)
            ).fetchone()[0]
            day_usage = db.execute(
                "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) "
                "FROM usage WHERE at >= ? AND at < ?",
                (start, start + 86400),
            ).fetchone()
            run_usage = db.execute(
                "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) "
                "FROM usage WHERE run_id = ?",
                (self.run_id,),
            ).fetchone()
        return {
            "day_attempts": day_attempts,
            "run_attempts": run_attempts,
            "day_input_tokens": day_usage[0],
            "day_output_tokens": day_usage[1],
            "run_input_tokens": run_usage[0],
            "run_output_tokens": run_usage[1],
        }


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return max(0.0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return None


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, inner: httpx.AsyncBaseTransport, budget: RequestBudget, *, max_retries: int = 2
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.inner = inner
        self.budget = budget
        self.max_retries = max_retries

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            await self.budget.acquire()
            try:
                response = await self.inner.handle_async_request(request)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 10))
                continue
            await response.aread()
            try:
                usage = json.loads(response.content).get("usage", {})
                tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if all(type(value) is int and value >= 0 for value in tokens):
                    self.budget.record_usage(*tokens)
            except (ValueError, AttributeError, TypeError):
                pass
            if response.status_code not in (429, 502, 503, 504) or attempt == self.max_retries:
                return response
            delay = _retry_after(response.headers.get("Retry-After"))
            if delay is None:
                delay = min(2**attempt, 10)
            if delay > self.budget.max_wait_seconds:
                return response
            await response.aclose()
            await asyncio.sleep(delay)
        raise AssertionError("Unreachable")

    async def aclose(self) -> None:
        await self.inner.aclose()
