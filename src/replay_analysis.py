from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import QWidget

import sc2reader


REPLAY_DIR = (
    Path.home()
    / "Games/battlenet/drive_c/users/steamuser/Documents/"
    / "StarCraft II/Accounts/136808857/2-S2-1-3378640/"
    / "Replays/Multiplayer"
)

METRICS = {
    "Minerały": ("minerals", "Minerały", 0),
    "Gaz": ("gas", "Gaz", 0),
    "Robotnicy": ("workers", "Robotnicy", 0),
    "Armia": ("army", "Wartość aktywnej armii", 0),
    "Supply": ("supply", "Wykorzystany supply", 1),
}


def load_latest_replay():
    """Wczytuje najnowszą powtórkę i zbiera próbki statystyk obu graczy."""
    files = sorted(
        REPLAY_DIR.glob("*.SC2Replay"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(f"Nie znaleziono powtórek w: {REPLAY_DIR}")

    path = files[0]
    replay = sc2reader.load_replay(str(path), load_level=4)

    players = {}
    replay_players = getattr(replay, "players", [])
    if isinstance(replay_players, dict):
        replay_players = replay_players.values()

    for player in replay_players:
        pid = getattr(player, "pid", None)
        if pid is not None:
            players[pid] = getattr(player, "name", f"Gracz {pid}")

    samples = []
    for event in replay.events:
        if type(event).__name__ != "PlayerStatsEvent":
            continue

        pid = getattr(event, "pid", None)
        stats = getattr(event, "stats", {})

        if pid is None:
            continue

        samples.append({
            "time": max(0, int(getattr(event, "second", 0))),
            "pid": pid,
            "name": players.get(pid, f"Gracz {pid}"),
            "minerals": max(0, int(getattr(event, "minerals_current", 0))),
            "gas": max(0, int(getattr(event, "vespene_current", 0))),
            "workers": max(0, int(getattr(event, "workers_active_count", 0))),
            "army": max(0, int(getattr(event, "minerals_used_active_forces", 0))),
            "supply": max(0, float(getattr(event, "food_used", 0))),
            "supply_cap": max(0, float(getattr(event, "food_made", 0))),
        })

    if not samples:
        raise ValueError("Powtórka nie zawiera zdarzeń PlayerStatsEvent.")

    samples.sort(key=lambda s: s["time"])
    return path, samples, players


def build_insights(samples, own_pid):
    """
    Generuje komentarz analityczny na podstawie statystyk z powtórki.
    Wnioski opisują obserwacje i hipotezy, nie przypisują niepotwierdzonych
    przyczyn zdarzeniom w grze.
    """
    if own_pid is None:
        return [
            "IDENTYFIKACJA GRACZA | Nie ustalono profilu analizowanego gracza. "
            "Ustaw SC2ASSISTANT_PLAYER na nick występujący w powtórce, "
            "a następnie uruchom aplikację ponownie."
        ]

    own = sorted(
        (s for s in samples if s["pid"] == own_pid),
        key=lambda s: s["time"],
    )

    if len(own) < 2:
        return [
            "NIEWYSTARCZAJĄCE DANE | Powtórka zawiera zbyt mało próbek "
            "statystycznych, aby wiarygodnie ocenić przebieg rozwoju "
            "ekonomii i armii."
        ]

    name = own[0]["name"]
    result = []

    def fmt_time(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def duration_text(seconds):
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} s"
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes} min {remainder:02d} s"

    def longest_run(predicate):
        best, current = [], []
        for sample in own:
            if predicate(sample):
                current.append(sample)
            else:
                if len(current) > len(best):
                    best = current
                current = []
        if len(current) > len(best):
            best = current
        return best

    def add(priority, category, message):
        result.append({
            "priority": priority,
            "category": category,
            "message": message,
        })

    first_time = own[0]["time"]
    last_time = own[-1]["time"]
    match_duration = max(0, last_time - first_time)

    # 1. Ekonomia: trwały zapas minerałów.
    mineral_run = longest_run(lambda s: s["minerals"] >= 700)
    if len(mineral_run) >= 3:
        duration = mineral_run[-1]["time"] - mineral_run[0]["time"]
        peak = max(s["minerals"] for s in mineral_run)
        average = sum(s["minerals"] for s in mineral_run) / len(mineral_run)

        if duration >= 20:
            add(
                1,
                "MAKROEKONOMIA",
                f"[{fmt_time(mineral_run[0]['time'])}–"
                f"{fmt_time(mineral_run[-1]['time'])}] "
                f"PRZEKŁADANIE EKONOMII NA TEMPO GRY. "
                f"Zapas minerałów utrzymywał się na poziomie co najmniej "
                f"700 przez {duration_text(duration)}; maksimum wyniosło "
                f"{peak}, a średnia w tym okresie około {average:.0f}. "
                f"To sygnał do sprawdzenia, czy gospodarka była skutecznie "
                f"zamieniana na robotników, jednostki, budynki produkcyjne "
                f"lub technologię. Zweryfikuj kolejkę produkcji i dostępne "
                f"możliwości wydawania zasobów. Sam wysoki zapas nie dowodzi "
                f"przestoju produkcji — może wynikać również z planowanej "
                f"inwestycji lub ograniczeń technologicznych."
            )

    # 2. Gaz: interpretacja zależna od planu strategicznego.
    gas_run = longest_run(lambda s: s["gas"] >= 300)
    if len(gas_run) >= 3:
        duration = gas_run[-1]["time"] - gas_run[0]["time"]
        peak = max(s["gas"] for s in gas_run)

        if duration >= 20:
            add(
                2,
                "ZARZĄDZANIE GAZEM",
                f"[{fmt_time(gas_run[0]['time'])}–"
                f"{fmt_time(gas_run[-1]['time'])}] "
                f"KAPITAŁ TECHNOLOGICZNY POZOSTAJE NIEWYKORZYSTANY. "
                f"Zapas gazu utrzymywał się na poziomie co najmniej 300 "
                f"przez {duration_text(duration)}; szczyt wyniósł {peak}. "
                f"Sprawdź, czy poziom wydobycia odpowiada planowanej "
                f"kompozycji armii, ulepszeniom i ścieżce technicznej. "
                f"Jeśli brakuje jednostek wymagających gazu, rozważ "
                f"wcześniejsze uruchomienie odpowiedniej produkcji. "
                f"Jeżeli przygotowujesz technologię lub konkretny timing, "
                f"wysoki zapas może być celowy."
            )

    # 3. Supply: wykrywanie próbek bliskich limitowi.
    supply_run = longest_run(
        lambda s: s["supply_cap"] > 0
        and s["supply_cap"] - s["supply"] < 1
    )

    if len(supply_run) >= 2:
        duration = supply_run[-1]["time"] - supply_run[0]["time"]
        max_used = max(s["supply"] for s in supply_run)
        max_cap = max(s["supply_cap"] for s in supply_run)

        add(
            1,
            "TEMPO PRODUKCJI",
            f"[około {fmt_time(supply_run[0]['time'])}] "
            f"RYZYKO BLOKADY ZAOPATRZENIA. Próbki wskazują, że wykorzystanie "
            f"supply zbliżyło się do dostępnego limitu. Wykorzystanie "
            f"wynosiło maksymalnie {max_used:g}, przy limicie dochodzącym "
            f"do {max_cap:g}."
            + (
                f" Stan utrzymywał się przez około {duration_text(duration)}."
                if duration else ""
            )
            + " Sprawdź, czy dodatkowy supply został przygotowany "
            "odpowiednio wcześnie. Nawet krótka blokada może opóźnić "
            "produkcję i osłabić timing, ale próbki statystyczne nie "
            "pozwalają ustalić dokładnego początku ani końca blokady."
        )

    # 4. Robotnicy: nagły spadek aktywnej populacji ekonomicznej.
    worker_events = []
    for previous, current in zip(own, own[1:]):
        gap = current["time"] - previous["time"]
        drop = previous["workers"] - current["workers"]

        if drop >= 4 and 0 < gap <= 20:
            worker_events.append((previous, current, drop, gap))

    if worker_events:
        previous, current, drop, gap = max(
            worker_events,
            key=lambda item: item[2],
        )

        add(
            1,
            "BEZPIECZEŃSTWO EKONOMII",
            f"[{fmt_time(previous['time'])}–{fmt_time(current['time'])}] "
            f"ISTOTNY SPADEK LICZBY ROBOTNIKÓW. Liczba aktywnych "
            f"robotników zmniejszyła się z {previous['workers']} do "
            f"{current['workers']}, czyli o {drop}, w ciągu około "
            f"{duration_text(gap)}. To potencjalnie istotny sygnał "
            f"utraty zdolności wydobywczych. Obejrzyj ten fragment "
            f"powtórki i sprawdź, czy doszło do presji na bazę, strat "
            f"robotników albo innej zmiany gospodarczej. Same próbki "
            f"nie ujawniają przyczyny spadku."
        )

    # 5. Wartość aktywnej armii: analiza zmian, bez utożsamiania jej
    # z pełną siłą bojową lub bilansem wszystkich zasobów.
    army_changes = []
    for previous, current in zip(own, own[1:]):
        gap = current["time"] - previous["time"]
        if gap <= 0 or gap > 30:
            continue

        before = previous["army"]
        after = current["army"]

        if before >= 300 and after <= before * 0.70:
            army_changes.append((previous, current, before - after, gap))

    if army_changes:
        previous, current, drop, gap = max(
            army_changes,
            key=lambda item: item[2],
        )

        add(
            1,
            "ZARZĄDZANIE ARMIĄ",
            f"[{fmt_time(previous['time'])}–{fmt_time(current['time'])}] "
            f"WYRAŹNY SPADEK WSKAŹNIKA AKTYWNEJ ARMII. Wartość pola "
            f"statystycznego aktywnych sił zmniejszyła się z "
            f"{previous['army']} do {current['army']} "
            f"(różnica {drop}) w ciągu {duration_text(gap)}. "
            f"To punkt do weryfikacji w powtórce: sprawdź, czy zmiana "
            f"zbiega się ze starciem, stratami lub przejściem do innej "
            f"kompozycji. Wskaźnik nie opisuje samodzielnie całkowitej "
            f"siły bojowej, jakości jednostek ani korzystności wymiany."
        )

    # 6. Ocena trendu ekonomicznego na początku i końcu próbek.
    if match_duration >= 60:
        early_count = max(1, len(own) // 5)
        early = own[:early_count]
        late = own[-early_count:]

        early_workers = sum(s["workers"] for s in early) / len(early)
        late_workers = sum(s["workers"] for s in late) / len(late)

        if early_workers > 0:
            worker_change = (late_workers - early_workers) / early_workers * 100

            if worker_change <= -20:
                add(
                    2,
                    "ROZWÓJ GOSPODARKI",
                    f"[{fmt_time(first_time)}–{fmt_time(last_time)}] "
                    f"SPADEK AKTYWNEJ POPULACJI EKONOMICZNEJ. Średnia "
                    f"liczba aktywnych robotników w końcowej części próbek "
                    f"była o około {abs(worker_change):.0f}% niższa niż "
                    f"w początkowej. Może to wynikać ze strat, zmian "
                    f"w strukturze gospodarki lub zakończenia pracy części "
                    f"robotników. Sprawdź, czy po ewentualnych stratach "
                    f"nastąpiła odbudowa ekonomii. Ten trend sam w sobie "
                    f"nie przesądza o błędzie strategicznym."
                )

    # Jeżeli nie znaleziono istotnych sygnałów, nie ogłaszamy,
    # że gra była dobra — tylko że obecne reguły nie wykryły problemu.
    if not result:
        add(
            3,
            "PODSUMOWANIE ANALITYCZNE",
            f"[{fmt_time(first_time)}–{fmt_time(last_time)}] "
            f"Nie wykryto trwałych odchyleń objętych obecnymi regułami. "
            f"To nie jest ocena jakości całego meczu. Aktualna analiza "
            f"opiera się na próbkach statystyk i nie ocenia jeszcze "
            f"bezpośrednio build orderu, scoutingu, pozycji armii, "
            f"wyborów taktycznych ani skuteczności poszczególnych starć. "
            f"Te elementy wymagają analizy dodatkowych zdarzeń z powtórki."
        )

    # Najpierw najistotniejsze sygnały, maksymalnie sześć komentarzy.
    result.sort(key=lambda item: item["priority"])

    header = (
        f"RAPORT ANALITYCZNY SC2 | Gracz: {name} | "
        f"Zakres próbek: {fmt_time(first_time)}–{fmt_time(last_time)}"
    )

    return [header] + [
        f"{item['category']} — {item['message']}"
        for item in result[:5]
    ]


class ReplayChart(QWidget):
    """Prosty wykres liniowy: próbki gracza i przeciwnika w czasie."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = []
        self.player_ids = []
        self.selected_metric = "Minerały"
        self.setMinimumHeight(340)

    def set_data(self, samples, player_ids, metric):
        self.samples = samples
        self.player_ids = player_ids
        self.selected_metric = metric
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1420"))

        painter.setPen(QColor("#eaf7ff"))
        painter.setFont(QFont("Sans Serif", 12, QFont.Weight.Bold))
        painter.drawText(18, 27, f"{self.selected_metric} — przebieg meczu")

        if not self.samples or not self.player_ids:
            painter.setPen(QColor("#8da9bd"))
            painter.drawText(18, 65, "Wczytaj powtórkę, aby wyświetlić wykres.")
            return

        key, label, decimals = METRICS.get(
            self.selected_metric, METRICS["Minerały"]
        )

        left, right = 65, max(100, self.width() - 25)
        top, bottom = 55, max(120, self.height() - 45)
        plot_w = right - left
        plot_h = bottom - top

        max_time = max(s["time"] for s in self.samples)
        max_time = max(max_time, 1)

        series = []
        for index, pid in enumerate(self.player_ids[:2]):
            rows = sorted(
                (s for s in self.samples if s["pid"] == pid),
                key=lambda s: s["time"],
            )
            if not rows:
                continue

            values = [(s["time"], s[key]) for s in rows]
            series.append((pid, rows[0]["name"], values, index))

        max_value = max(
            (value for _, _, values, _ in series for _, value in values),
            default=1,
        )
        max_value = max(max_value, 1)

        # Siatka i osie.
        painter.setPen(QPen(QColor("#29445a"), 1))
        for i in range(5):
            y = bottom - int(plot_h * i / 4)
            painter.drawLine(left, y, right, y)
            value = max_value * i / 4
            painter.setPen(QColor("#8da9bd"))
            painter.drawText(5, y + 4, f"{value:.0f}")
            painter.setPen(QPen(QColor("#29445a"), 1))

        for i in range(6):
            x = left + int(plot_w * i / 5)
            painter.drawLine(x, top, x, bottom)
            sec = int(max_time * i / 5)
            painter.setPen(QColor("#8da9bd"))
            painter.drawText(
                x - 18, bottom + 22, f"{sec // 60:02d}:{sec % 60:02d}"
            )
            painter.setPen(QPen(QColor("#29445a"), 1))

        # Dwie linie graczy.
        line_colors = [QColor("#32c8ff"), QColor("#ffb454")]
        legend_x = left

        for pid, name, values, index in series:
            color = line_colors[index]
            painter.setPen(QPen(color, 2.5))
            previous = None

            for sec, value in values:
                x = left + int(plot_w * sec / max_time)
                y = bottom - int(plot_h * value / max_value)
                if previous is not None:
                    painter.drawLine(previous[0], previous[1], x, y)
                painter.setBrush(color)
                painter.drawEllipse(x - 2, y - 2, 4, 4)
                previous = (x, y)

            painter.setPen(color)
            painter.drawText(legend_x, self.height() - 8, name)
            legend_x += min(260, max(120, len(name) * 9 + 35))

        painter.end()
