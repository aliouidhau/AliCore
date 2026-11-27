# ============================================================
# 第 17 课：装饰器——不改原函数，给它加一层能力
# ============================================================
# 【本课目标】
# 1. 理解函数也是对象，可以被传来传去。
# 2. 会写不带参数和带参数的装饰器。
# 3. 知道 functools.wraps 为什么重要。
#
# 【心智模型】
# 装饰器像给工具加一个外壳：
# 工具本身还是那把工具，但外壳可以记录使用时间、打印日志、做权限检查。

import functools
import time

# ------------------------------------------------------------
# 一、函数是对象
# ------------------------------------------------------------
def greet(name):
    return f"你好，{name}"


print("直接调用：", greet("阿码"))

# 函数可以赋值给变量。
speak = greet
print("通过变量调用：", speak("阿码"))

# 函数可以作为参数传给另一个函数。
def call_twice(function, value):
    return function(value), function(value)


print("调用两次：", call_twice(greet, "阿码"))

# ------------------------------------------------------------
# 二、最简单装饰器
# ------------------------------------------------------------
# 装饰器本质是一个函数：
# 它接收原函数，返回一个“新的函数”。
# @decorator 是语法糖，等价于 original = decorator(original)。

def announce(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        print(f"[开始] {function.__name__}")
        result = function(*args, **kwargs)
        print(f"[结束] {function.__name__}")
        return result

    return wrapper


@announce
def calculate_area(length, width):
    return length * width


print("\n带装饰器的函数：")
area = calculate_area(80, 50)
print("面积：", area)
print("函数名仍然是：", calculate_area.__name__)

# ------------------------------------------------------------
# 三、装饰器记录耗时
# ------------------------------------------------------------
# 这里用一个空转循环制造一点可测的时间。
# 实际项目里，耗时可能来自文件、数据库或网络。

def timed(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        result = function(*args, **kwargs)
        elapsed = time.perf_counter() - started
        print(f"{function.__name__} 用时 {elapsed:.4f} 秒")
        return result

    return wrapper


@timed
def busy_work():
    total = 0
    for number in range(300_000):
        total += number
    return total


print("\n计时：", busy_work())

# ------------------------------------------------------------
# 四、带参数的装饰器
# ------------------------------------------------------------
# 带参数的装饰器要再套一层：最外层接收参数，里面才接收函数。
# 这样写起来像 @repeat(3)，而不是 @repeat。

def repeat(times):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            result = None
            for _ in range(times):
                result = function(*args, **kwargs)
            return result

        return wrapper

    return decorator


@repeat(3)
def say_hi(name):
    print(f"  hi, {name}")
    return name


print("\n重复执行：")
print("返回值：", say_hi("阿码"))

# ------------------------------------------------------------
# 五、装饰器做参数检查
# ------------------------------------------------------------
# 装饰器不只会打印，也可以拦截非法输入。
# 这里把“长度必须为正”的检查从业务函数里抽出来。

def positive_arguments(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        for value in args:
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{function.__name__} 只接受正数，收到 {value}")
        return function(*args, **kwargs)

    return wrapper


@positive_arguments
def make_box_volume(length, width, height):
    return length * width * height


print("\n合法调用：", make_box_volume(10, 20, 5))
try:
    make_box_volume(-10, 20, 5)
except ValueError as error:
    print("非法调用被拦截：", error)

# ------------------------------------------------------------
# 六、常见错误
# ------------------------------------------------------------
# 1. 忘记在 wrapper 里 return function(...)，原函数的结果会变成 None。
# 2. 忘记 *args / **kwargs，原函数一有参数就报 TypeError。
# 3. 不写 functools.wraps，函数名和文档会变成 wrapper。
# 4. 装饰器里写死参数，导致只能装饰某一种函数。
#
# 调试建议：先写普通 wrapper，确认能调用原函数，再加 @。

# ------------------------------------------------------------
# 七、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 写一个 @log_calls，打印函数名和参数。
# 2. 写一个 @retry(2)，函数失败时最多重试两次。
# 3. 给 make_box_volume 再套一层 @timed，观察两个装饰器的顺序。
#
# 参考顺序：
# @timed
# @positive_arguments
# def ...
# 最靠近函数的装饰器先包住函数。
print("\n第 17 课结束：装饰器不是魔法，是函数套函数。")
