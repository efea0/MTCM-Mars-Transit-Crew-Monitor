"""FastAPI tabanlı telemetri sunucusu ve SSE yayını — sahibi: Aydın (backend)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from enum import Enum
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from backend.comms import DelayedLink
from backend.models import CONTRACT_VERSION, Scenario, TelemetryFrame
from backend.rules import RulesEngine, SepContext
from backend.simulator import SIMULATION_EPOCH, SimulationFinished, Simulator

DEFAULT_SCENARIO_NAME: Final[str] = "nominal"
DEFAULT_TIME_SCALE: Final[float] = 600.0
MAX_TIME_SCALE: Final[float] = 6000.0
MIN_TIME_SCALE: Final[float] = 0.0
SUBSCRIBER_QUEUE_MAXSIZE: Final[int] = 100
KEEPALIVE_INTERVAL_S: Final[float] = 15.0
SECONDS_PER_MINUTE: Final[float] = 60.0
HTTP_NOT_FOUND: Final[int] = 404

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"
RANGES_PATH: Final[Path] = DATA_DIR / "reference" / "ranges.json"
SCENARIOS_DIR: Final[Path] = DATA_DIR / "scenarios"
FRONTEND_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "frontend"


def load_default_time_scale(ranges_path: Path = RANGES_PATH) -> float:
    env_scale = os.getenv("MARS_TIME_SCALE")
    if env_scale is not None:
        try:
            parsed = float(env_scale)
            if parsed > MIN_TIME_SCALE:
                return parsed
        except ValueError:
            pass
    try:
        data = json.loads(ranges_path.read_text(encoding="utf-8"))
        return float(data["iletisim"]["demo_zaman_carpani"]["deger"])
    except (KeyError, OSError, TypeError, ValueError):
        return DEFAULT_TIME_SCALE


class Pipeline:
    """HTTP ve FastAPI'den bağımsız simülasyon ve kural motoru hattı."""

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
        self.link = DelayedLink()
        self._active_keys: set[tuple[str, ...]] = set()

    @property
    def measurement_interval_min(self) -> float:
        return self.sim._spec.model.measurement_interval_min

    @property
    def finished(self) -> bool:
        return self.sim.finished

    def reset(self, scenario: Scenario | str) -> None:
        self.scenario = Scenario(scenario)
        self.sim = Simulator(self.scenario, scenario_dir=self.scenarios_dir, ranges_path=self.ranges_path)
        self.rules = RulesEngine(self.ranges_path)
        self.link = DelayedLink()
        self._active_keys.clear()

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

        for released_key in self._active_keys - current_keys:
            self.link.release(released_key)

        self._active_keys = current_keys
        self.link.update(frame.mission_elapsed_s)

        return frame.model_copy(
            update={
                "messages": self.link.contract_messages(SIMULATION_EPOCH, frame.mission_elapsed_s),
                "comms_one_way_delay_s": self.link.one_way_delay_s,
            }
        )


class SimulationManager:
    """Arka plan zaman döngüsü ve SSE yayın yöneticisi."""

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
        self.running: bool = True
        self.finished: bool = False
        self.last_frame: TelemetryFrame | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._unpaused_event: asyncio.Event = asyncio.Event()
        self._unpaused_event.set()
        self._interrupt_event: asyncio.Event = asyncio.Event()
        self._shutdown: bool = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._shutdown = False
        self._unpaused_event = asyncio.Event()
        if self.running and not self.finished:
            self._unpaused_event.set()
        self._interrupt_event = asyncio.Event()
        if self._task is None or self._task.done():
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
        self.finished = False
        self.running = True
        self._unpaused_event.set()
        self._interrupt_event.set()

    def set_scenario(self, scenario_name: str) -> None:
        self.pipeline.reset(scenario_name)
        self.finished = False
        self.running = True
        self._unpaused_event.set()
        self._interrupt_event.set()

    def set_time_scale(self, scale: float) -> None:
        self.time_scale = scale
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
        self._broadcast(f"event: telemetry\ndata: {frame.model_dump_json()}\n\n")

    def _broadcast_end(self, scenario_name: str) -> None:
        payload = json.dumps({"scenario": scenario_name})
        self._broadcast(f"event: end\ndata: {payload}\n\n")

    async def _run_loop(self) -> None:
        while not self._shutdown:
            await self._unpaused_event.wait()
            if self._shutdown:
                break

            try:
                frame = self.pipeline.step()
                self.last_frame = frame
                self._broadcast_frame(frame)
            except SimulationFinished:
                self.finished = True
                self.running = False
                self._unpaused_event.clear()
                self._broadcast_end(self.pipeline.scenario.value)
                continue

            interval_min = self.pipeline.measurement_interval_min
            delay_s = (interval_min * SECONDS_PER_MINUTE) / self.time_scale

            try:
                await asyncio.wait_for(self._interrupt_event.wait(), timeout=delay_s)
                self._interrupt_event.clear()
            except TimeoutError:
                pass


manager: Final[SimulationManager] = SimulationManager()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    manager.start()
    try:
        yield
    finally:
        await manager.stop()


class ControlAction(str, Enum):
    pause = "pause"
    resume = "resume"
    restart = "restart"


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ControlAction


class SpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_scale: float = Field(gt=MIN_TIME_SCALE, le=MAX_TIME_SCALE)


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


@app.post("/api/scenario/{name}", response_model=ScenarioChangeResponse)
async def post_scenario(name: str) -> ScenarioChangeResponse:
    valid_names = {s.value for s in Scenario}
    scenario_path = SCENARIOS_DIR / f"{name}.json"
    if name not in valid_names or not scenario_path.is_file():
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail=f"'{name}' senaryosu bulunamadı.",
        )
    manager.set_scenario(name)
    return ScenarioChangeResponse(scenario=name, status="started")


@app.post("/api/control")
async def post_control(req: ControlRequest) -> dict[str, bool]:
    if req.action == ControlAction.pause:
        manager.pause()
    elif req.action == ControlAction.resume:
        manager.resume()
    elif req.action == ControlAction.restart:
        manager.restart()
    return {"running": manager.running}


@app.post("/api/speed")
async def post_speed(req: SpeedRequest) -> dict[str, float]:
    manager.set_time_scale(req.time_scale)
    return {"time_scale": manager.time_scale}


@app.get("/api/state")
async def get_state() -> Response:
    frame = manager.last_frame
    if frame is None:
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail="Henüz telemetri karesi üretilmedi.",
        )
    return Response(content=frame.model_dump_json(), media_type="application/json")


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
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            manager.remove_subscriber(queue)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
