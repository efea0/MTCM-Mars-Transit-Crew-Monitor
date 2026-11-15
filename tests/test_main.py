"""Unit, HTTP and SSE stream tests for backend/main.py."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import warnings
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.requests import Request

warnings.filterwarnings("ignore", category=DeprecationWarning, module="starlette")

from backend.main import (  # noqa: E402
    CONTRACT_VERSION,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_SCENARIO_NAME,
    DEFAULT_TIME_SCALE,
    HISTORY_MAXLEN,
    MAX_TIME_SCALE,
    MIN_TIME_SCALE,
    RELEASE_AFTER_ABSENT_FRAMES,
    Pipeline,
    SimulationManager,
    app,
    get_stream,
    load_default_time_scale,
    manager,
)
from backend.models import Scenario, Severity, TelemetryFrame  # noqa: E402
from backend.simulator import SimulationFinished  # noqa: E402


def test_pipeline_nominal_frames() -> None:
    pipeline = Pipeline(Scenario.nominal)
    for _ in range(5):
        frame = pipeline.step()
        assert isinstance(frame, TelemetryFrame)
        assert frame.scenario == Scenario.nominal
        assert frame.simulated is True
        assert len(frame.crew) == 4
        assert frame.comms_one_way_delay_s > 0.0


def test_pipeline_hypoxia_alerts_and_messages() -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    found_alerts = False
    for _ in range(50):
        frame = pipeline.step()
        if frame.alerts:
            found_alerts = True
            assert len(frame.messages) > 0
            break
    assert found_alerts, "No alert produced in 50 steps of the hypoxia scenario"


def test_pipeline_solar_storm_warns_before_dose_is_received() -> None:
    pipeline = Pipeline(Scenario.solar_storm)
    first_minute: float | None = None
    while first_minute is None:
        frame = pipeline.step()
        if any("radyasyon" in alert.rule_id for alert in frame.alerts):
            first_minute = frame.mission_elapsed_s / 60
            doses = [crew.event_dose_msv or 0.0 for crew in frame.crew]
            assert max(doses) < 200.0
            assert {crew.decision for crew in frame.crew} == {"action"}
    assert 30 <= first_minute <= 45


def test_pipeline_alert_deduplication() -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    for _ in range(50):
        if pipeline.step().alerts:
            break
    else:
        pytest.fail("No alert produced in 50 steps of the hypoxia scenario")
    frame_with_alert = pipeline.step()
    msg_count_1 = len(frame_with_alert.messages)
    assert msg_count_1 > 0
    frame_with_alert_2 = pipeline.step()
    assert len(frame_with_alert_2.messages) == msg_count_1


def test_pipeline_reset() -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    for _ in range(5):
        pipeline.step()
    assert pipeline.sim._tick > 0
    pipeline.reset(Scenario.nominal)
    f0 = pipeline.step()
    assert f0.tick == 0
    assert f0.mission_elapsed_s == 0.0
    assert f0.scenario == Scenario.nominal


def test_pipeline_finished_raises() -> None:
    pipeline = Pipeline(Scenario.nominal)
    while not pipeline.finished:
        pipeline.step()
    with pytest.raises(SimulationFinished):
        pipeline.step()


def test_pipeline_hypoxia_message_hysteresis() -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    while not pipeline.finished:
        pipeline.step()
    messages = [
        message for message in pipeline.link.messages() if message.key[:2] == ("crew-1", "karar-kalp_yuku")
    ]
    assert 1 <= len(messages) <= 10


@pytest.mark.parametrize(
    "absent_frames",
    [1, RELEASE_AFTER_ABSENT_FRAMES - 1, RELEASE_AFTER_ABSENT_FRAMES, RELEASE_AFTER_ABSENT_FRAMES + 1],
)
def test_pipeline_release_after_consecutive_absence(
    monkeypatch: pytest.MonkeyPatch, absent_frames: int
) -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    for _ in range(50):
        frame = pipeline.step()
        if frame.alerts:
            break
    else:
        pytest.fail("No alert found for the test")
    alert = frame.alerts[0]
    pipeline.reset(Scenario.hypoxia)
    frames = iter(
        [
            frame.model_copy(update={"alerts": alerts})
            for alerts in [[alert], *([[]] * absent_frames), [alert]]
        ]
    )
    monkeypatch.setattr(pipeline.rules, "evaluate", lambda *args, **kwargs: next(frames))
    for _ in range(absent_frames + 2):
        pipeline.step()
    expected = 2 if absent_frames >= RELEASE_AFTER_ABSENT_FRAMES else 1
    assert len(pipeline.link.messages()) == expected


def test_pipeline_severity_changes_send_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    for _ in range(50):
        frame = pipeline.step()
        if frame.alerts:
            break
    else:
        pytest.fail("No alert found for the test")
    alert = frame.alerts[0]
    pipeline.reset(Scenario.hypoxia)
    severities = (Severity.caution, Severity.critical, Severity.caution)
    frames = iter(
        frame.model_copy(update={"alerts": [alert.model_copy(update={"severity": severity})]})
        for severity in severities
    )
    monkeypatch.setattr(pipeline.rules, "evaluate", lambda *args, **kwargs: next(frames))
    for _ in severities:
        pipeline.step()
    assert [message.key[-1] for message in pipeline.link.messages()] == [s.value for s in severities]
    pipeline.reset(Scenario.hypoxia)
    assert not pipeline._active_keys
    assert not pipeline._absent_frames


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "1e9", "1e-300", "0", "-1", "bad"])
def test_invalid_env_time_scale_falls_back(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.delenv("MARS_TIME_SCALE", raising=False)
    fallback = load_default_time_scale()
    monkeypatch.setenv("MARS_TIME_SCALE", value)
    assert load_default_time_scale() == fallback


@pytest.mark.parametrize("value", [MIN_TIME_SCALE, DEFAULT_TIME_SCALE, MAX_TIME_SCALE])
def test_valid_env_time_scale(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setenv("MARS_TIME_SCALE", str(value))
    assert load_default_time_scale() == value


def test_invalid_config_time_scale_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MARS_TIME_SCALE", raising=False)
    ranges = tmp_path / "ranges.json"
    ranges.write_text(json.dumps({"iletisim": {"demo_zaman_carpani": {"deger": 1e9}}}), encoding="utf-8")
    assert load_default_time_scale(ranges) == DEFAULT_TIME_SCALE


def test_manager_slowdown_preserves_elapsed_simulation_time() -> None:
    async def check() -> None:
        local = SimulationManager()
        local.set_time_scale(600.0)
        queue: asyncio.Queue[str] = asyncio.Queue()
        local.add_subscriber(queue)
        local.start()
        try:
            await asyncio.wait_for(queue.get(), timeout=1.0)
            loop = asyncio.get_running_loop()
            started = loop.time()
            await asyncio.sleep(0.2)
            elapsed = loop.time() - started
            local.set_time_scale(60.0)
            await asyncio.wait_for(queue.get(), timeout=4.5)
            actual = loop.time() - started
            interval_s = local.pipeline.measurement_interval_min * 60.0
            expected = elapsed + (interval_s - elapsed * 600.0) / 60.0
            assert actual == pytest.approx(expected, abs=0.4)
        finally:
            await local.stop()

    asyncio.run(check())


def test_manager_start_pause_start_resume() -> None:
    async def check() -> None:
        local = SimulationManager()
        local.set_time_scale(MAX_TIME_SCALE)
        queue: asyncio.Queue[str] = asyncio.Queue()
        local.add_subscriber(queue)
        local.start()
        try:
            await asyncio.wait_for(queue.get(), timeout=1.0)
            local.pause()
            await asyncio.sleep(0.02)
            task, unpaused, interrupt = local._task, local._unpaused_event, local._interrupt_event
            local.start()
            assert local._task is task
            assert local._unpaused_event is unpaused
            assert local._interrupt_event is interrupt
            assert not local.running
            local.resume()
            msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            assert json.loads(msg.split("data: ", 1)[1])["tick"] == 1
        finally:
            await local.stop()

    asyncio.run(check())


@pytest.fixture
def isolated_manager(monkeypatch: pytest.MonkeyPatch) -> SimulationManager:
    local = SimulationManager()
    monkeypatch.setattr("backend.main.manager", local)
    return local


@pytest.fixture
def stream_request(monkeypatch: pytest.MonkeyPatch) -> Request:
    request = Request({"type": "http"})
    monkeypatch.setattr(request, "is_disconnected", AsyncMock(return_value=False))
    return request


@pytest.mark.parametrize("endpoint", ["/api/control", "/api/scenario/hypoxia"])
def test_reset_clears_state_and_sse_replay(
    isolated_manager: SimulationManager, stream_request: Request, endpoint: str
) -> None:
    async def check() -> None:
        local = isolated_manager
        local.last_frame = local.pipeline.step()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(endpoint, json={"action": "restart"})
            assert response.status_code == 200
            assert local.last_frame is None
            assert (await client.get("/api/state")).status_code == 404
        response = await get_stream(stream_request)
        assert all(queue.empty() for queue in local._subscribers)
        pending = asyncio.create_task(anext(response.body_iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not local._subscribers

    asyncio.run(check())


def test_stream_end_event(
    isolated_manager: SimulationManager, stream_request: Request, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        local = isolated_manager
        frame = local.pipeline.step()
        monkeypatch.setattr(local.pipeline, "step", Mock(side_effect=[frame, SimulationFinished]))
        local.set_time_scale(MAX_TIME_SCALE)
        response = await get_stream(stream_request)
        local.start()
        try:
            msg = await asyncio.wait_for(anext(response.body_iterator), timeout=1.0)
            assert msg.startswith("event: telemetry\n")
            end = await asyncio.wait_for(anext(response.body_iterator), timeout=1.0)
            assert end == 'event: end\ndata: {"scenario": "nominal"}\n\n'
            assert local.finished
            assert not local.running
            assert all(queue.empty() for queue in local._subscribers)
        finally:
            await response.body_iterator.aclose()
            await local.stop()

    asyncio.run(check())


def test_stream_pause_resume(isolated_manager: SimulationManager, stream_request: Request) -> None:
    async def check() -> None:
        local = isolated_manager
        local.set_time_scale(MAX_TIME_SCALE)
        response = await get_stream(stream_request)
        local.start()
        try:
            first = await asyncio.wait_for(anext(response.body_iterator), timeout=1.0)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.post("/api/control", json={"action": "pause"})).json() == {
                    "running": False
                }
                pending = asyncio.create_task(anext(response.body_iterator))
                await asyncio.sleep(0.15)
                assert not pending.done()
                assert (await client.post("/api/control", json={"action": "resume"})).json() == {
                    "running": True
                }
                second = await asyncio.wait_for(pending, timeout=1.0)
            assert first.startswith("event: telemetry\n")
            assert second.startswith("event: telemetry\n")
            assert (
                json.loads(second.split("data: ", 1)[1])["tick"]
                > json.loads(first.split("data: ", 1)[1])["tick"]
            )
        finally:
            await response.body_iterator.aclose()
            await local.stop()

    asyncio.run(check())


@pytest.mark.parametrize("disconnect", ["cancel", "close", "disconnect"])
def test_stream_disconnect_cleanup(
    isolated_manager: SimulationManager,
    stream_request: Request,
    monkeypatch: pytest.MonkeyPatch,
    disconnect: str,
) -> None:
    async def check() -> None:
        local = isolated_manager
        local.last_frame = local.pipeline.step()
        response = await get_stream(stream_request)
        assert (await anext(response.body_iterator)).startswith("event: telemetry\n")
        assert len(local._subscribers) == 1
        if disconnect == "cancel":
            pending = asyncio.create_task(anext(response.body_iterator))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        elif disconnect == "close":
            await response.body_iterator.aclose()
        else:
            monkeypatch.setattr(stream_request, "is_disconnected", AsyncMock(return_value=True))
            with pytest.raises(StopAsyncIteration):
                await anext(response.body_iterator)
        assert not local._subscribers

    asyncio.run(check())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.delenv("DEMO_CONTROL_KEY", raising=False)
    manager.set_scenario(DEFAULT_SCENARIO_NAME)
    manager.set_time_scale(DEFAULT_TIME_SCALE)
    with TestClient(app) as test_client:
        yield test_client


def test_api_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["contract_version"] == CONTRACT_VERSION
    assert data["scenario"] == DEFAULT_SCENARIO_NAME
    assert isinstance(data["running"], bool)
    assert isinstance(data["finished"], bool)


def test_api_scenarios(client: TestClient) -> None:
    res = client.get("/api/scenarios")
    assert res.status_code == 200
    scenarios = res.json()
    assert isinstance(scenarios, list)
    ids = [s["id"] for s in scenarios]
    assert "nominal" in ids
    assert "hypoxia" in ids
    for s in scenarios:
        assert "id" in s
        assert "title" in s
        assert "duration_min" in s
        assert s["duration_min"] > 0


def test_api_scenario_invalid(client: TestClient) -> None:
    res = client.post("/api/scenario/gecersiz_senaryo_xyz")
    assert res.status_code == 404
    data = res.json()
    assert "detail" in data
    assert "not found" in data["detail"].lower()


def test_api_scenario_valid_enum_missing_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A valid scenario name whose file is missing is 404. Every scenario file exists now, so the
    scenarios directory is pointed at an empty folder to create the missing-file case."""
    monkeypatch.setattr("backend.main.SCENARIOS_DIR", tmp_path)
    res = client.post("/api/scenario/sleep_deprivation")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_api_scenario_change(client: TestClient) -> None:
    res = client.post("/api/scenario/hypoxia")
    assert res.status_code == 200
    assert res.json() == {"scenario": "hypoxia", "status": "started"}
    health = client.get("/api/health").json()
    assert health["scenario"] == "hypoxia"
    client.post("/api/scenario/nominal")


