"""Client for the local StarCraft II game-information API."""

import json
from urllib.error import URLError
from urllib.request import Request, urlopen


API_URL = "http://127.0.0.1:6119"


def get_game_info() -> tuple[dict | None, str | None]:
    """Return game metadata from the local SC2 client API."""
    request = Request(
        f"{API_URL}/game",
        headers={"Accept": "application/json"},
    )

    try:
        with urlopen(request, timeout=1.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data, None
    except (URLError, TimeoutError, OSError) as exc:
        return None, str(exc)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"Nieprawidłowa odpowiedź API: {exc}"
