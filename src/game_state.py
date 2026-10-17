from dataclasses import dataclass


@dataclass
class GameState:
    """Snapshot of the player's current StarCraft II game state."""

    minerals: int = 0
    vespene: int = 0
    workers: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    idle_production_buildings: int = 0
    is_game_active: bool = False
