def calc(chinese, math, english):
    total = chinese + math + english
    return {"total": total, "average": total / 4.0}


print(calc(86, 92, 78))
