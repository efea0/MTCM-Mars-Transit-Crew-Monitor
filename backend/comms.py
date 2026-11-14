"""Dünya-Mars ışık gecikmeli mesajlaşma (3-22 dk) — sahibi: Barış (comms).

Mars aracından Dünya'ya giden raporları ve yer kontrolünün cevaplarını ışık gecikmesiyle modelleyen
saf Python modülü. FastAPI'ye, dosyaya, ağa ya da saate bağlı değildir: zaman her çağrıda görev
saniyesi (`now_s`, TelemetryFrame.mission_elapsed_s ile aynı saat) olarak verilir. Böylece demo
duraklatma / ileri sarma panel ile birlikte çalışır ve testler beklemeden sonuç verir.

Kurallar:
- K1  Tek yön gecikme sınırları data/reference/ranges.json iletisim.mars_tek_yon_gecikme alanından okunur
      (kodda sabit yazılmaz); varsayılan 600 s. Aralık dışı değer ValueError.
- K2  Gecikme gönderim anında mesaja sabitlenir; sonradan değişirse yoldaki mesaj etkilenmez.
- K3  Varış = gönderim + gecikme. Cevap = varış + yer işleme süresi + gecikme (varsayılan işleme 120 s).
      Bu yüzden cevap en erken gönderim + 2 x gecikme sonra gelir.
- K4  Durum sırası: queued -> in_transit -> delivered -> replied. Atlanmaz, geri gitmez.
- K5  Sırayla teslim (FIFO): gecikme kısaltılsa bile sonraki mesaj öncekinden önce ulaşmaz, cevabı da
      önce gelmez.
- K6  Aynı uyarı ikinci kez kuyruğa girmez. Anahtar (crew_id veya "cabin", metric/karar, severity).
      Uyarı bitince `release(key)` çağrılır; ancak o zaman aynı anahtar yeniden gönderilir.
- K7  Saat yok, veritabanı yok: mesajlar bellekte. Dosya yalnızca gecikme sınırlarını okumak için açılır.
- K8  Cevap metni karara göre `replies` sözlüğünden seçilir, yoksa varsayılan metin. Cevaplar simüledir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from backend.models import CommsMessage, Sender

RANGES_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "ranges.json"
DEFAULT_ONE_WAY_DELAY_S = 600.0  # 10 dk, demo varsayılanı
DEFAULT_GROUND_PROCESSING_S = 120.0  # HİPOTEZ: yer kontrolünün cevap hazırlama süresi
DEFAULT_REPLY = "Rapor alındı, değerlendiriliyor."
PENDING_REPLY = "(cevap yolda)"
REPLY_ID_SUFFIX = "-reply"

AlertKey = tuple[str, ...]

_SECONDS_PER_UNIT = {"saniye": 1.0, "dakika": 60.0}


def load_delay_bounds_s(path: Path | str = RANGES_PATH) -> tuple[float, float]:
    """Tek yön gecikme sınırlarını (min, max; saniye) ranges.json iletisim.mars_tek_yon_gecikme'den okur."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))["iletisim"]["mars_tek_yon_gecikme"]
        scale = _SECONDS_PER_UNIT[record["birim"]]
        low, high = float(record["min"]) * scale, float(record["max"]) * scale
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{path}: iletisim.mars_tek_yon_gecikme okunamadı ({error!r})") from error
    if not 0 < low <= high:
        raise ValueError(f"{path}: iletisim.mars_tek_yon_gecikme aralığı geçersiz: {low}-{high}")
    return low, high


class MessageState(str, Enum):
    queued = "queued"  # oluşturuldu, henüz gönderilmedi
    in_transit = "in_transit"  # Dünya'ya doğru yolda
    delivered = "delivered"  # Dünya'ya ulaştı, cevap bekleniyor
    replied = "replied"  # cevap araca ulaştı


@dataclass(eq=False)
class Message:
    """Kuyruktaki tek rapor ve onun cevabı. Zamanlar görev saniyesidir."""

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
        """Cevabın Dünya'dan çıktığı an."""
        return self.reply_s - self.one_way_delay_s

    def remaining_s(self, now_s: float) -> float:
        """Bir sonraki durum geçişine kalan görev saniyesi; cevap geldiyse 0."""
        target = {
            MessageState.queued: self.sent_s,
            MessageState.in_transit: self.deliver_s,
            MessageState.delivered: self.reply_s,
        }.get(self.state)
        return 0.0 if target is None else max(0.0, target - now_s)

    def to_contract(self, epoch: datetime, now_s: float) -> list[CommsMessage]:
        """Sözleşmedeki CommsMessage listesi: giden rapor ve, Dünya'dan çıktıysa, cevabı."""
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
    """Dünya bağlantısı: gecikmeli, sıralı, tekrarsız mesaj kuyruğu."""

    def __init__(
        self,
        one_way_delay_s: float = DEFAULT_ONE_WAY_DELAY_S,
        ground_processing_s: float = DEFAULT_GROUND_PROCESSING_S,
        replies: dict[str, str] | None = None,
        delay_bounds_s: tuple[float, float] | None = None,
    ) -> None:
        if ground_processing_s < 0:
            raise ValueError("ground_processing_s negatif olamaz")
        self.min_delay_s, self.max_delay_s = delay_bounds_s or load_delay_bounds_s()
        self.set_delay(one_way_delay_s)
        self.ground_processing_s = ground_processing_s
        self.replies = dict(replies or {})
        self._messages: list[Message] = []
        self._active: set[AlertKey] = set()

    @property
    def one_way_delay_s(self) -> float:
        return self._delay_s

    def set_delay(self, seconds: float) -> None:
        """K1, K2: yalnızca bundan sonra gönderilen mesajları etkiler."""
        if not self.min_delay_s <= seconds <= self.max_delay_s:
            raise ValueError(
                f"tek yön gecikme {self.min_delay_s:.0f}-{self.max_delay_s:.0f} s olmalı: {seconds}"
            )
        self._delay_s = float(seconds)

    def send(self, key: AlertKey, body: str, now_s: float, sender: Sender = Sender.system) -> Message | None:
        """Yeni mesaj 'queued' döner. Anahtar zaten aktifse None (K6)."""
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
        """Uyarı bitti: aynı anahtar yeniden gönderilebilir. Bilinmeyen anahtar sessizce geçilir."""
        self._active.discard(key)

    def update(self, now_s: float) -> list[Message]:
        """Süresi dolan geçişleri uygular; durumu değişen mesajları sırayla döndürür (K4)."""
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
        """Gönderim sırasıyla tüm mesajlar (kopya liste)."""
        return list(self._messages)

    def contract_messages(self, epoch: datetime, now_s: float) -> list[CommsMessage]:
        """TelemetryFrame.messages için hazır liste."""
        return [cm for m in self._messages for cm in m.to_contract(epoch, now_s)]

    def _reply_for(self, key: AlertKey) -> str:
        decision = key[1] if len(key) > 1 else ""
        return self.replies.get(decision, DEFAULT_REPLY)


def real_seconds(mission_s: float, time_scale: float) -> float:
    """Ekran için: görev saniyesini gerçek saniyeye çevirir. time_scale=60 ise 1 görev dk = 1 s."""
    if time_scale <= 0:
        raise ValueError("time_scale pozitif olmalı")
    return mission_s / time_scale
