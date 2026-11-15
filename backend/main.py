"""FastAPI telemetry server with SSE streaming."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from enum import Enum
from pathlib import Path
from typing import Any, Final

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator

from backend.comms import DelayedLink, load_delay_bounds_s
from backend.models import CONTRACT_VERSION, Scenario, TelemetryFrame
from backend.reference import build_limits, build_sep_event
from backend.rules import RulesEngine, SepContext
from backend.simulator import SIMULATION_EPOCH, SimulationFinished, Simulator

DEFAULT_SCENARIO_NAME: Final[str] = "nominal"
SCENARIO_ORDER: Final[tuple[Scenario, ...]] = tuple(Scenario)
DEFAULT_TIME_SCALE: Final[float] = 600.0
MAX_TIME_SCALE: Final[float] = 6000.0
MIN_TIME_SCALE: Final[float] = 0.01
RELEASE_AFTER_ABSENT_FRAMES: Final[int] = 12
SUBSCRIBER_QUEUE_MAXSIZE: Final[int] = 100
KEEPALIVE_INTERVAL_S: Final[float] = 15.0
SECONDS_PER_MINUTE: Final[float] = 60.0
HTTP_NOT_FOUND: Final[int] = 404
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_SERVICE_UNAVAILABLE: Final[int] = 503
DEMO_KEY_ENV: Final[str] = "DEMO_CONTROL_KEY"
DEMO_KEY_HEADER: Final[str] = "X-Demo-Key"
FINISH_RESTART_DELAY_S: Final[float] = 30.0
HISTORY_MAXLEN: Final[int] = 400
DEFAULT_HISTORY_LIMIT: Final[int] = 120
MIN_HISTORY_LIMIT: Final[int] = 1

logger: Final = logging.getLogger(__name__)

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"
RANGES_PATH: Final[Path] = DATA_DIR / "reference" / "ranges.json"
SCENARIOS_DIR: Final[Path] = DATA_DIR / "scenarios"
SEP_EVENT_PATH: Final[Path] = DATA_DIR / "donki" / "sep_event.json"
FRONTEND_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "frontend"


def validate_time_scale(scale: float) -> float:
    if not math.isfinite(scale) or not MIN_TIME_SCALE <= scale <= MAX_TIME_SCALE:
        raise ValueError(f"time_scale must be finite and within {MIN_TIME_SCALE}-{MAX_TIME_SCALE}")
    return scale


def load_default_time_scale(ranges_path: Path = RANGES_PATH) -> float:
    env_scale = os.getenv("MARS_TIME_SCALE")
    if env_scale is not None:
        try:
            return validate_time_scale(float(env_scale))
        except ValueError:
            pass
    try:
        data = json.loads(ranges_path.read_text(encoding="utf-8"))
        return validate_time_scale(float(data["iletisim"]["demo_zaman_carpani"]["deger"]))
    except (KeyError, OSError, TypeError, ValueError):
        return DEFAULT_TIME_SCALE


class Pipeline:
    def __init__(
        self,
        scenario: Scenario | str = Scenario.nominal,
        *,
        ranges_path: Path = RANGES_PATH,
        scenarios_dir: Path = SCENARIOS_DIR,
    ) -> None:
        self.ranges_path = ranges_path
        self.scenarios_dir = scenarios_dir
        self.scenario = Scenario(scenario)
        self.sim = Simulator(self.scenario, scenario_dir=self.scenarios_dir, ranges_path=self.ranges_path)
        self.rules = RulesEngine(self.ranges_path)
        bounds = load_delay_bounds_s(self.ranges_path)
        self.link = DelayedLink(delay_bounds_s=bounds)
        self._active_keys: set[tuple[str, ...]] = set()
        self._absent_frames: dict[tuple[str, ...], int] = {}

    @property
    def measurement_interval_min(self) -> float:
        return self.sim._spec.model.measurement_interval_min

    @property
    def finished(self) -> bool:
        return self.sim.finished

    def reset(self, scenario: Scenario | str) -> None:
        target_scenario = Scenario(scenario)
        new_sim = Simulator(target_scenario, scenario_dir=self.scenarios_dir, ranges_path=self.ranges_path)
        new_rules = RulesEngine(self.ranges_path)
        bounds = load_delay_bounds_s(self.ranges_path)
        new_link = DelayedLink(delay_bounds_s=bounds)

        self.scenario = target_scenario
        self.sim = new_sim
        self.rules = new_rules
        self.link = new_link
        self._active_keys.clear()
        self._absent_frames.clear()

    def step(self) -> TelemetryFrame:
        frame = self.sim.tick()

        sep = self.sim.sep_state
        sep_context = (
            SepContext(
                sep_active=True,
                event_dose_rate_msv_per_h=sep.event_dose_rate_msv_per_h,
                remaining_event_h=sep.remaining_event_h,
            )
            if sep.active
            else None
        )
        frame = self.rules.evaluate(frame, sep=sep_context)

        current_keys: set[tuple[str, ...]] = set()
        for alert in frame.alerts:
            severity_str = alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)
            key = (alert.crew_id or "cabin", alert.rule_id, severity_str)
            current_keys.add(key)
            if key not in self._active_keys:
                self.link.send(key, alert.message, frame.mission_elapsed_s)
            self._absent_frames.pop(key, None)

        current_rules = {key[:2] for key in current_keys}
        for key in self._active_keys - current_keys:
            absent = self._absent_frames.get(key, 0) + 1
            if key[:2] in current_rules or absent >= RELEASE_AFTER_ABSENT_FRAMES:
                self.link.release(key)
                self._active_keys.remove(key)
                self._absent_frames.pop(key, None)
            else:
                self._absent_frames[key] = absent

        self._active_keys.update(current_keys)
        self.link.update(frame.mission_elapsed_s)

        return frame.model_copy(
            update={
                "messages": self.link.contract_messages(SIMULATION_EPOCH, frame.mission_elapsed_s),
                "comms_one_way_delay_s": self.link.one_way_delay_s,
            }
        )


class SimulationManager:
    def __init__(
        self,
        scenario: Scenario | str = DEFAULT_SCENARIO_NAME,
        *,
        ranges_path: Path = RANGES_PATH,
        scenarios_dir: Path = SCENARIOS_DIR,
    ) -> None:
        self.ranges_path = ranges_path
        self.scenarios_dir = scenarios_dir
        self.pipeline = Pipeline(
            scenario=scenario,
            ranges_path=self.ranges_path,
            scenarios_dir=self.scenarios_dir,
        )
        self.time_scale: float = load_default_time_scale(self.ranges_path)
        self.finish_restart_delay_s: float | None = FINISH_RESTART_DELAY_S
        self.running: bool = True
        self.finished: bool = False
        self.last_frame: TelemetryFrame | None = None
        self.history: deque[TelemetryFrame] = deque(maxlen=HISTORY_MAXLEN)
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._unpaused_event: asyncio.Event = asyncio.Event()
        self._unpaused_event.set()
        self._interrupt_event: asyncio.Event = asyncio.Event()
        self._reset_requested: bool = False
        self._sim_elapsed: float = 0.0
        self._shutdown: bool = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown = False
        self._unpaused_event = asyncio.Event()
        if self.running and not self.finished:
            self._unpaused_event.set()
        self._interrupt_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._shutdown = True
        self._unpaused_event.set()
        self._interrupt_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._subscribers.clear()

    def pause(self) -> None:
        self.running = False
        self._unpaused_event.clear()
        self._interrupt_event.set()

    def resume(self) -> None:
        if not self.finished:
            self.running = True
            self._unpaused_event.set()
            self._interrupt_event.set()

    def restart(self) -> None:
        self.pipeline.reset(self.pipeline.scenario)
        self.last_frame = None
        self.history.clear()
        self.finished = False
        self.running = True
        self._reset_requested = True
        self._unpaused_event.set()
        self._interrupt_event.set()

    def set_scenario(self, scenario_name: str) -> None:
        self.pipeline.reset(scenario_name)
        self.last_frame = None
        self.history.clear()
        self.finished = False
        self.running = True
        self._reset_requested = True
        self._unpaused_event.set()
        self._interrupt_event.set()

    def set_time_scale(self, scale: float) -> None:
        self.time_scale = validate_time_scale(scale)
        self._interrupt_event.set()

    def add_subscriber(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.add(queue)

    def remove_subscriber(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, msg: str) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(msg)

    def _broadcast_frame(self, frame: TelemetryFrame) -> None:
        self.history.append(frame)
        self._broadcast(f"event: telemetry\ndata: {frame.model_dump_json()}\n\n")

    def _broadcast_end(self, scenario_name: str) -> None:
        payload = json.dumps({"scenario": scenario_name})
        self._broadcast(f"event: end\ndata: {payload}\n\n")

    def _next_scenario_name(self) -> str:
        current = self.pipeline.scenario
        try:
            index = SCENARIO_ORDER.index(current)
        except ValueError:
            return DEFAULT_SCENARIO_NAME
        return SCENARIO_ORDER[(index + 1) % len(SCENARIO_ORDER)].value

    async def _advance_scenario_after_delay(self) -> None:
        if self.finish_restart_delay_s is None:
            return
        try:
            await asyncio.wait_for(self._unpaused_event.wait(), timeout=self.finish_restart_delay_s)
        except TimeoutError:
            if not self._shutdown:
                self.set_scenario(self._next_scenario_name())

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        interval_min = self.pipeline.measurement_interval_min
        total_sim_s = interval_min * SECONDS_PER_MINUTE
        self._sim_elapsed = total_sim_s

        while not self._shutdown:
            await self._unpaused_event.wait()
            if self._shutdown:
                break

            if self._reset_requested:
                self._reset_requested = False
                self._sim_elapsed = total_sim_s

            if self._sim_elapsed >= total_sim_s:
                try:
                    frame = self.pipeline.step()
                    self.last_frame = frame
                    self._broadcast_frame(frame)
                    interval_min = self.pipeline.measurement_interval_min
                    total_sim_s = interval_min * SECONDS_PER_MINUTE
                    self._sim_elapsed = 0.0
                except SimulationFinished:
                    self.finished = True
                    self.running = False
                    self._unpaused_event.clear()
                    self._broadcast_end(self.pipeline.scenario.value)
                    await self._advance_scenario_after_delay()
                    self._sim_elapsed = total_sim_s
                    continue

            while self._sim_elapsed < total_sim_s and not self._shutdown:
                if not self.running or self._reset_requested:
                    break

                remaining_sim = total_sim_s - self._sim_elapsed
                time_scale = self.time_scale
                delay_s = remaining_sim / time_scale
                t_start = loop.time()

                try:
                    await asyncio.wait_for(self._interrupt_event.wait(), timeout=delay_s)
                    self._interrupt_event.clear()
                    dt_real = loop.time() - t_start
                    self._sim_elapsed += dt_real * time_scale
                    if self._shutdown or not self.running or self._reset_requested:
                        break
                except TimeoutError:
                    self._sim_elapsed = total_sim_s
                    break


manager: Final[SimulationManager] = SimulationManager()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if not os.getenv(DEMO_KEY_ENV, ""):
        logger.warning(
            "DEMO_CONTROL_KEY is not configured; control endpoints are open without authentication."
        )
    manager.start()
    try:
        yield
    finally:
        await manager.stop()


def require_demo_key(x_demo_key: str | None = Header(default=None)) -> None:
    expected = os.getenv(DEMO_KEY_ENV, "")
    if not expected:
        logger.warning("DEMO_CONTROL_KEY is not configured; demo control endpoints are unauthenticated.")
        return
    provided = x_demo_key or ""
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=HTTP_UNAUTHORIZED,
            detail=f"A valid {DEMO_KEY_HEADER} header is required for this endpoint.",
        )


class ControlAction(str, Enum):
    pause = "pause"
    resume = "resume"
    restart = "restart"


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ControlAction


class SpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_scale: float

    @field_validator("time_scale")
    @classmethod
    def validate_scale(cls, value: float) -> float:
        return validate_time_scale(value)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    contract_version: str
    scenario: str
    running: bool
    finished: bool


class ScenarioInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    duration_min: float


class ScenarioChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str
    status: str


app: Final[FastAPI] = FastAPI(
    title="Mars Transit Crew Health & Cabin Air Monitor",
    version=CONTRACT_VERSION,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {
            **error,
            "input": str(error.get("input")),
            "ctx": {k: str(v) for k, v in error.get("ctx", {}).items()},
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/api/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        contract_version=CONTRACT_VERSION,
        scenario=manager.pipeline.scenario.value,
        running=manager.running,
        finished=manager.finished,
    )


@app.get("/api/scenarios", response_model=list[ScenarioInfo])
async def get_scenarios() -> list[ScenarioInfo]:
    items: list[ScenarioInfo] = []
    for sc in Scenario:
        path = SCENARIOS_DIR / f"{sc.value}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(
                    ScenarioInfo(
                        id=sc.value,
                        title=str(data.get("title", sc.value)),
                        duration_min=float(data.get("duration_min", 0.0)),
                    )
                )
            except (KeyError, OSError, ValueError):
                continue
    return items


@app.post(
    "/api/scenario/{name}",
    response_model=ScenarioChangeResponse,
)
async def post_scenario(name: str) -> ScenarioChangeResponse:
    valid_names = {s.value for s in Scenario}
    scenario_path = SCENARIOS_DIR / f"{name}.json"
    if name not in valid_names or not scenario_path.is_file():
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail=f"Scenario '{name}' not found.",
        )
    try:
        manager.set_scenario(name)
    except (FileNotFoundError, ValueError, OSError) as err:
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail=f"Scenario '{name}' could not be loaded: {err}",
        ) from err
    return ScenarioChangeResponse(scenario=name, status="started")


@app.post("/api/control", dependencies=[Depends(require_demo_key)])
async def post_control(req: ControlRequest) -> dict[str, bool]:
    if req.action == ControlAction.pause:
        manager.pause()
    elif req.action == ControlAction.resume:
        manager.resume()
    elif req.action == ControlAction.restart:
        manager.restart()
    return {"running": manager.running}


@app.post("/api/speed", dependencies=[Depends(require_demo_key)])
async def post_speed(req: SpeedRequest) -> dict[str, float]:
    manager.set_time_scale(req.time_scale)
    return {"time_scale": manager.time_scale}


@app.get("/api/state")
async def get_state() -> Response:
    frame = manager.last_frame
    if frame is None:
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail="No telemetry frame has been produced yet.",
        )
    return Response(content=frame.model_dump_json(), media_type="application/json")


@app.get("/api/limits")
async def get_limits() -> dict[str, Any]:
    try:
        return build_limits(RANGES_PATH)
    except FileNotFoundError as err:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail="Limits reference not found.") from err
    except (OSError, ValueError, AttributeError) as err:
        raise HTTPException(
            status_code=HTTP_SERVICE_UNAVAILABLE, detail="Limits reference not available."
        ) from err


@app.get("/api/sep-event")
async def get_sep_event() -> dict[str, Any]:
    try:
        return build_sep_event(SEP_EVENT_PATH)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail="SEP event record not found.") from err


@app.get("/api/history")
async def get_history(
    limit: int = Query(default=DEFAULT_HISTORY_LIMIT, ge=MIN_HISTORY_LIMIT, le=HISTORY_MAXLEN),
) -> dict[str, Any]:
    frames = list(manager.history)[-limit:]
    return {
        "scenario": manager.pipeline.scenario.value,
        "frames": [frame.model_dump(mode="json") for frame in frames],
    }


@app.get("/api/stream")
async def get_stream(request: Request) -> StreamingResponse:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
    if manager.last_frame is not None:
        queue.put_nowait(f"event: telemetry\ndata: {manager.last_frame.model_dump_json()}\n\n")

    manager.add_subscriber(queue)

    async def event_generator() -> AsyncIterator[str]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL_S)
                    yield msg
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            manager.remove_subscriber(queue)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@app.api_route("/checks{rest_of_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def block_checks(rest_of_path: str = "") -> None:
    raise HTTPException(status_code=HTTP_NOT_FOUND, detail="Not Found")


FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
