# ============================================================
# 第 15 课：迭代器与生成器——让数据“一个一个来”
# ============================================================
# 【本课目标】
# 1. 理解可迭代对象、迭代器、生成器的区别。
# 2. 会用 __iter__ / __next__ 写一个自己的迭代器。
# 3. 会用 yield 写生成器，并知道它为什么省内存。
#
# 【心智模型】
# 列表像一箱已经做好的零件，全部摆在桌上。
# 生成器像一条传送带：你需要一个，它才生产一个。
# 数据很大、或者要边读边算时，传送带更合适。

# ------------------------------------------------------------
# 一、for 循环背后发生了什么？
# ------------------------------------------------------------
# for 会先调用 iter(对象)，拿到一个“迭代器”，
# 然后不断调用 next(迭代器)，直到抛出 StopIteration。

numbers = [10, 20, 30]
iterator = iter(numbers)
print("第一次 next：", next(iterator))
print("第二次 next：", next(iterator))
print("第三次 next：", next(iterator))
try:
    next(iterator)
except StopIteration:
    print("第四次 next：没有更多数据了")

# ------------------------------------------------------------
# 二、自己写一个迭代器
# ------------------------------------------------------------
# 迭代器必须实现 __iter__ 和 __next__。
# __iter__ 返回自己；__next__ 返回下一个值，取完时抛 StopIteration。

class Countdown:
    def __init__(self, start):
        self.current = start

    def __iter__(self):
        return self

    def __next__(self):
        if self.current <= 0:
            raise StopIteration
        value = self.current
        self.current -= 1
        return value


print("\n自定义倒计时：")
for value in Countdown(5):
    print(value, end=" ")
print()

# 迭代器只能往前走，不能回头。
countdown = Countdown(3)
print("第一次遍历：", list(countdown))
print("第二次遍历：", list(countdown))  # 已经耗尽，结果是 []

# ------------------------------------------------------------
# 三、生成器：写起来更短的迭代器
# ------------------------------------------------------------
# 函数里出现 yield，它就不再是普通函数，而是生成器函数。
# 调用时不会立刻执行，而是返回一个生成器对象。
# 每次 next 会运行到下一个 yield，然后暂停并保留现场。

def countdown_generator(start):
    while start > 0:
        yield start
        start -= 1


gen = countdown_generator(5)
print("\n生成器倒计时：", list(gen))

# 生成器同样是一次性的。
gen = countdown_generator(3)
print("第一次：", list(gen))
print("第二次：", list(gen))

# ------------------------------------------------------------
# 四、生成器为什么省内存？
# ------------------------------------------------------------
# 下面这个函数不会一次生成一百万个整数，
# 它只在循环需要时，才算出当前那个数。

def big_numbers(limit):
    current = 1
    while current <= limit:
        yield current
        current += 1


import itertools

first_ten = itertools.islice(big_numbers(1_000_000), 10)
print("\n只取前 10 个：", list(first_ten))

# ------------------------------------------------------------
# 五、生成器管道：过滤、转换、汇总
# ------------------------------------------------------------
# 生成器可以串起来：上一步吐出的数据，成为下一步的输入。
# 每一步都只在需要时工作。

def measured_parts():
    yield ("底座", 80, 50, 10)
    yield ("支架", 120, 60, 8)
    yield ("垫块", 30, 30, 5)
    yield ("坏件", 0, 30, 5)


def valid_parts(parts):
    for name, length, width, height in parts:
        if min(length, width, height) > 0:
            yield name, length * width * height


def build_report(parts):
    for name, volume in parts:
        yield f"{name}: {volume} mm³"


report = build_report(valid_parts(measured_parts()))
for line in report:
    print(line)

# ------------------------------------------------------------
# 六、常见错误
# ------------------------------------------------------------
# 1. 把生成器当成列表反复使用。它取完就空了。
# 2. 在生成器函数里用 return 返回一个值，调用者拿到的是 StopIteration，
#    不是那个返回值。
# 3. 忘记 yield 会让函数变成普通函数，调用时什么都不会发生。

def mistaken_generator():
    print("这行会执行，但函数没有 yield")
    return [1, 2, 3]


print("\n误写成普通函数的返回值：", mistaken_generator())

# ------------------------------------------------------------
# 七、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 写一个生成器 even_numbers(limit)，只吐偶数。
# 2. 用 itertools.islice 取前 5 个。
# 3. 写一个生成器读取一段多行文本，每次 yield 一行。
#
# 参考思路：
def even_numbers(limit):
    for number in range(2, limit + 1, 2):
        yield number


print("练习：前 5 个偶数 =", list(itertools.islice(even_numbers(20), 5)))

print("\n第 15 课结束：数据不必一次全部搬进内存。")
