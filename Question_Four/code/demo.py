"""Small synthetic smoke test; no attachment or workbook access."""

from datetime import date, timedelta

import numpy as np

from .model import DayData, run_strategy
from .price_stress import stress_test


def synthetic_day(day: date, load: float, price: float) -> DayData:
    return DayData(
        day=day,
        load_kwh=np.full(144, load),
        pv_kwh=np.zeros(144),
        price=np.full(144, price),
        pv_forecast_kw=np.zeros((4, 24)),
    )


def main() -> None:
    days = [
        synthetic_day(
            date(2025, 1, 1) + timedelta(days=i),
            100.0 + 20.0 * i,
            (1.2, 1.5, 1.0)[i],
        )
        for i in range(3)
    ]
    for strategy in ("4-2", "4-3"):
        results = run_strategy(days, strategy)
        last = results[-1]
        stress = stress_test(last, days[:-1], 6.0, actual_price=days[-1].price)
        print(
            f"{strategy}: cost={last.total_cost:.2f}, end_soc={last.soc[-1]:.2f}, "
            f"stress_cost={stress.worst_cost:.2f}"
        )


if __name__ == "__main__":
    main()
