"""Earth-Mars light-delay messaging (3-22 min) - owner: Barış (comms).

Pure-Python module that models reports going from the Mars craft to Earth, and ground control's
replies, with light delay. It does not depend on FastAPI, files, the network or the clock: time is
passed on every call as mission seconds (`now_s`, the same clock as TelemetryFrame.mission_elapsed_s).
So demo pause / fast-forward works with the panel, and tests return results without waiting.

Rules:
- K1  One-way delay bounds are read from data/reference/ranges.json iletisim.mars_tek_yon_gecikme
      (not hard-coded); default 600 s. A value outside the range raises ValueError.
- K2  The delay is fixed on the message at send time; a later change does not affect messages in flight.
- K3  Arrival = send + delay. Reply = arrival + ground processing time + delay (default processing 120 s).
      So the reply arrives at the earliest at send + 2 x delay.
- K4  State order: queued -> in_transit -> delivered -> replied. No skipping, no going back.
- K5  In-order delivery (FIFO): even if the delay is shortened, a later message does not arrive before
      an earlier one, and its reply does not arrive first.
- K6  The same alert is not queued twice. Key (crew_id or "cabin", metric/decision, severity).
      When the alert ends, `release(key)` is called; only then can the same key be sent again.
- K7  No clock, no database: messages live in memory. The file is read only for the delay bounds.
- K8  The reply text is chosen from the `replies` dict by decision, else the default text.
      Replies are simulated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from backend.models import CommsMessage, Sender

RANGES_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "ranges.json"
DEFAULT_ONE_WAY_DELAY_S = 600.0
DEFAULT_GROUND_PROCESSING_S = 120.0
DEFAULT_REPLY = "Report received, under review."
PENDING_REPLY = "(reply in transit)"
REPLY_ID_SUFFIX = "-reply"

AlertKey = tuple[str, ...]

_SECONDS_PER_UNIT = {"saniye": 1.0, "dakika": 60.0}


def _check_delay_bounds_s(low: float, high: float) -> None:
    if not 0 < low <= high:
        raise ValueError(f"invalid one-way delay range: {low}-{high} s (need 0 < min <= max)")


def load_delay_bounds_s(path: Path | str = RANGES_PATH) -> tuple[float, float]:
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))["iletisim"]["mars_tek_yon_gecikme"]
        scale = _SECONDS_PER_UNIT[record["birim"]]
        low, high = float(record["min"]) * scale, float(record["max"]) * scale
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{path}: could not read iletisim.mars_tek_yon_gecikme ({error!r})") from error
    try:
        _check_delay_bounds_s(low, high)
    except ValueError as error:
        raise ValueError(f"{path}: iletisim.mars_tek_yon_gecikme {error}") from error
    return low, high


class MessageState(str, Enum):
    queued = "queued"
    in_transit = "in_transit"
    delivered = "delivered"
    replied = "replied"


@dataclass(eq=False)
class Message:
    seq: int
    key: AlertKey
    body: str
    sender: Sender
    created_s: float
    sent_s: float
    one_way_delay_s: float
    deliver_s: float
    reply_s: float
    state: MessageState = MessageState.queued
    reply_body: str | None = None

    @property
    def id(self) -> str:
        return f"msg-{self.seq}"

    @property
    def reply_sent_s(self) -> float:
        return self.reply_s - self.one_way_delay_s

    def remaining_s(self, now_s: float) -> float:
        target = {
            MessageState.queued: self.sent_s,
            MessageState.in_transit: self.deliver_s,
            MessageState.delivered: self.reply_s,
        }.get(self.state)
        return 0.0 if target is None else max(0.0, target - now_s)

    def to_contract(self, epoch: datetime, now_s: float) -> list[CommsMessage]:
        out = [
            CommsMessage(
                id=self.id,
                sender=self.sender,
                body=self.body,
                sent_at=epoch + timedelta(seconds=self.sent_s),
                deliver_at=epoch + timedelta(seconds=self.deliver_s),
                one_way_delay_s=self.one_way_delay_s,
            )
        ]
        if now_s >= self.reply_sent_s:
            out.append(
                CommsMessage(
                    id=self.id + REPLY_ID_SUFFIX,
                    sender=Sender.mission_control,
                    body=self.reply_body or PENDING_REPLY,
                    sent_at=epoch + timedelta(seconds=self.reply_sent_s),
                    deliver_at=epoch + timedelta(seconds=self.reply_s),
                    one_way_delay_s=self.one_way_delay_s,
                )
            )
        return out


class DelayedLink:
    def __init__(
        self,
        one_way_delay_s: float = DEFAULT_ONE_WAY_DELAY_S,
        ground_processing_s: float = DEFAULT_GROUND_PROCESSING_S,
        replies: dict[str, str] | None = None,
        delay_bounds_s: tuple[float, float] | None = None,
    ) -> None:
        if ground_processing_s < 0:
            raise ValueError("ground_processing_s must not be negative")
        self.min_delay_s, self.max_delay_s = delay_bounds_s or load_delay_bounds_s()
        _check_delay_bounds_s(self.min_delay_s, self.max_delay_s)
        self.set_delay(one_way_delay_s)
        self.ground_processing_s = ground_processing_s
        self.replies = dict(replies or {})
        self._messages: list[Message] = []
        self._active: set[AlertKey] = set()

    @property
    def one_way_delay_s(self) -> float:
        return self._delay_s

    def set_delay(self, seconds: float) -> None:
        if not self.min_delay_s <= seconds <= self.max_delay_s:
            raise ValueError(
                f"one-way delay must be {self.min_delay_s:.0f}-{self.max_delay_s:.0f} s: {seconds}"
            )
        self._delay_s = float(seconds)

    def send(self, key: AlertKey, body: str, now_s: float, sender: Sender = Sender.system) -> Message | None:
        if key in self._active:
            return None
        self._active.add(key)
        deliver_s = now_s + self._delay_s
        reply_s = deliver_s + self.ground_processing_s + self._delay_s
        if self._messages:  # K5
            previous = self._messages[-1]
            deliver_s = max(deliver_s, previous.deliver_s)
            reply_s = max(reply_s, previous.reply_s)
        message = Message(
            seq=len(self._messages) + 1,
            key=key,
            body=body,
            sender=sender,
            created_s=now_s,
            sent_s=now_s,
            one_way_delay_s=self._delay_s,
            deliver_s=deliver_s,
            reply_s=reply_s,
        )
        self._messages.append(message)
        return message

    def release(self, key: AlertKey) -> None:
        self._active.discard(key)

    def update(self, now_s: float) -> list[Message]:
        changed: list[Message] = []
        for message in self._messages:
            before = message.state
            if message.state is MessageState.queued and now_s >= message.sent_s:
                message.state = MessageState.in_transit
            if message.state is MessageState.in_transit and now_s >= message.deliver_s:
                message.state = MessageState.delivered
            if message.state is MessageState.delivered and now_s >= message.reply_s:
                message.state = MessageState.replied
                message.reply_body = self._reply_for(message.key)
            if message.state is not before:
                changed.append(message)
        return changed

    def messages(self) -> list[Message]:
        return list(self._messages)

    def contract_messages(self, epoch: datetime, now_s: float) -> list[CommsMessage]:
        return [cm for m in self._messages for cm in m.to_contract(epoch, now_s)]

    def _reply_for(self, key: AlertKey) -> str:
        decision = key[1] if len(key) > 1 else ""
        return self.replies.get(decision, DEFAULT_REPLY)


def real_seconds(mission_s: float, time_scale: float) -> float:
    if time_scale <= 0:
        raise ValueError("time_scale must be positive")
    return mission_s / time_scale
