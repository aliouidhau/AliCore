def build_report(parts):
    # parts: [{"name": ..., "unit": 单件体积, "quantity": 数量}]
    # TODO: 返回按总体积从大到小排序的列表，每项含 name/total
    return []


print(build_report([
    {"name": "底座", "unit": 40000, "quantity": 2},
    {"name": "支架", "unit": 57600, "quantity": 1},
]))
