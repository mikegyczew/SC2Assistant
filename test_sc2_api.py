import asyncio

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer


class SC2ApiTest(BotAI):
    async def on_step(self, iteration: int):
        # Odczyt rzeczywistych danych z obserwacji API StarCraft II.
        print(
            f"[SC2 API] klatka={iteration} | "
            f"minerały={self.minerals} | "
            f"gaz={self.vespene} | "
            f"robotnicy={self.workers.amount} | "
            f"zaopatrzenie={self.supply_used}/{self.supply_cap}",
            flush=True,
        )

        # Po kilku odczytach kończymy test, żeby nie zostawić
        # niepotrzebnie uruchomionego meczu.
        if iteration >= 10:
            await self.client.leave()


def main():
    print("Uruchamiam test API SC2 przeciwko AI...", flush=True)

    result = run_game(
        maps.get("Simple64"),
        [
            Bot(Race.Terran, SC2ApiTest()),
            Computer(Race.Zerg, Difficulty.VeryEasy),
        ],
        realtime=True,
    )

    print(f"\nTest zakończony. Wynik meczu: {result}", flush=True)


if __name__ == "__main__":
    main()