def test_api_control(client: TestClient) -> None:
    res_pause = client.post("/api/control", json={"action": "pause"})
    assert res_pause.status_code == 200
    assert res_pause.json() == {"running": False}

    res_resume = client.post("/api/control", json={"action": "resume"})
    assert res_resume.status_code == 200
    assert res_resume.json() == {"running": True}

    res_restart = client.post("/api/control", json={"action": "restart"})
    assert res_restart.status_code == 200
    assert res_restart.json() == {"running": True}

    res_invalid = client.post("/api/control", json={"action": "bilinmeyen"})
    assert res_invalid.status_code == 422


def test_api_speed(client: TestClient) -> None:
    res_ok = client.post("/api/speed", json={"time_scale": 1200.0})
    assert res_ok.status_code == 200
    assert res_ok.json() == {"time_scale": 1200.0}

    res_zero = client.post("/api/speed", json={"time_scale": 0.0})
    assert res_zero.status_code == 422

    res_neg = client.post("/api/speed", json={"time_scale": -10.0})
    assert res_neg.status_code == 422

    res_too_high = client.post("/api/speed", json={"time_scale": 99999.0})
    assert res_too_high.status_code == 422


@pytest.mark.parametrize("raw_value", ["NaN", "Infinity", "-Infinity", "1e9", "1e-300"])
def test_api_speed_invalid_raw_json(client: TestClient, raw_value: str) -> None:
    original_scale = manager.time_scale
    response = client.post(
        "/api/speed",
        content='{"time_scale": ' + raw_value + "}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "time_scale"]
    assert isinstance(error["input"], str)
    assert manager.time_scale == original_scale


@pytest.mark.parametrize("scale", [MIN_TIME_SCALE, MAX_TIME_SCALE])
def test_api_speed_accepts_boundaries(client: TestClient, scale: float) -> None:
    response = client.post("/api/speed", json={"time_scale": scale})
    assert response.status_code == 200
    assert response.json() == {"time_scale": scale}


def test_api_speed_does_not_trigger_early_tick(client: TestClient) -> None:
    client.post("/api/control", json={"action": "restart"})
    client.post("/api/control", json={"action": "pause"})
    tick1 = client.get("/api/state").json()["tick"]
    for scale in (1000.0, 2000.0, 500.0, 3000.0):
        client.post("/api/speed", json={"time_scale": scale})
    tick2 = client.get("/api/state").json()["tick"]
    assert tick1 == tick2


def test_api_restart_and_scenario_change_after_finished(client: TestClient) -> None:
    manager.pause()
    pipeline = manager.pipeline
    while not pipeline.finished:
        pipeline.step()
    manager.finished = True
    manager.running = False

    res_restart = client.post("/api/control", json={"action": "restart"})
    assert res_restart.status_code == 200
    assert res_restart.json() == {"running": True}
    health = client.get("/api/health").json()
    assert health["running"] is True
    assert health["finished"] is False

    manager.pause()
    while not manager.pipeline.finished:
        manager.pipeline.step()
    manager.finished = True
    manager.running = False

    res_change = client.post("/api/scenario/hypoxia")
    assert res_change.status_code == 200
    assert res_change.json() == {"scenario": "hypoxia", "status": "started"}
    health2 = client.get("/api/health").json()
    assert health2["scenario"] == "hypoxia"
    assert health2["running"] is True
    assert health2["finished"] is False
    client.post("/api/scenario/nominal")


def test_pipeline_solar_storm_radiation_alert_and_decisions() -> None:
    pipeline = Pipeline(Scenario.solar_storm)
    rule_ids: set[str] = set()
    sep_decisions: set[str] = set()
    while not pipeline.finished:
        frame = pipeline.step()
        rule_ids.update(alert.rule_id for alert in frame.alerts)
        if any(crew.event_dose_msv is not None for crew in frame.crew):
            sep_decisions.update(str(getattr(crew.decision, "value", crew.decision)) for crew in frame.crew)
    assert "karar-radyasyon_etkisi" in rule_ids
    assert "unknown" not in sep_decisions


def test_pipeline_all_scenarios_to_end() -> None:
    for sc in ("nominal", "hypoxia", "hypercapnia", "solar_storm", "sensor_dropout"):
        p = Pipeline(sc)
        ticks = 0
        while not p.finished:
            frame = p.step()
            ticks += 1
            assert isinstance(frame, TelemetryFrame)
            assert (frame.scenario.value if hasattr(frame.scenario, "value") else str(frame.scenario)) == sc
        with pytest.raises(SimulationFinished):
            p.step()
        assert ticks > 0


def test_api_state(client: TestClient) -> None:
    res = client.get("/api/state")
    assert res.status_code == 200
    frame = TelemetryFrame.model_validate(res.json())
    assert frame.contract_version == CONTRACT_VERSION


def test_api_state_empty() -> None:
    original_frame = manager.last_frame
    original_running = manager.running
    try:
        manager.pause()
        manager.last_frame = None
        with TestClient(app) as test_client:
            res = test_client.get("/api/state")
            assert res.status_code == 404
            assert "No telemetry frame has been produced yet." in res.json()["detail"]
    finally:
        manager.last_frame = original_frame
        if original_running:
            manager.resume()


def test_api_stream_sse() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", ws="none")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()

    for _ in range(50):
        if server.started:
            break
        time.sleep(0.02)

    frames: list[TelemetryFrame] = []
    event_names: list[str] = []
    try:
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
            client.post("/api/speed", json={"time_scale": MAX_TIME_SCALE}).raise_for_status()
            client.post("/api/scenario/nominal").raise_for_status()
            with client.stream("GET", "/api/stream") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        event_names.append(line[len("event:") :].strip())
                    if line.startswith("data:"):
                        raw_json = line[len("data:") :].strip()
                        if "scenario" in raw_json and "contract_version" in raw_json:
                            frame = TelemetryFrame.model_validate_json(raw_json)
                            frames.append(frame)
                            if len(frames) >= 3:
                                break
            for _ in range(50):
                if not manager._subscribers:
                    break
                time.sleep(0.02)
            assert not manager._subscribers
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)
        listener.close()

    assert len(frames) == 3
    assert event_names[:3] == ["telemetry"] * 3
    for frame in frames:
        assert frame.scenario == Scenario.nominal
        assert frame.simulated is True
        assert len(frame.crew) == 4
        assert frame.cabin.status is not None


