"""Earth link paneli (frontend): alan adları docs/contract.json ile birebir aynı olmalı (AGENTS §5).

Tarayıcı olmadan çalışır; panelin okuduğu alanları, HTML kimliklerini ve mock akışını statik denetler.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
MOCK_JS = (ROOT / "frontend" / "mock.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
FRAME_FIELDS = CONTRACT["$defs"]["TelemetryFrame"]["properties"]
MESSAGE_FIELDS = CONTRACT["$defs"]["CommsMessage"]["properties"]


def test_panel_reads_contract_message_fields() -> None:
    for field in ("id", "sender", "body", "sent_at", "deliver_at"):
        assert field in MESSAGE_FIELDS, field
        assert re.search(rf"message\.{field}\b", APP_JS), f"app.js message.{field} okumuyor"


def test_panel_reads_contract_frame_fields() -> None:
    for field in ("ts", "messages", "comms_one_way_delay_s"):
        assert field in FRAME_FIELDS, field
        assert re.search(rf"frame\??\.{field}\b", APP_JS), f"app.js frame.{field} okumuyor"


def test_message_has_no_status_field_so_delivery_is_derived() -> None:
    """CommsMessage durum taşımaz; panel durumu frame.ts ile deliver_at'ten türetir."""
    assert "status" not in MESSAGE_FIELDS
    assert "deliveryState" in APP_JS and "deliver_at" in APP_JS


def test_every_sender_has_a_label() -> None:
    for sender in CONTRACT["$defs"]["Sender"]["enum"]:
        assert re.search(rf"\b{sender}:\s*\"", APP_JS), f"LABELS.sender.{sender} yok"


def test_html_has_ids_the_panel_updates() -> None:
    for element_id in ("earth-delay", "earth-notice", "earth-messages"):
        assert f'id="{element_id}"' in INDEX_HTML, element_id
        assert f'getElementById("{element_id}")' in APP_JS, element_id


def test_mock_frames_carry_messages_and_delay() -> None:
    assert "messages: mockMessages(" in MOCK_JS
    assert "comms_one_way_delay_s: MOCK_ONE_WAY_DELAY_S" in MOCK_JS
