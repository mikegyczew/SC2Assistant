import os
import sys
import time
import subprocess
import shutil
import json
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QEvent, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QComboBox, QPlainTextEdit, QPushButton, QMessageBox,
)

from src.screen_capture import ScreenCapture
from src.sc2_api import get_game_info
from src.replay_analysis import (
    load_latest_replay, build_insights, ReplayChart, METRICS,
)



class PiperWorker(QThread):
    audio_ready = Signal(str, int)
    error_occurred = Signal(str, int)

    def __init__(self, text, model_path, request_id):
        super().__init__()
        self.text = text
        self.model_path = model_path
        self.request_id = request_id
        self.process = None
        self.cancelled = False

    def stop(self):
        """Przerywa generowanie mowy, jeśli proces już działa."""
        self.cancelled = True
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self):
        audio_path = None

        try:
            if self.cancelled:
                return

            model = Path(self.model_path)
            if not model.is_file():
                raise FileNotFoundError(
                    f"Nie znaleziono modelu polskiego głosu: {model}"
                )

            with tempfile.NamedTemporaryFile(
                prefix="sc2assistant-",
                suffix=".wav",
                delete=False,
            ) as audio_file:
                audio_path = audio_file.name

            command = [
                sys.executable,
                "-m", "piper",
                "--model", str(model),
                "--output_file", audio_path,
            ]

            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            _, stderr = self.process.communicate(
                input=self.text.encode("utf-8"),
                timeout=180,
            )

            if self.cancelled:
                return

            if self.process.returncode != 0:
                details = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    details or "Piper nie zdołał wygenerować pliku audio."
                )

            if not Path(audio_path).is_file() or Path(audio_path).stat().st_size == 0:
                raise RuntimeError("Piper wygenerował pusty plik audio.")

            self.audio_ready.emit(audio_path, self.request_id)
            audio_path = None

        except subprocess.TimeoutExpired:
            if self.process is not None:
                self.process.kill()
                self.process.communicate()
            if not self.cancelled:
                self.error_occurred.emit(
                    "Generowanie mowy trwało zbyt długo. Spróbuj ponownie.",
                    self.request_id,
                )

        except Exception as exc:
            if not self.cancelled:
                self.error_occurred.emit(
                    f"Nie udało się wygenerować mowy Piper: {exc}",
                    self.request_id,
                )

        finally:
            self.process = None
            if audio_path:
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except OSError:
                    pass


