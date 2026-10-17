from src.alerts import get_alerts
from src.game_state import GameState


def test_no_alerts_when_game_is_inactive():
    state = GameState(
        minerals=2000,
        vespene=1000,
        workers=5,
        supply_used=30,
        supply_cap=31,
        is_game_active=False,
    )

    assert get_alerts(state) == []


def test_supply_alert():
    state = GameState(
        supply_used=30,
        supply_cap=31,
        is_game_active=True,
    )

    alerts = get_alerts(state)

    assert any("SUPPLY" in alert for alert in alerts)


def test_mineral_alert():
    state = GameState(minerals=1200, is_game_active=True)

    alerts = get_alerts(state)

    assert any("minerałów" in alert for alert in alerts)


def test_no_alerts_with_healthy_economy():
    state = GameState(
        minerals=300,
        vespene=100,
        workers=25,
        supply_used=20,
        supply_cap=30,
        idle_production_buildings=0,
        is_game_active=True,
    )

    assert get_alerts(state) == []
