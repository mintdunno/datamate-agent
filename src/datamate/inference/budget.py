"""Persistent, process-safe API attempt accounting and bounded retries."""

import asyncio
import codecs
import json
import sqlite3
import time
import zlib
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
        CREATE TABLE IF NOT EXISTS request_traces (
            run_id TEXT NOT NULL, at REAL NOT NULL, trace_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS traces_run ON request_traces(run_id, at);
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

    def record_trace(self, trace: dict) -> None:
        """Persist timing/control metadata only, never headers or model text."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO request_traces VALUES (?,?,?)",
                (self.run_id, time.time(), json.dumps(trace, allow_nan=False)),
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
            traces = db.execute(
                "SELECT trace_json FROM request_traces WHERE run_id=? "
                "ORDER BY at DESC,rowid DESC LIMIT 10",
                (self.run_id,),
            ).fetchall()
        return {
            "request_traces": [json.loads(row[0]) for row in reversed(traces)],
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


class StreamProtocolError(httpx.RemoteProtocolError):
    """Unsupported or invalid provider stream framing/content encoding."""


class RequestDeadlineExceeded(TimeoutError):
    """Absolute request deadline, distinct from socket inactivity timeouts."""


def _transport_failure(error: BaseException) -> str:
    if isinstance(error, StreamProtocolError):
        return "STREAM_PROTOCOL_ERROR"
    if isinstance(error, BudgetExceeded):
        return "BUDGET_EXCEEDED"
    if isinstance(error, RequestDeadlineExceeded):
        return "DEADLINE_EXCEEDED"
    for kind, code in (
        (httpx.ConnectTimeout, "TIMEOUT_CONNECT"),
        (httpx.ReadTimeout, "TIMEOUT_READ"),
        (httpx.WriteTimeout, "TIMEOUT_WRITE"),
        (httpx.PoolTimeout, "TIMEOUT_POOL"),
    ):
        if isinstance(error, kind):
            return code
    if isinstance(error, asyncio.CancelledError):
        return "CANCELLED"
    return "NETWORK_ERROR"


async def _bounded(operation, deadline):
    if deadline is None:
        return await operation()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RequestDeadlineExceeded("request deadline exceeded")
    try:
        async with asyncio.timeout(remaining):
            return await operation()
    except TimeoutError as error:
        raise RequestDeadlineExceeded("request deadline exceeded") from error


class _Observation:
    def __init__(self, budget, requested_stream):
        self.budget, self.started = budget, time.monotonic()
        self.sent = None
        self.status = None
        self.finish_reason = None
        self.usage = None
        self.done = False
        self.trace = {
            "stream_requested": requested_stream,
            "stream_received": False,
            "stream_done": False,
            "first_body_byte_seconds": None,
            "first_delta_seconds": None,
            "first_content_seconds": None,
            "first_tool_seconds": None,
            "first_reasoning_seconds": None,
            "headers_wait_seconds": None,
            "rate_limit_wait_seconds": None,
            "connect_seconds": None,
            "tls_seconds": None,
            "has_tool_calls": False,
            "has_content": False,
            "completed": False,
            "failure_code": None,
        }
        self.connections = {}

    async def network_event(self, name, info):
        for event, output in (("connect_tcp", "connect_seconds"), ("start_tls", "tls_seconds")):
            if name.endswith(event + ".started"):
                self.connections[event] = time.monotonic()
            elif name.endswith(event + ".complete") and event in self.connections:
                self.trace[output] = time.monotonic() - self.connections[event]

    def payload(self, data):
        if not isinstance(data, dict):
            return
        usage = data.get("usage")
        if isinstance(usage, dict):
            tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if all(type(v) is int and 0 <= v < 2**63 for v in tokens):
                self.usage = tokens
            details = usage.get("completion_tokens_details")
            reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if type(reasoning) is int and reasoning >= 0:
                self.trace["reasoning_tokens"] = reasoning
        if data.get("error"):
            self.trace["provider_stream_error"] = True
        choices = data.get("choices", [])
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish = choice.get("finish_reason")
            if finish in {"stop", "length", "tool_calls", "function_call", "content_filter"}:
                self.finish_reason = finish
            delta = choice.get("delta", choice.get("message", {}))
            if not isinstance(delta, dict):
                continue
            for delta_field, timing in (
                ("content", "first_content_seconds"),
                ("tool_calls", "first_tool_seconds"),
                ("reasoning_content", "first_reasoning_seconds"),
                ("reasoning", "first_reasoning_seconds"),
            ):
                if delta.get(delta_field):
                    elapsed = time.monotonic() - self.sent
                    if self.trace[timing] is None:
                        self.trace[timing] = elapsed
                    if self.trace["first_delta_seconds"] is None:
                        self.trace["first_delta_seconds"] = elapsed
                    if delta_field == "tool_calls":
                        self.trace["has_tool_calls"] = True
                    if delta_field == "content":
                        self.trace["has_content"] = True

    def complete(self, *, completed=False, error=None, code=None):
        if self.done:
            return
        self.done = True
        self.trace.update(
            completed=completed,
            http_status=self.status,
            finish_reason=self.finish_reason,
            elapsed_seconds=time.monotonic() - self.started,
            failure_code=_transport_failure(error) if error else code,
        )
        if self.usage is not None:
            self.budget.record_usage(*self.usage)
        if self.status is not None:
            self.budget.record_response(self.status, self.finish_reason)
        self.budget.record_trace(self.trace)


class _ObservedStream(httpx.AsyncByteStream):
    """Forward original bytes; decode only a bounded copy of each SSE event."""

    def __init__(self, inner, observation, deadline, encoding="identity"):
        self.inner, self.observation, self.deadline = inner, observation, deadline
        if encoding not in {"identity", "gzip"}:
            raise StreamProtocolError("unsupported SSE content encoding")
        self.compression = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.data = []
        self.closed = False

    def event(self):
        payload = "\n".join(self.data)
        self.data.clear()
        if payload == "[DONE]":
            self.observation.trace["stream_done"] = True
        elif payload:
            try:
                self.observation.payload(json.loads(payload))
            except (ValueError, TypeError):
                trace = self.observation.trace
                trace["malformed_events"] = trace.get("malformed_events", 0) + 1

    def consume(self, text, final=False):
        self.buffer += text
        if len(self.buffer) + sum(map(len, self.data)) > 1024 * 1024:
            raise httpx.RemoteProtocolError("SSE event exceeds bounded parser capacity")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                self.event()
            elif line.startswith("data:"):
                self.data.append(line[5:].removeprefix(" "))
        if final:
            if self.buffer.startswith("data:"):
                self.data.append(self.buffer[5:].removeprefix(" ").rstrip("\r"))
            self.buffer = ""
            self.event()

    def decode(self, chunk, *, final=False):
        if self.compression is not None:
            try:
                chunk = self.compression.decompress(chunk, 1024 * 1024 + 1)
                if (
                    self.compression.unconsumed_tail
                    or self.compression.unused_data
                    or len(chunk) > 1024 * 1024
                ):
                    raise StreamProtocolError("compressed SSE exceeds bounded decoder capacity")
                if final:
                    chunk += self.compression.flush()
                    if not self.compression.eof or len(chunk) > 1024 * 1024:
                        raise StreamProtocolError("incomplete compressed SSE body")
            except zlib.error as error:
                raise StreamProtocolError("invalid compressed SSE body") from error
        try:
            return self.decoder.decode(chunk, final=final)
        except UnicodeDecodeError as error:
            raise StreamProtocolError("invalid UTF-8 SSE body") from error

    async def __aiter__(self):
        iterator = self.inner.__aiter__()
        try:
            while True:
                try:
                    chunk = await _bounded(lambda: anext(iterator), self.deadline)
                except StopAsyncIteration:
                    break
                if chunk and self.observation.trace["first_body_byte_seconds"] is None:
                    self.observation.trace["first_body_byte_seconds"] = (
                        time.monotonic() - self.observation.sent
                    )
                self.consume(self.decode(chunk))
                yield chunk
            self.consume(self.decode(b"", final=True), final=True)
            complete = (
                self.observation.trace["stream_done"] or self.observation.finish_reason is not None
            )
            self.observation.complete(
                completed=complete, code=None if complete else "STREAM_INCOMPLETE"
            )
        except BaseException as error:
            self.observation.complete(error=error)
            raise
        finally:
            await self.aclose()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            self.observation.complete(code="STREAM_CLOSED_EARLY")
            await self.inner.aclose()


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        budget: RequestBudget,
        *,
        max_retries: int = 2,
        deadline_seconds: float | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if budget.campaign_id is not None and max_retries > 1:
            raise ValueError("campaign permits at most one transport retry")
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("request deadline must be positive")
        self.inner, self.budget, self.max_retries = inner, budget, max_retries
        self.deadline_seconds = deadline_seconds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            request_payload = json.loads(request.content)
            requested_stream = request_payload.get("stream") is True
        except (ValueError, AttributeError, TypeError):
            requested_stream = False
            request_payload = {}
        if requested_stream:
            request.headers["accept-encoding"] = "identity"
        for attempt in range(self.max_retries + 1):
            obs = _Observation(self.budget, requested_stream)
            timeouts = request.extensions.get("timeout", {})
            obs.trace["timeouts"] = {
                key: value
                for key, value in timeouts.items()
                if key in {"connect", "read", "write", "pool"}
                and (value is None or type(value) in (int, float))
            }
            choice = request_payload.get("tool_choice")
            obs.trace["tool_choice_requested"] = (
                choice
                if isinstance(choice, str) and choice in {"auto", "none", "required"}
                else "named"
                if isinstance(choice, dict)
                else "not_provided"
            )
            obs.trace["deadline_seconds"] = self.deadline_seconds
            deadline = (
                obs.started + self.deadline_seconds if self.deadline_seconds is not None else None
            )
            previous = request.extensions.get("trace")

            async def network_event(name, info, observer=obs, prior=previous):
                await observer.network_event(name, info)
                if prior is not None:
                    await prior(name, info)

            request.extensions["trace"] = network_event
            response = None
            try:
                await _bounded(self.budget.acquire, deadline)
                obs.sent = time.monotonic()
                obs.trace["rate_limit_wait_seconds"] = obs.sent - obs.started
                response = await _bounded(
                    lambda: self.inner.handle_async_request(request), deadline
                )
                obs.status = response.status_code
                obs.trace["headers_wait_seconds"] = time.monotonic() - obs.sent
                is_sse = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip()
                    == "text/event-stream"
                )
                obs.trace["stream_received"] = is_sse
                if is_sse and 200 <= response.status_code < 300:
                    encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    response.stream = _ObservedStream(
                        response.stream, obs, deadline, encoding=encoding
                    )
                    return response
                await _bounded(response.aread, deadline)
                try:
                    obs.payload(json.loads(response.content))
                except (ValueError, AttributeError, TypeError):
                    pass
                obs.complete(completed=True)
            except BaseException as error:
                obs.complete(error=error)
                if response is not None:
                    await response.aclose()
                if (
                    isinstance(error, StreamProtocolError)
                    or not isinstance(
                        error,
                        (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
                    )
                    or attempt == self.max_retries
                ):
                    raise
                await asyncio.sleep(min(2**attempt, 10))
                continue
            finally:
                if previous is None:
                    request.extensions.pop("trace", None)
                else:
                    request.extensions["trace"] = previous
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
