def require_int(value, name="参数"):
    # TODO: 不是整数就抛 TypeError，是整数就返回它
    return value


print(require_int(3))
print(require_int("3"))