class SC2Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self._tts_request_id = 0
        self._tts_workers = []
        self._speech_process = None
        self._speech_audio_path = None

        self.setWindowTitle("SC2Assistant — analiza powtórek")
        # Pulpit analizy po meczu — nie zasłania stale gry.
        self.setWindowFlags(Qt.WindowType.Window)

        self.setStyleSheet("""
            QWidget#panel {
                background-color: #0c1420;
                border: 1px solid #32c8ff;
                border-radius: 10px;
                color: #eaf7ff;
                font-family: Sans Serif;
            }
            QLabel#title {
                color: #32c8ff;
                font-size: 20px;
                font-weight: bold;
            }
            QLabel#subtitle {
                color: #8da9bd;
                font-size: 11px;
            }
            QLabel#status {
                color: #71e6a1;
                font-size: 12px;
                font-weight: bold;
            }
            QFrame#separator {
                background-color: #29445a;
                max-height: 1px;
            }
            QComboBox {
                background-color: #17283a;
                color: #eaf7ff;
                border: 1px solid #29445a;
                border-radius: 4px;
                padding: 6px 10px;
                min-width: 130px;
                selection-background-color: #245778;
                selection-color: #ffffff;
            }
            QComboBox::drop-down {
                background-color: #17283a;
                border: none;
                width: 25px;
            }
            QComboBox QAbstractItemView {
                background-color: #101d2b;
                color: #eaf7ff;
                border: 1px solid #29445a;
                outline: none;
                selection-background-color: #245778;
                selection-color: #ffffff;
            }
            QComboBox QAbstractItemView::item {
                background-color: #101d2b;
                color: #eaf7ff;
                min-height: 28px;
                padding: 5px 9px;
            }
            QComboBox QAbstractItemView::item:hover,
            QComboBox QAbstractItemView::item:selected {
                background-color: #245778;
                color: #ffffff;
            }
            QPlainTextEdit {
                background: #101d2b;
                color: #eaf7ff;
                border: 1px solid #29445a;
                font-size: 12px;
                padding: 8px;
            }
        """)

        self.setObjectName("window")
        self.setMinimumSize(850, 500)
        self.resize(850, 760)
        self._expanded = False
        self._normal_geometry = None
        self._speech_process = None

        self._frame_count = 0
        self._last_fps_time = time.monotonic()
        self._last_fps_frames = 0

        panel = QWidget()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        title = QLabel("SC2 ASSISTANT")
        title.setObjectName("title")
        layout.addWidget(title)

        subtitle = QLabel(
            "ANALIZA POWTÓREK  |  EKONOMIA  |  ARMIA  |  SUPPLY"
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        self.status_label = QLabel("● Ładowanie analizy...")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        self.game_label = QLabel("Odczyt lokalnego API SC2...")
        self.game_label.setObjectName("subtitle")
        self.game_label.setWordWrap(True)
        layout.addWidget(self.game_label)

        self.resources_label = QLabel(
            "MINERAŁY: —    GAZ: —    ROBOTNICY: —    SUPPLY: —"
        )
        self.resources_label.setStyleSheet(
            "color: #71e6a1; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self.resources_label)

        controls = QHBoxLayout()

        player_label = QLabel("ANALIZOWANY GRACZ:")
        player_label.setObjectName("subtitle")
        controls.addWidget(player_label)

        self.player_combo = QComboBox()
        self.player_combo.setMinimumWidth(270)
        self.player_combo.setMaxVisibleItems(8)

        # Ciemny popup listy gracza.
        self.player_combo.setStyleSheet("""
            QComboBox {
                background-color: #17283a;
                color: #eaf7ff;
                border: 1px solid #29445a;
                border-radius: 4px;
                padding: 6px 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #101d2b;
                color: #eaf7ff;
                border: 1px solid #29445a;
                outline: 0;
                selection-background-color: #245778;
                selection-color: #ffffff;
            }
        """)
        self.player_combo.currentIndexChanged.connect(self.change_player)
        controls.addWidget(self.player_combo)

        metric_label = QLabel("WYKRES:")
        metric_label.setObjectName("subtitle")
        controls.addWidget(metric_label)

        self.metric_combo = QComboBox()
        self.metric_combo.setStyleSheet(self.player_combo.styleSheet())
        self.metric_combo.addItems(list(METRICS.keys()))
        self.metric_combo.currentTextChanged.connect(self.change_metric)
        controls.addWidget(self.metric_combo)
        controls.addStretch()
        layout.addLayout(controls)

        self.chart = ReplayChart()
        layout.addWidget(self.chart)

        insights_title = QLabel("NAJWAŻNIEJSZE WNIOSKI")
        insights_title.setObjectName("title")
        insights_title.setStyleSheet("font-size: 15px;")
        layout.addWidget(insights_title)

        self.insights_box = QPlainTextEdit()
        self.insights_box.setReadOnly(True)
        self.insights_box.setMinimumHeight(145)
        self.insights_box.setMaximumHeight(190)
        layout.addWidget(self.insights_box)

        speech_controls = QHBoxLayout()
        self.read_button = QPushButton('▶ Czytaj raport')
        self.stop_read_button = QPushButton('■ Zatrzymaj czytanie')
        self.read_button.clicked.connect(self.read_report_aloud)
        self.stop_read_button.clicked.connect(self.stop_reading)
        speech_controls.addWidget(self.read_button)
        speech_controls.addWidget(self.stop_read_button)
        speech_controls.addStretch()
        layout.addLayout(speech_controls)

        self.capture_label = QLabel("Przechwycone klatki: 0")
        self.capture_label.setObjectName("subtitle")
        layout.addWidget(self.capture_label)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(panel)

        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(
                max(area.left(), area.right() - self.width() - 20),
                area.top() + 30,
            )

        self.samples = []
        self.player_ids = []
        self.replay_path = None
        self.load_replay_report()

        self.capture = ScreenCapture(self)
        self.capture.status_changed.connect(self.on_capture_status)
        self.capture.frame_received.connect(self.on_frame_received)
        self.capture.start()

        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self.update_capture_stats)
        self.fps_timer.start(1000)

        self.api_timer = QTimer(self)
        self.api_timer.timeout.connect(self.update_game_api)
        self.api_timer.start(1000)
        self.update_game_api()

        # Co 15 sekund sprawdza, czy pojawiła się nowsza powtórka.
        self.replay_timer = QTimer(self)
        self.replay_timer.timeout.connect(self.check_new_replay)
        self.replay_timer.start(15000)

        # Dwuklik powiększa raport tylko po kliknięciu w pole z wnioskami.
        self.insights_box.viewport().installEventFilter(self)

    def load_replay_report(self):
        try:
            path, samples, players = load_latest_replay()
            player_ids = list(dict.fromkeys(
                sample["pid"] for sample in samples
            ))
            if not player_ids:
                raise ValueError("Brak graczy ze statystykami.")

            self.replay_path = path
            self.samples = samples
            self.player_ids = player_ids[:2]
            self.player_names = {
                pid: next(
                    (sample["name"] for sample in samples
                     if sample["pid"] == pid),
                    f"Gracz {pid}",
                )
                for pid in self.player_ids
            }

            configured_nick = os.environ.get(
                "SC2ASSISTANT_PLAYER", "Misiek"
            ).strip()
            target_name = configured_nick.casefold()

            detected_pid = next(
                (pid for pid in self.player_ids
                 if self.player_names[pid].strip().casefold() == target_name),
                None,
            )

            # Wypełniamy listę bez uruchamiania obsługi zmiany w trakcie ładowania.
            self.player_combo.blockSignals(True)
            self.player_combo.clear()
            self.player_combo.addItem(
                f"Mój nick ({configured_nick})", "own"
            )

            for number, pid in enumerate(self.player_ids, start=1):
                self.player_combo.addItem(
                    f"Gracz {number} — {self.player_names[pid]}", pid
                )

            # Jeśli nick istnieje w powtórce, domyślnie wybieramy go.
            # W przeciwnym razie domyślnie wybieramy Gracza 1.
            self.player_combo.setCurrentIndex(
                0 if detected_pid is not None else 1
            )
            self.player_combo.blockSignals(False)

            self.own_pid = (
                detected_pid if detected_pid is not None
                else self.player_ids[0]
            )

            names = [self.player_names[pid] for pid in self.player_ids]
            self.game_label.setText(
                f"Plik: {path.name}\\n"
                f"Gracze: {' vs '.join(names)}\\n"
                f"Próbek statystyk: {len(samples)}"
            )

            self.update_selected_player()

        except Exception as exc:
            self.player_combo.blockSignals(False)
            self.status_label.setText("● NIE UDAŁO SIĘ WCZYTAĆ POWTÓRKI")
            self.insights_box.setPlainText(
                f"{exc}\\n\\nSprawdź ścieżkę katalogu powtórek."
            )

    def change_player(self, _index):
        if not self.samples or not self.player_ids:
            return

        selected = self.player_combo.currentData()

        if selected == "own":
            target_name = os.environ.get(
                "SC2ASSISTANT_PLAYER", "Misiek"
            ).strip().casefold()

            self.own_pid = next(
                (pid for pid in self.player_ids
                 if self.player_names.get(pid, "").strip().casefold()
                 == target_name),
                None,
            )
        else:
            self.own_pid = selected

        self.update_selected_player()

    def update_selected_player(self):
        if self.own_pid is None:
            self.status_label.setText("● NIE ZNALEZIONO TWOJEGO NICKU")
            self.resources_label.setText(
                "Nick nie występuje w powtórce. Wybierz Gracza 1 lub Gracza 2."
            )
            self.insights_box.setPlainText(
                "Nie znaleziono nicku skonfigurowanego jako "
                "SC2ASSISTANT_PLAYER. Wybierz gracza z listy, aby "
                "przeanalizować jego statystyki."
            )
            self.update_chart()
            return

        own_rows = sorted(
            (sample for sample in self.samples
             if sample["pid"] == self.own_pid),
            key=lambda sample: sample["time"],
        )

        if not own_rows:
            self.status_label.setText("● BRAK STATYSTYK WYBRANEGO GRACZA")
            self.insights_box.setPlainText(
                "Ta powtórka nie zawiera statystyk wybranego gracza."
            )
            self.resources_label.setText(
                "MINERAŁY: —    GAZ: —    ROBOTNICY: —    SUPPLY: —"
            )
            self.update_chart()
            return

        name = self.player_names.get(self.own_pid, own_rows[0]["name"])
        last = own_rows[-1]

        self.resources_label.setText(
            f"{name}  |  MINERAŁY: {last['minerals']}    "
            f"GAZ: {last['gas']}    ROBOTNICY: {last['workers']}    "
            f"SUPPLY: {last['supply']:.0f}/{last['supply_cap']:.0f}"
        )

        self.status_label.setText(f"● ANALIZA GRACZA: {name}")
        insights = build_insights(self.samples, self.own_pid)
        self.insights_box.setPlainText(self.format_insights(insights))
        self.update_chart()

    def format_insights(self, insights):
        """Formatuje raport z prawdziwymi nowymi liniami."""
        sections = []

        for index, item in enumerate(insights):
            text = str(item).strip()
            if not text:
                continue

            if index == 0 and text.startswith("RAPORT ANALITYCZNY SC2"):
                text = text.replace(" | Gracz: ", "\nGracz: ")
                text = text.replace(" | Zakres próbek: ", "\nZakres próbek: ")
                sections.append(text)
                continue

            if " — " in text:
                heading, details = text.split(" — ", 1)
                heading = heading.strip()
                if heading and heading.upper() == heading:
                    sections.append(f"{heading}\n{details.strip()}")
                    continue

            sections.append(text)

        return "\n\n".join(sections)

    def read_report_aloud(self):
        """Generuje polską mowę lokalnie za pomocą Piper TTS."""
        report = self.insights_box.toPlainText().strip()
        if not report:
            QMessageBox.information(
                self, "Brak raportu", "Nie ma tekstu do przeczytania."
            )
            return

        if not shutil.which("mpv"):
            QMessageBox.warning(
                self,
                "Brak odtwarzacza",
                "Zainstaluj odtwarzacz poleceniem: sudo apt install mpv",
            )
            return

        model_path = (
            Path.home()
            / ".local/share/sc2assistant/voices/pl_PL-gosia-medium.onnx"
        )

        if not model_path.is_file():
            QMessageBox.warning(
                self,
                "Brak polskiego głosu",
                "Nie znaleziono modelu Piper. W terminalu wykonaj:\\n\\n"
                "source .venv/bin/activate\\n"
                "python -m piper.download_voices "
                "--data-dir ~/.local/share/sc2assistant/voices "
                "pl_PL-gosia-medium",
            )
            return

        self.stop_reading()
        self._tts_request_id += 1
        request_id = self._tts_request_id

        worker = PiperWorker(report, str(model_path), request_id)
        self._tts_workers.append(worker)
        worker.audio_ready.connect(self._play_generated_audio)
        worker.error_occurred.connect(self._show_tts_error)
        worker.finished.connect(self._tts_worker_finished)

        self.read_button.setEnabled(False)
        self.status_label.setText("Generuję polski głos Piper…")
        worker.start()

    def _play_generated_audio(self, audio_path, request_id):
        """Odtwarza wygenerowany plik, o ile użytkownik nie anulował żądania."""
        if request_id != self._tts_request_id:
            Path(audio_path).unlink(missing_ok=True)
            return

        self._speech_audio_path = audio_path
        try:
            self._speech_process = subprocess.Popen(
                [
                    "mpv", "--no-terminal", "--really-quiet",
                    "--no-video", audio_path
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            QTimer.singleShot(500, self._check_audio_finished)
        except OSError as exc:
            self._remove_speech_audio()
            QMessageBox.warning(
                self, "Błąd odtwarzania", f"Nie udało się uruchomić mpv: {exc}"
            )

    def _check_audio_finished(self):
        """Usuwa plik tymczasowy po zakończeniu odtwarzania."""
        process = self._speech_process
        if process is not None and process.poll() is None:
            QTimer.singleShot(500, self._check_audio_finished)
            return

        self._speech_process = None
        self._remove_speech_audio()

    def _remove_speech_audio(self):
        path = self._speech_audio_path
        self._speech_audio_path = None
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _show_tts_error(self, message, request_id):
        if request_id != self._tts_request_id:
            return
        QMessageBox.warning(self, "Piper TTS", message)

    def _tts_worker_finished(self):
        worker = self.sender()
        if worker in self._tts_workers:
            self._tts_workers.remove(worker)
        self.read_button.setEnabled(True)

    def stop_reading(self):
        """Zatrzymuje generowanie mowy oraz odtwarzanie."""
        self._tts_request_id += 1

        for worker in list(self._tts_workers):
            worker.stop()

        process = self._speech_process
        self._speech_process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass

        self._remove_speech_audio()


    def toggle_expanded(self):
        """Rozszerza okno raportu i przywraca jego poprzedni rozmiar."""
        if not self._expanded:
            self._normal_geometry = self.geometry()
            self._expanded = True

            self.insights_box.setMinimumHeight(260)
            self.insights_box.setMaximumHeight(16777215)
            self.showMaximized()
        else:
            self._expanded = False

            self.insights_box.setMinimumHeight(145)
            self.insights_box.setMaximumHeight(190)

            self.showNormal()

            if self._normal_geometry is not None:
                geometry = self._normal_geometry
                QTimer.singleShot(
                    0, lambda g=geometry: self.setGeometry(g)
                )

    def eventFilter(self, watched, event):
        if (
            watched is self.insights_box.viewport()
            and event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.toggle_expanded()
            return True

        return super().eventFilter(watched, event)

    def check_new_replay(self):
        try:
            from src.replay_analysis import REPLAY_DIR
            files = sorted(
                REPLAY_DIR.glob("*.SC2Replay"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files and files[0] != self.replay_path:
                self.load_replay_report()
        except Exception:
            pass

    def update_chart(self):
        selected_ids = self.player_ids
        self.chart.set_data(
            self.samples,
            selected_ids,
            self.metric_combo.currentText(),
        )

    def change_metric(self, metric):
        if self.samples:
            selected_ids = self.player_ids
            self.chart.set_data(self.samples, selected_ids, metric)

    def update_game_api(self):
        data, error = get_game_info()

        if error or data is None:
            self.game_label.setStyleSheet("color: #ff7777;")
            if self.replay_path is None:
                self.game_label.setText(
                    "API SC2: brak odpowiedzi na porcie 6119"
                )
            return

        if self.replay_path is not None:
            self.game_label.setStyleSheet("color: #8da9bd;")
            # Nie zastępuj informacji o analizowanej powtórce.
            return

        players = data.get("players") or []
        seconds = max(0, int(data.get("displayTime", 0)))
        minutes, seconds = divmod(seconds, 60)
        game_time = f"{minutes:02d}:{seconds:02d}"
        names = [
            f"{p.get('name', 'Nieznany')} ({p.get('race', '?')})"
            for p in players
        ]
        self.game_label.setText(
            f"Czas gry: {game_time}\n" + " vs ".join(names)
        )

    def on_capture_status(self, message: str):
        if message.startswith("Błąd"):
            self.status_label.setText(f"● {message}")

    def on_frame_received(self, width: int, height: int, frame: bytes):
        self._frame_count += 1

    def update_capture_stats(self):
        now = time.monotonic()
        elapsed = now - self._last_fps_time
        if elapsed <= 0:
            return

        new_frames = self._frame_count - self._last_fps_frames
        fps = new_frames / elapsed
        self.capture_label.setText(
            f"Przechwycone klatki: {self._frame_count} | {fps:.1f} FPS"
        )
        self._last_fps_time = now
        self._last_fps_frames = self._frame_count

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if hasattr(self, "_drag_position"):
                self.move(
                    event.globalPosition().toPoint() - self._drag_position
                )
                event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self.stop_reading()
        if hasattr(self, "capture") and self.capture.isRunning():
            self.capture.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    overlay = SC2Overlay()
    overlay.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
