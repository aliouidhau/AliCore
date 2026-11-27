def subtotal(items):
    return sum(price * qty for price, qty in items)


def discount_rate(total):
    if total >= 100:
        return 0.1
    return 0


def final_total(items):
    total = subtotal(items)
    return total


print(final_total([(20, 3), (10, 5)]))
