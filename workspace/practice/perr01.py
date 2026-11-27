parts = [
    {"name": "轴承", "qty": 12},
    {"name": "齿轮", "qty": 8},
]


def total_qty(items):
    total = 0
    for i in range(len(items) + 1):
        total += items[i]["qty"]
    return total


print(total_qty(parts))
