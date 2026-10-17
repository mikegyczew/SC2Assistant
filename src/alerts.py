from src.game_state import GameState


def get_alerts(state: GameState) -> list[str]:
    """Generate macro alerts from a game-state snapshot."""
    alerts: list[str] = []

    if not state.is_game_active:
        return alerts

    free_supply = state.supply_cap - state.supply_used

    if state.supply_cap > 0 and free_supply <= 2:
        alerts.append("SUPPLY: Brakuje wolnego supply! Przygotuj kolejny budynek.")

    if state.minerals >= 1000:
        alerts.append("EKONOMIA: Masz dużo niewydanych minerałów.")

    if state.vespene >= 700:
        alerts.append("EKONOMIA: Masz dużo niewydanego gazu.")

    if state.workers < 20:
        alerts.append("MAKRO: Masz mało robotników — sprawdź produkcję.")

    if state.idle_production_buildings > 0:
        alerts.append(
            f"PRODUKCJA: {state.idle_production_buildings} budynków produkcyjnych "
            "może być bezczynnych."
        )

    return alerts
