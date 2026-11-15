"""backend/comms.py kabul testleri — sahibi: Barış (comms). Zaman görev saniyesidir; testler beklemez."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from backend import comms
from backend.comms import DelayedLink, MessageState
from backend.models import CommsMessage, Sender

MIN = 60.0
CABIN_CO2 = ("cabin", "pp_co2", "warning")
CREW2_HR = ("crew-2", "heart_rate", "warning")
EPOCH = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def link(**kw: float) -> DelayedLink:
    return DelayedLink(**kw)


# K1
def test_default_delay_and_bounds() -> None:
    q = link(ground_processing_s=0)
    m = q.send(CABIN_CO2, "CO2 limiti aşıldı", 0)
    assert m is not None
    assert m.one_way_delay_s == 10 * MIN
    for ok in (3 * MIN, 22 * MIN):
        q.set_delay(ok)
    for bad in (2.9 * MIN, 22.1 * MIN, 0, -5):
        with pytest.raises(ValueError):
            q.set_delay(bad)
    with pytest.raises(ValueError):
        DelayedLink(one_way_delay_s=30 * MIN)
    with pytest.raises(ValueError):
        DelayedLink(ground_processing_s=-1)


# K1: sınırlar ranges.json'dan gelir, kodda sabit yok
def test_delay_bounds_come_from_ranges_json(tmp_path) -> None:
    assert comms.load_delay_bounds_s() == (3 * MIN, 22 * MIN)
    assert not hasattr(comms, "MIN_ONE_WAY_DELAY_S") and not hasattr(comms, "MAX_ONE_WAY_DELAY_S")
    custom = tmp_path / "ranges.json"
    custom.write_text(
        '{"iletisim": {"mars_tek_yon_gecikme": {"min": 5, "max": 9, "birim": "dakika"}}}', encoding="utf-8"
    )
    q = DelayedLink(one_way_delay_s=6 * MIN, delay_bounds_s=comms.load_delay_bounds_s(custom))
    q.set_delay(9 * MIN)
    with pytest.raises(ValueError):
        q.set_delay(10 * MIN)
    with pytest.raises(ValueError):
        q.set_delay(4 * MIN)


def test_delay_bounds_reject_bad_reference(tmp_path) -> None:
    bad = tmp_path / "ranges.json"
    bad.write_text('{"iletisim": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        comms.load_delay_bounds_s(bad)
    with pytest.raises(ValueError):
        comms.load_delay_bounds_s(tmp_path / "yok.json")


# K1 regresyonu: doğrudan verilen delay_bounds_s de doğrulanır; sıfır/negatif gecikme sızmaz
def test_delay_bounds_argument_is_validated() -> None:
    for bad_bounds in ((0, 100), (-10, 100), (100, 5), (-5, -1)):
        with pytest.raises(ValueError):
            DelayedLink(one_way_delay_s=10 * MIN, delay_bounds_s=bad_bounds)
    q = DelayedLink(one_way_delay_s=10 * MIN, delay_bounds_s=(3 * MIN, 22 * MIN))
    assert (q.min_delay_s, q.max_delay_s) == (3 * MIN, 22 * MIN)


# kenar: boş kuyrukta tüm işlemler güvenli ve boş döner
def test_empty_queue_operations_are_safe() -> None:
    q = link()
    assert q.update(0) == []
    assert q.messages() == []
    assert q.contract_messages(EPOCH, 0) == []
    q.release(CABIN_CO2)  # bilinmeyen anahtar, etkisiz
    assert q.update(10 * MIN) == []
    assert q.messages() == []


# kenar: aynı anda gönderilen mesajlar eşit zamanlarla sıraya girer, FIFO bozulmaz (K5)
def test_simultaneous_sends_share_fifo_times() -> None:
    q = link(one_way_delay_s=10 * MIN, ground_processing_s=2 * MIN)
    keys = (CABIN_CO2, CREW2_HR, ("crew-3", "spo2", "warning"))
    sent = [q.send(key, f"rapor-{i}", 100 * MIN) for i, key in enumerate(keys)]
    assert all(m is not None for m in sent)
    assert {m.deliver_s for m in sent} == {100 * MIN + 10 * MIN}
    assert {m.reply_s for m in sent} == {100 * MIN + 10 * MIN + 2 * MIN + 10 * MIN}
    q.update(1000 * MIN)
    assert [m.state for m in q.messages()] == [MessageState.replied] * 3


# K3, K4
def test_state_sequence_and_times() -> None:
    q = link(one_way_delay_s=10 * MIN, ground_processing_s=2 * MIN)
    m = q.send(CABIN_CO2, "CO2 limiti aşıldı", 100 * MIN)
    assert m is not None
    assert (m.state, m.id) == (MessageState.queued, "msg-1")

    q.update(100 * MIN)
    assert m.state is MessageState.in_transit
    assert (m.sent_s, m.deliver_s, m.reply_s) == (100 * MIN, 110 * MIN, 122 * MIN)
    assert m.remaining_s(104 * MIN) == pytest.approx(6 * MIN)

    q.update(110 * MIN - 1)
    assert m.state is MessageState.in_transit
    q.update(110 * MIN)
    assert m.state is MessageState.delivered
    assert m.reply_body is None
    assert m.remaining_s(115 * MIN) == pytest.approx(7 * MIN)

    q.update(122 * MIN - 1)
    assert m.state is MessageState.delivered
    q.update(122 * MIN)
    assert m.state is MessageState.replied
    assert m.reply_body
    assert m.remaining_s(130 * MIN) == 0


# K3: Mistral'ın kuralı alt sınırdır
@pytest.mark.parametrize("processing", [0, 2 * MIN, 5 * MIN])
def test_reply_lower_bound_is_two_one_way_delays(processing: float) -> None:
    q = link(one_way_delay_s=7 * MIN, ground_processing_s=processing)
    m = q.send(CABIN_CO2, "x", 0)
    assert m is not None
    assert m.reply_s - m.sent_s >= 2 * 7 * MIN
    if processing == 0:
        assert m.reply_s - m.sent_s == 14 * MIN


# K2
def test_delay_change_does_not_affect_message_in_flight() -> None:
    q = link(one_way_delay_s=20 * MIN, ground_processing_s=0)
    m1 = q.send(CABIN_CO2, "ilk", 0)
    q.update(0)
    q.set_delay(5 * MIN)
    m2 = q.send(CREW2_HR, "ikinci", 1 * MIN)
    assert m1 is not None and m2 is not None
    assert (m1.one_way_delay_s, m1.deliver_s, m1.reply_s) == (20 * MIN, 20 * MIN, 40 * MIN)
    assert m2.one_way_delay_s == 5 * MIN


# K5
def test_fifo_even_when_delay_shortened() -> None:
    q = link(one_way_delay_s=20 * MIN, ground_processing_s=0)
    m1 = q.send(CABIN_CO2, "ilk", 0)
    q.update(0)
    q.set_delay(3 * MIN)
    m2 = q.send(CREW2_HR, "ikinci", 1 * MIN)
    assert m1 is not None and m2 is not None
    assert m2.deliver_s >= m1.deliver_s  # 1 + 3 = 4 dk olurdu
    assert m2.reply_s >= m1.reply_s

    q.update(10 * MIN)
    assert m2.state is MessageState.in_transit, "m1 ulaşmadan m2 ulaşmamalı"
    q.update(20 * MIN)
    assert [m.state for m in q.messages()] == [MessageState.delivered, MessageState.delivered]
    assert [m.id for m in q.messages()] == ["msg-1", "msg-2"]


# K6
def test_same_alert_is_not_queued_twice() -> None:
    q = link()
    assert q.send(CABIN_CO2, "x", 0) is not None
    for t in range(1, 50):
        q.update(t * MIN)
        assert q.send(CABIN_CO2, "x", t * MIN) is None
    assert len(q.messages()) == 1


def test_same_alert_stays_blocked_after_reply_until_released() -> None:
    q = link(one_way_delay_s=3 * MIN, ground_processing_s=0)
    q.send(CABIN_CO2, "x", 0)
    q.update(100 * MIN)
    assert q.send(CABIN_CO2, "x", 100 * MIN) is None, "uyarı sürüyorsa cevaptan sonra da gitmez"
    q.release(CABIN_CO2)
    assert q.send(CABIN_CO2, "x", 101 * MIN) is not None
    assert len(q.messages()) == 2


def test_escalation_and_other_crew_are_new_messages() -> None:
    q = link()
    assert q.send(("crew-3", "spo2", "caution"), "caution", 0) is not None
    assert q.send(("crew-3", "spo2", "warning"), "warning", 1) is not None
    assert q.send(("crew-4", "spo2", "warning"), "başka kişi", 1) is not None
    assert len(q.messages()) == 3


def test_release_of_unknown_key_is_harmless() -> None:
    link().release(("yok", "yok", "yok"))


# K4: büyük adım ara durumları atlamadan sona götürür
def test_large_step_reaches_final_state_and_reports_change() -> None:
    q = link(one_way_delay_s=10 * MIN, ground_processing_s=2 * MIN)
    m = q.send(CABIN_CO2, "x", 0)
    changed = q.update(1000 * MIN)
    assert m is not None and m.state is MessageState.replied
    assert m in changed
    assert (m.sent_s, m.deliver_s, m.reply_s) == (0, 10 * MIN, 22 * MIN)
    assert q.update(1001 * MIN) == [], "değişiklik yoksa boş liste"


# K8
def test_reply_text_by_decision_with_default() -> None:
    q = link(one_way_delay_s=3 * MIN, ground_processing_s=0)
    q.replies["pp_co2"] = "Yedek CO2 giderme onaylandı."
    m1 = q.send(CABIN_CO2, "x", 0)
    m2 = q.send(CREW2_HR, "y", 0)
    q.update(100 * MIN)
    assert m1 is not None and m2 is not None
    assert m1.reply_body == "Yedek CO2 giderme onaylandı."
    assert m2.reply_body == comms.DEFAULT_REPLY


# sözleşme
def test_contract_messages_validate_and_match_times() -> None:
    q = link(one_way_delay_s=10 * MIN, ground_processing_s=2 * MIN)
    q.send(CABIN_CO2, "CO2 limiti aşıldı", 0)

    q.update(5 * MIN)
    (out,) = q.contract_messages(EPOCH, 5 * MIN)
    assert isinstance(out, CommsMessage)
    assert out.sender == Sender.system.value
    assert (out.deliver_at - out.sent_at).total_seconds() == out.one_way_delay_s == 10 * MIN

    q.update(13 * MIN)  # cevap Dünya'dan 12. dakikada çıktı, yolda
    out, reply = q.contract_messages(EPOCH, 13 * MIN)
    assert reply.sender == Sender.mission_control.value
    assert reply.id == out.id + comms.REPLY_ID_SUFFIX
    assert (reply.deliver_at - EPOCH).total_seconds() == 22 * MIN
    assert reply.sent_at >= out.deliver_at


def test_real_seconds_helper() -> None:
    assert comms.real_seconds(10 * MIN, 60) == 10
    with pytest.raises(ValueError):
        comms.real_seconds(1, 0)


# K7
def test_module_has_no_clock_or_disk_access() -> None:
    src = inspect.getsource(comms)
    for forbidden in ("import time", "from time", "datetime.now", "utcnow", "open(", "write_text", "sqlite"):
        assert forbidden not in src, f"comms.py içinde '{forbidden}' olmamalı"


def test_deterministic() -> None:
    def run() -> list[tuple[str, MessageState, float, float]]:
        q = link(one_way_delay_s=12 * MIN, ground_processing_s=2 * MIN)
        for i, t in enumerate((0, 5, 9)):
            q.send(("crew-1", f"metric{i}", "warning"), "x", t * MIN)
            q.update(t * MIN)
        q.update(30 * MIN)
        return [(m.id, m.state, m.deliver_s, m.reply_s) for m in q.messages()]

    assert run() == run()
