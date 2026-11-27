stock = {"轴承": 12, "齿轮": 8}


def get_qty(stock, name):
    return stock[name]


print(get_qty(stock, "垫圈"))
