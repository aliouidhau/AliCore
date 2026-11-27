parts = [
    {"name": "轴承", "qty": 12},
    {"name": "齿轮", "qty": 8},
    {"name": "电机", "qty": 15},
    {"name": "皮带", "qty": 6},
    {"name": "螺栓", "qty": 16},
]


def total_qty(items):
    total = 0
    for i in range(len(items) - 1):
        total += items[i]["qty"]
    return total


print(total_qty(parts))
