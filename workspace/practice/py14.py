def make_report(items):
    total = ""
    lines = []
    for name, qty in items:
        total += str(qty)
        lines.append(f"{name}: {qty}")
    lines.append(f"合计: {total}")
    return "\n".join(lines)


print(make_report([("轴承", 10), ("齿轮", 8)]))
