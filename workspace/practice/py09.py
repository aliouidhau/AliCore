def add_item(item, cart=[]):
    cart.append(item)
    return cart


print(add_item("轴承"))
print(add_item("齿轮"))
