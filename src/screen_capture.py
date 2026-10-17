import asyncio
import secrets
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

# GI jest dostępne w systemowym Pythonie Ubuntu.
# Pozostałe zależności projektu pozostają w .venv.
SYSTEM_PACKAGES = "/usr/lib/python3/dist-packages"
if SYSTEM_PACKAGES not in sys.path:
    sys.path.append(SYSTEM_PACKAGES)

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus


DEST = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"
REQ_IFACE = "org.freedesktop.portal.Request"


class ScreenCapture(QThread):
    frame_received = Signal(int, int, bytes)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._pipeline = None
        self._portal_bus = None
        self._session = None

    def stop(self):
        self._running = False
        self.wait(5000)

    def run(self):
        try:
            asyncio.run(self._capture_loop())
        except Exception as exc:
            self.status_changed.emit(
                f"Błąd przechwytywania: {type(exc).__name__}: {exc}"
            )

    async def _portal_call(
        self, interface, member, signature="", body=None, path=PORTAL_PATH
    ):
        message = Message(
            destination=DEST,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )

        reply = await asyncio.wait_for(
            self._portal_bus.call(message), timeout=15
        )

        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{reply.error_name}: {reply.body}")

        return reply

    async def _wait_response(self, request_path, timeout=120):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def handler(message):
            if (
                message.path == request_path
                and message.interface == REQ_IFACE
                and message.member == "Response"
                and not future.done()
            ):
                future.set_result(message.body)

        self._portal_bus.add_message_handler(handler)

        try:
            code, results = await asyncio.wait_for(future, timeout)

            if code != 0:
                raise RuntimeError(
                    f"Portal odmówił operacji: {code}, {results}"
                )

            return results
        finally:
            self._portal_bus.remove_message_handler(handler)

    async def _capture_loop(self):
        Gst.init(None)

        self.status_changed.emit("Łączenie z portalem ekranu...")

        self._portal_bus = await MessageBus(
            bus_type=BusType.SESSION,
            negotiate_unix_fd=True,
        ).connect()

        token = secrets.token_hex(8)

        try:
            # Utworzenie sesji
            options = {
                "session_handle_token": Variant(
                    "s", f"sc2_session_{token}"
                ),
                "handle_token": Variant("s", f"sc2_create_{token}"),
            }

            reply = await self._portal_call(
                IFACE, "CreateSession", "a{sv}", [options]
            )
            result = await self._wait_response(reply.body[0])
            self._session = result["session_handle"].value

            # Wybór monitora
            options = {
                "types": Variant("u", 1),
                "multiple": Variant("b", False),
                "handle_token": Variant("s", f"sc2_select_{token}"),
            }

            reply = await self._portal_call(
                IFACE,
                "SelectSources",
                "oa{sv}",
                [self._session, options],
            )
            await self._wait_response(reply.body[0])

            # Uruchomienie strumienia
            options = {
                "handle_token": Variant("s", f"sc2_start_{token}")
            }

            reply = await self._portal_call(
                IFACE,
                "Start",
                "osa{sv}",
                [self._session, "", options],
            )
            result = await self._wait_response(reply.body[0])

            streams = result.get(
                "streams", Variant("a(ua{sv})", [])
            ).value

            if not streams:
                raise RuntimeError("Portal nie zwrócił strumienia ekranu.")

            node_id = streams[0][0]

            # Deskryptor połączenia PipeWire
            reply = await self._portal_call(
                IFACE,
                "OpenPipeWireRemote",
                "oa{sv}",
                [self._session, {}],
            )

            if not reply.unix_fds:
                raise RuntimeError("Nie otrzymano deskryptora PipeWire.")

            fd = reply.unix_fds[0]

            description = (
                f"pipewiresrc fd={fd} path={node_id} do-timestamp=true "
                "! videoconvert "
                "! video/x-raw,format=RGB "
                "! appsink name=sc2sink max-buffers=1 drop=true sync=false"
            )

            self._pipeline = Gst.parse_launch(description)
            sink = self._pipeline.get_by_name("sc2sink")

            state_result = self._pipeline.set_state(Gst.State.PLAYING)

            if state_result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Nie udało się uruchomić GStreamera.")

            self.status_changed.emit("Przechwytywanie ekranu działa.")

            # Pobieramy klatki bez blokowania interfejsu Qt.
            while self._running:
                sample = await asyncio.to_thread(
                    sink.emit, "try-pull-sample", Gst.SECOND
                )

                if sample is None:
                    continue

                structure = sample.get_caps().get_structure(0)
                width = structure.get_value("width")
                height = structure.get_value("height")

                buffer = sample.get_buffer()
                frame = buffer.extract_dup(0, buffer.get_size())

                expected_size = width * height * 3

                if len(frame) != expected_size:
                    self.status_changed.emit(
                        "Nieoczekiwany format klatki."
                    )
                    continue

                self.frame_received.emit(width, height, frame)

        finally:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
                self._pipeline = None

            if self._session:
                try:
                    await self._portal_call(
                        "org.freedesktop.portal.Session",
                        "Close",
                        path=self._session,
                    )
                except Exception:
                    pass

            self._portal_bus.disconnect()
            self._portal_bus = None
