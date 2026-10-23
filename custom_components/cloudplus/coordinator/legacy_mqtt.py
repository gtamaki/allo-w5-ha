"""Legacy Meari ("ppstrong") MQTT push channel.

Older Meari devices (legacy PPCS transport, no IoT-hub config, e.g. the
Intelbras Allo w3/w5) do not publish alarms on the ``$bsssvr/iot/...``
event topic. The official app receives their pushes on a second broker,
``mqtts.meari.com.cn:8883``, subscribed to ``meari/app/<clientID>``:

* client id  ``<clientID>-/<4 random digits>``
* username   ``<clientID>-<epoch ms>-/<same 4 digits>``
* password   ``base64(HMAC-SHA1(username, userToken))``
* TLS        trusted against the broker's private CA bundled with the app
               (``meari_legacy_ca.pem``); like the app, no hostname check.

Mirrors ``o0OOo00O.OooOO0O.OooO0Oo`` / ``o0OOOo00.OooOo`` in Intelbras Allo
6.2.1 (com.interbra.smarteye).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import random
import ssl
import time
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

LEGACY_MQTT_HOST = "mqtts.meari.com.cn"
LEGACY_MQTT_PORT = 8883
LEGACY_MQTT_KEEPALIVE = 60
_CA_FILE = Path(__file__).resolve().parent.parent / "meari_legacy_ca.pem"


def legacy_credentials(
    client_id: str, user_token: str, now_ms: int | None = None, suffix: str | None = None
) -> tuple[str, str, str]:
    """Return (mqtt_client_id, username, password) like the official app."""
    if suffix is None:
        suffix = "/" + "".join(str(random.randint(0, 9)) for _ in range(4))
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    username = f"{client_id}-{now_ms}-{suffix}"
    digest = hmac.new(user_token.encode(), username.encode(), hashlib.sha1).digest()
    return f"{client_id}-{suffix}", username, base64.b64encode(digest).decode()


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    if _CA_FILE.is_file():
        ctx.load_verify_locations(cafile=str(_CA_FILE))
    return ctx


class LegacyMqttListener:
    """Connects to the legacy broker and hands every payload to a callback."""

    def __init__(
        self,
        client_id: str,
        user_token: str,
        on_payload: Callable[[str, bytes], None],
    ) -> None:
        self._client_id = client_id
        self._user_token = user_token
        self._on_payload = on_payload
        self._client: mqtt.Client | None = None
        self._topic = f"meari/app/{client_id}"
        self._warned = False

    def start(self) -> None:
        if self._client is not None or not self._client_id or not self._user_token:
            return
        mqtt_id, username, password = legacy_credentials(
            self._client_id, self._user_token
        )
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=mqtt_id,
                clean_session=False,
                protocol=mqtt.MQTTv311,
            )
        except (AttributeError, TypeError):
            client = mqtt.Client(
                client_id=mqtt_id, clean_session=False, protocol=mqtt.MQTTv311
            )
        client.username_pw_set(username, password)
        client.tls_set_context(_ssl_context())

        def on_connect(cli, _userdata, *args):
            rc = args[1] if len(args) > 1 else (args[0] if args else None)
            if str(rc) in ("0", "Success"):
                self._warned = False
                cli.subscribe(self._topic, qos=2)
                _LOGGER.info("Legacy MQTT listener subscribed to %s", self._topic)
                return
            level = logging.DEBUG if self._warned else logging.WARNING
            self._warned = True
            _LOGGER.log(level, "Legacy MQTT listener connect failed: rc=%s", rc)

        def on_disconnect(_cli, _userdata, *args):
            _LOGGER.debug("Legacy MQTT listener disconnected: %s", args)

        def on_message(_cli, _userdata, msg):
            _LOGGER.info(
                "Legacy MQTT message on %s: %s",
                msg.topic,
                msg.payload[:600].decode("utf-8", "replace"),
            )
            try:
                self._on_payload(msg.topic, msg.payload)
            except Exception:  # noqa: BLE001 - never kill the paho thread
                _LOGGER.exception("Legacy MQTT payload handler failed")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=3, max_delay=120)
        self._client = client
        try:
            client.connect_async(
                LEGACY_MQTT_HOST, LEGACY_MQTT_PORT, keepalive=LEGACY_MQTT_KEEPALIVE
            )
            client.loop_start()
        except (OSError, ValueError) as exc:
            _LOGGER.warning("Legacy MQTT listener failed to start: %s", exc)
            self._client = None

    def stop(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except OSError:
            pass
