"""backend/main.py için birim, HTTP ve SSE akış testleri."""

from __future__ import annotations

import threading
import time
import warnings
from collections.abc import Generator

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore", category=DeprecationWarning, module="starlette")

from backend.main import (  # noqa: E402
    CONTRACT_VERSION,
    DEFAULT_SCENARIO_NAME,
    DEFAULT_TIME_SCALE,
    Pipeline,
    app,
    manager,
)
from backend.models import Scenario, TelemetryFrame  # noqa: E402
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
    assert found_alerts, "Hypoxia senaryosunda 50 adımda uyarı üretilmedi"


def test_pipeline_alert_deduplication() -> None:
    pipeline = Pipeline(Scenario.hypoxia)
    while not pipeline.step().alerts:
        pass
    frame_with_alert = pipeline.step()
    msg_count_1 = len(frame_with_alert.messages)
    assert msg_count_1 > 0
    # Aynı uyarı aktif kaldığı sürece yeni mesaj eklenmemeli
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


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
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
    assert "bulunamadı" in data["detail"].lower()


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
            assert "Henüz telemetri karesi üretilmedi." in res.json()["detail"]
    finally:
        manager.last_frame = original_frame
        if original_running:
            manager.resume()


def test_api_stream_sse() -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="error", ws="none")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(50):
        if server.started:
            break
        time.sleep(0.02)

    manager.set_time_scale(6000.0)
    manager.set_scenario("nominal")

    frames: list[TelemetryFrame] = []
    event_names: list[str] = []
    try:
        with (
            httpx.Client(base_url="http://127.0.0.1:8001", timeout=5.0) as client,
            client.stream("GET", "/api/stream") as resp,
        ):
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
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)

    assert len(frames) == 3
    assert event_names[:3] == ["telemetry"] * 3  # docs/contract.json: event: telemetry
    for frame in frames:
        assert frame.scenario == Scenario.nominal
        assert frame.simulated is True
        assert len(frame.crew) == 4
        assert frame.cabin.status is not None