@pytest.mark.parametrize(
    ("method_path", "body"),
    [
        ("/api/control", {"action": "pause"}),
        ("/api/speed", {"time_scale": 600.0}),
    ],
)
def test_control_endpoints_require_demo_key_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method_path: str, body: dict[str, object] | None
) -> None:
    monkeypatch.setenv("DEMO_CONTROL_KEY", "test-anahtari")
    assert client.post(method_path, json=body).status_code == 401
    assert client.post(method_path, json=body, headers={"X-Demo-Key": "yanlis"}).status_code == 401
    assert client.post(method_path, json=body, headers={"X-Demo-Key": "test-anahtari"}).status_code == 200


def test_scenario_switch_open_with_demo_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_CONTROL_KEY", "test-anahtari")
    assert client.post("/api/scenario/hypoxia").status_code == 200
    assert client.post("/api/scenario/nominal").status_code == 200


def test_read_endpoints_stay_open_with_demo_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_CONTROL_KEY", "test-anahtari")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/scenarios").status_code == 200


def test_empty_demo_key_leaves_control_open(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_CONTROL_KEY", "")
    assert client.post("/api/control", json={"action": "resume"}).status_code == 200


def test_finished_scenario_advances_to_next_after_delay(
    isolated_manager: SimulationManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        local = isolated_manager
        local.set_scenario("nominal")
        frame = local.pipeline.step()
        steps = [frame, SimulationFinished, *([frame] * 50)]
        monkeypatch.setattr(local.pipeline, "step", Mock(side_effect=steps))
        local.finish_restart_delay_s = 0.05
        local.set_time_scale(MAX_TIME_SCALE)
        local.start()
        try:
            for _ in range(100):
                await asyncio.sleep(0.02)
                if local.pipeline.scenario == Scenario.hypoxia and not local.finished:
                    break
            assert local.pipeline.scenario == Scenario.hypoxia
            assert not local.finished
            assert local.running
        finally:
            await local.stop()

    asyncio.run(check())


def test_finished_scenario_stays_finished_without_delay(
    isolated_manager: SimulationManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        local = isolated_manager
        monkeypatch.setattr(local.pipeline, "step", Mock(side_effect=SimulationFinished))
        local.finish_restart_delay_s = None
        local.start()
        try:
            await asyncio.sleep(0.2)
            assert local.finished
            assert not local.running
        finally:
            await local.stop()

    asyncio.run(check())


def test_manager_history_buffer_is_bounded() -> None:
    local = SimulationManager()
    frame = local.pipeline.step()
    for _ in range(HISTORY_MAXLEN + 25):
        local._broadcast_frame(frame)
    assert len(local.history) == HISTORY_MAXLEN


def test_manager_history_cleared_on_scenario_change_and_restart() -> None:
    local = SimulationManager()
    local._broadcast_frame(local.pipeline.step())
    assert len(local.history) == 1
    local.restart()
    assert len(local.history) == 0
    local._broadcast_frame(local.pipeline.step())
    local.set_scenario("hypoxia")
    assert len(local.history) == 0


def test_api_history_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    local = SimulationManager()
    monkeypatch.setattr("backend.main.manager", local)
    with TestClient(app) as test_client:
        local.pause()
        local.history.clear()
        res = test_client.get("/api/history")
    assert res.status_code == 200
    assert res.json() == {"scenario": DEFAULT_SCENARIO_NAME, "frames": []}


def test_api_history_limit_order_and_format(monkeypatch: pytest.MonkeyPatch) -> None:
    local = SimulationManager()
    monkeypatch.setattr("backend.main.manager", local)
    frames = [local.pipeline.step() for _ in range(10)]
    for frame in frames:
        local._broadcast_frame(frame)
    with TestClient(app) as test_client:
        local.pause()
        default = test_client.get("/api/history").json()
        assert default["scenario"] == DEFAULT_SCENARIO_NAME
        assert len(default["frames"]) == len(local.history)
        limited = test_client.get("/api/history", params={"limit": 3}).json()["frames"]
    assert len(limited) == 3
    expected = [f.model_dump(mode="json") for f in list(local.history)[-3:]]
    assert limited == expected
    elapsed = [f["mission_elapsed_s"] for f in limited]
    assert elapsed == sorted(elapsed)
    TelemetryFrame.model_validate(limited[-1])


def test_api_history_default_limit_with_full_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    local = SimulationManager()
    monkeypatch.setattr("backend.main.manager", local)
    frame = local.pipeline.step()
    for _ in range(HISTORY_MAXLEN):
        local._broadcast_frame(frame)
    with TestClient(app) as test_client:
        local.pause()
        assert len(test_client.get("/api/history").json()["frames"]) == DEFAULT_HISTORY_LIMIT
        assert len(test_client.get("/api/history?limit=400").json()["frames"]) == HISTORY_MAXLEN


@pytest.mark.parametrize("limit", ["0", "-1", "401", "abc"])
def test_api_history_rejects_out_of_range_limit(limit: str) -> None:
    with TestClient(app) as test_client:
        assert test_client.get("/api/history", params={"limit": limit}).status_code == 422


def test_api_history_cleared_after_scenario_change(client: TestClient) -> None:
    manager.pause()
    manager._broadcast_frame(manager.pipeline.step())
    assert client.get("/api/history").json()["frames"]
    assert client.post("/api/scenario/hypoxia").status_code == 200
    manager.pause()
    body = client.get("/api/history").json()
    assert body["scenario"] == "hypoxia"
    assert all(f["scenario"] == "hypoxia" for f in body["frames"])


def test_checks_directory_returns_404(client: TestClient) -> None:
    assert client.get("/checks").status_code == 404
    assert client.get("/checks/").status_code == 404
    assert client.get("/checks/nasa-data.js").status_code == 404


def test_limits_returns_503_for_list_formatted_ranges(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_ranges = tmp_path / "bad_ranges.json"
    bad_ranges.write_text(json.dumps([{"item": 1}]), encoding="utf-8")
    monkeypatch.setattr("backend.main.RANGES_PATH", bad_ranges)
    res = client.get("/api/limits")
    assert res.status_code == 503
    assert res.json()["detail"] == "Limits reference not available."


def test_pipeline_reset_atomic_on_failure(tmp_path: Path) -> None:
    from backend.main import Pipeline

    p = Pipeline(scenario="nominal")
    orig_scenario = p.scenario
    orig_sim = p.sim
    with pytest.raises(ValueError):
        p.reset("non_existent_scenario_xyz")
    assert p.scenario == orig_scenario
    assert p.sim is orig_sim


def test_demo_control_key_missing_logs_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.delenv("DEMO_CONTROL_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        res = client.post("/api/control", json={"action": "pause"})
        assert res.status_code == 200
        assert any("DEMO_CONTROL_KEY is not configured" in rec.message for rec in caplog.records)


def test_pause_resume_preserves_sim_elapsed() -> None:
    async def check() -> None:
        local = SimulationManager()
        local.set_time_scale(60.0)
        queue: asyncio.Queue[str] = asyncio.Queue()
        local.add_subscriber(queue)
        local.start()
        try:
            msg1 = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert json.loads(msg1.split("data: ", 1)[1])["tick"] == 0
            await asyncio.sleep(0.1)
            local.pause()
            await asyncio.sleep(0.02)
            elapsed_at_pause = local._sim_elapsed
            assert 0.0 < elapsed_at_pause < 120.0

            local.resume()
            assert queue.empty()
            await asyncio.sleep(0.02)
            assert queue.empty()
            assert local._sim_elapsed >= elapsed_at_pause
        finally:
            await local.stop()

    asyncio.run(check())
