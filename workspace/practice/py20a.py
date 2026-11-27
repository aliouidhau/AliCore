def find_part(parts, key):
    return parts.get(key)


def safe_label(parts, key):
    name = find_part(parts, key)
    return name.upper()


print(safe_label({"底座": "base"}, "轴承"))
