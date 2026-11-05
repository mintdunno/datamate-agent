"""Persistent, process-safe API attempt accounting and bounded retries."""

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx


class BudgetExceeded(RuntimeError):
    """An API attempt cannot be made within the configured budget."""


@dataclass(frozen=True)
class CampaignPolicy:
    campaign_id: str
    total_limit: int = 120
    phase_limits: dict[str, int] = field(
        default_factory=lambda: {"smoke": 20, "development": 60, "final": 40}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.campaign_id, str) or not self.campaign_id.strip():
            raise ValueError("Campaign ID must be a nonempty string")
        if type(self.total_limit) is not int or self.total_limit < 1:
            raise ValueError("Campaign total limit must be a positive integer")
        if (
            not isinstance(self.phase_limits, dict)
            or not self.phase_limits
            or any(
                not isinstance(phase, str)
                or not phase.strip()
                or type(limit) is not int
                or limit < 1
                for phase, limit in self.phase_limits.items()
            )
        ):
            raise ValueError("Campaign phase limits require nonempty strings and positive integers")


def _initialize_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS attempts (run_id TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_at ON attempts(at);
        CREATE INDEX IF NOT EXISTS attempts_run ON attempts(run_id);
        CREATE TABLE IF NOT EXISTS usage (
            run_id TEXT NOT NULL, at REAL NOT NULL,
            input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS response_metadata (
            run_id TEXT NOT NULL, at REAL NOT NULL, status INTEGER NOT NULL,
            finish_reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id TEXT PRIMARY KEY, total_limit INTEGER NOT NULL,
            phase_limits TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaign_runs (
            run_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, phase TEXT NOT NULL
        );
    """)
    db.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
    if "campaign_id" not in columns:
        db.execute("ALTER TABLE attempts ADD COLUMN campaign_id TEXT")
    if "phase" not in columns:
        db.execute("ALTER TABLE attempts ADD COLUMN phase TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS attempts_campaign ON attempts(campaign_id, phase)")
    db.commit()


def register_campaign(path: Path, policy: CampaignPolicy) -> None:
    """Register immutable ceilings in the same database as request attempts."""
    with sqlite3.connect(path) as db:
        _initialize_schema(db)
        db.execute("BEGIN IMMEDIATE")
        limits = json.dumps(policy.phase_limits, sort_keys=True)
        previous = db.execute(
            "SELECT total_limit, phase_limits FROM campaigns WHERE campaign_id=?",
            (policy.campaign_id,),
        ).fetchone()
        if previous is None:
            db.execute(
                "INSERT INTO campaigns VALUES (?, ?, ?)",
                (policy.campaign_id, policy.total_limit, limits),
            )
        elif previous != (policy.total_limit, limits):
            raise ValueError("campaign policy cannot change after registration")


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
        campaign_id: str | None = None,
        phase: str | None = None,
    ) -> None:
        if min(rpm, daily_limit, run_limit) < 1 or max_wait_seconds < 0:
            raise ValueError("Budget limits must be positive and wait non-negative")
        self.path = Path(path)
        self.run_id = run_id
        self.rpm = rpm
        self.daily_limit = daily_limit
        self.run_limit = run_limit
        self.max_wait_seconds = max_wait_seconds
        if (campaign_id is None) != (phase is None):
            raise ValueError("campaign_id and phase must be supplied together")
        if campaign_id is not None and (
            not isinstance(campaign_id, str)
            or not campaign_id.strip()
            or not isinstance(phase, str)
            or not phase.strip()
        ):
            raise ValueError("campaign_id and phase must be strings")
        self.campaign_id = campaign_id
        self.phase = phase
        with self._connect() as db:
            _initialize_schema(db)
            if campaign_id is not None:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT phase_limits FROM campaigns WHERE campaign_id=?", (campaign_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("campaign is not registered")
                if phase not in json.loads(row[0]):
                    raise ValueError("phase is not registered for campaign")
                previous = db.execute(
                    "SELECT campaign_id, phase FROM campaign_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if previous is None:
                    old_attempts = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id=?", (run_id,)
                    ).fetchone()[0]
                    if old_attempts:
                        raise ValueError("run already has noncampaign attempts")
                    db.execute(
                        "INSERT INTO campaign_runs VALUES (?, ?, ?)",
                        (run_id, campaign_id, phase),
                    )
                elif previous != (campaign_id, phase):
                    raise ValueError("run is bound to another campaign or phase")
            elif db.execute("SELECT 1 FROM campaign_runs WHERE run_id=?", (run_id,)).fetchone():
                raise ValueError("run is bound to a campaign")

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
                    binding = db.execute(
                        "SELECT campaign_id, phase FROM campaign_runs WHERE run_id=?",
                        (self.run_id,),
                    ).fetchone()
                    expected_binding = (
                        (self.campaign_id, self.phase) if self.campaign_id is not None else None
                    )
                    if binding != expected_binding:
                        raise ValueError("run is bound to another campaign or phase")
                    day = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE at >= ? AND at < ?",
                        (self._day_start(now), self._day_start(now) + 86400),
                    ).fetchone()[0]
                    run = db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (self.run_id,)
                    ).fetchone()[0]
                    if day >= self.daily_limit or run >= self.run_limit:
                        raise BudgetExceeded("Daily or run request limit exhausted")
                    if self.campaign_id is not None:
                        total_limit, phase_limits = db.execute(
                            "SELECT total_limit, phase_limits FROM campaigns WHERE campaign_id=?",
                            (self.campaign_id,),
                        ).fetchone()
                        phase_limit = json.loads(phase_limits)[self.phase]
                        campaign = db.execute(
                            "SELECT COUNT(*) FROM attempts WHERE campaign_id=?",
                            (self.campaign_id,),
                        ).fetchone()[0]
                        phase_count = db.execute(
                            "SELECT COUNT(*) FROM attempts WHERE campaign_id=? AND phase=?",
                            (self.campaign_id, self.phase),
                        ).fetchone()[0]
                        if campaign >= total_limit or phase_count >= phase_limit:
                            raise BudgetExceeded("Campaign or phase request limit exhausted")
                    minute = db.execute(
                        "SELECT COUNT(*), MIN(at) FROM attempts WHERE at > ?", (now - 60,)
                    ).fetchone()
                    if minute[0] < self.rpm:
                        db.execute(
                            "INSERT INTO attempts (run_id, at, campaign_id, phase) "
                            "VALUES (?, ?, ?, ?)",
                            (self.run_id, now, self.campaign_id, self.phase),
                        )
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

    def record_response(self, status: int, finish_reason: str | None) -> None:
        allowed = {"stop", "length", "tool_calls", "function_call", "content_filter"}
        finish = finish_reason if finish_reason in allowed else "not_provided"
        with self._connect() as db:
            db.execute(
                "INSERT INTO response_metadata VALUES(?,?,?,?)",
                (self.run_id, time.time(), status, finish),
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
            usage_responses = db.execute(
                "SELECT COUNT(*) FROM usage WHERE run_id=?", (self.run_id,)
            ).fetchone()[0]
            campaign_snapshot = {}
            if self.campaign_id is not None:
                total_limit, phase_limits = db.execute(
                    "SELECT total_limit, phase_limits FROM campaigns WHERE campaign_id=?",
                    (self.campaign_id,),
                ).fetchone()
                campaign_attempts = db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE campaign_id=?",
                    (self.campaign_id,),
                ).fetchone()[0]
                phase_attempts = db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE campaign_id=? AND phase=?",
                    (self.campaign_id, self.phase),
                ).fetchone()[0]
                campaign_snapshot = {
                    "campaign_id": self.campaign_id,
                    "phase": self.phase,
                    "campaign_attempts": campaign_attempts,
                    "phase_attempts": phase_attempts,
                    "campaign_remaining": max(0, total_limit - campaign_attempts),
                    "phase_remaining": max(
                        0, json.loads(phase_limits)[self.phase] - phase_attempts
                    ),
                }
            finishes = dict(
                db.execute(
                    "SELECT finish_reason,COUNT(*) FROM response_metadata "
                    "WHERE run_id=? GROUP BY finish_reason",
                    (self.run_id,),
                )
            )
        return {
            "day_attempts": day_attempts,
            "day_remaining": max(0, self.daily_limit - day_attempts),
            "run_remaining": max(0, self.run_limit - run_attempts),
            **campaign_snapshot,
            "run_attempts": run_attempts,
            "day_input_tokens": day_usage[0],
            "day_output_tokens": day_usage[1],
            "run_input_tokens": run_usage[0],
            "run_output_tokens": run_usage[1],
            "run_usage_responses": usage_responses,
            "finish_reasons": finishes,
            "response_metadata_complete": sum(finishes.values()) == run_attempts,
            "token_usage_status": "reported_complete"
            if usage_responses == run_attempts
            else "partial_or_unavailable",
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
        if budget.campaign_id is not None and max_retries > 1:
            raise ValueError("campaign permits at most one transport retry")
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
            finish = None
            try:
                data = json.loads(response.content)
                usage = data.get("usage", {})
                tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if all(type(value) is int and value >= 0 for value in tokens):
                    self.budget.record_usage(*tokens)
                choices = data.get("choices", [])
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    finish = choices[0].get("finish_reason")
            except (ValueError, AttributeError, TypeError):
                pass
            self.budget.record_response(
                response.status_code, finish if isinstance(finish, str) else None
            )
            if (
                response.status_code not in (408, 429, 500, 502, 503, 504)
                or attempt == self.max_retries
            ):
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
