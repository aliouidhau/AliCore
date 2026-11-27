# ============================================================
# 第 18 课：上下文管理器——把“开始”和“收尾”绑在一起
# ============================================================
# 【本课目标】
# 1. 理解 with 语句在做什么。
# 2. 会用 __enter__ / __exit__ 写自己的上下文管理器。
# 3. 会用 contextlib.contextmanager 简化写法。
#
# 【心智模型】
# 上下文管理器像一套安全流程：
# 进入时打开资源，离开时无论成功失败都负责收尾。
# 文件、锁、数据库连接、计时器，都适合这样管理。

from contextlib import contextmanager, suppress
from pathlib import Path
import tempfile
import time

# ------------------------------------------------------------
# 一、with open：最熟悉的上下文管理器
# ------------------------------------------------------------
# 不用 with 时，你必须记得 close。
# 一旦中间抛异常，close 可能被跳过。
# with 会在退出代码块时自动关闭文件，即使发生异常。

with tempfile.TemporaryDirectory() as temp_dir:
    path = Path(temp_dir) / "parts.txt"
    path.write_text("底座,80,50,10\n支架,120,60,8\n", encoding="utf-8")

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            print("读到：", line.strip())

    print("离开 with 后，文件已经关闭。")

# ------------------------------------------------------------
# 二、自己写一个上下文管理器
# ------------------------------------------------------------
# 对象要实现两个方法：
# __enter__：进入 with 时调用，返回值会赋给 as 后面的变量。
# __exit__：离开 with 时调用；返回 True 会吞掉异常，返回 False 会继续抛出。

class Timer:
    def __init__(self, label):
        self.label = label
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        print(f"[开始] {self.label}")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        elapsed = time.perf_counter() - self.started
        print(f"[结束] {self.label}，用时 {elapsed:.4f} 秒")
        if exc_type is not None:
            print(f"[异常] {exc_type.__name__}: {exc_value}")
        return False  # 不吞掉异常，让调用者继续处理


with Timer("计算零件体积"):
    total = 0
    for number in range(200_000):
        total += number

print("计算结果：", total)

# ------------------------------------------------------------
# 三、即使出错，__exit__ 也会执行
# ------------------------------------------------------------
class SafeSection:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        print(f"\n进入 {self.name}")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        print(f"离开 {self.name}（无论是否出错）")
        return False


try:
    with SafeSection("会出错的段落"):
        print("准备除以 0")
        result = 10 / 0
        print("这行不会执行")
except ZeroDivisionError:
    print("外层捕获了除以 0，但收尾已经完成。")

# ------------------------------------------------------------
# 四、用生成器写上下文管理器
# ------------------------------------------------------------
# contextlib.contextmanager 把一个生成器变成上下文管理器。
# yield 之前是进入，yield 之后是退出。
# 这种写法适合简单的“开始/结束”逻辑。

@contextmanager
def section(name):
    print(f"\n--- {name} 开始 ---")
    try:
        yield
    finally:
        print(f"--- {name} 结束 ---")


with section("生成器版上下文"):
    print("正在执行中间步骤")

# ------------------------------------------------------------
# 五、suppress：只忽略指定异常
# ------------------------------------------------------------
# 不要用空的 except 吞掉所有错误。
# 如果确实只想忽略一种异常，contextlib.suppress 更清楚。

print("\n尝试把 'abc' 转成整数：")
with suppress(ValueError):
    int("abc")
print("ValueError 已被精确忽略，程序继续。")

# ------------------------------------------------------------
# 六、常见错误
# ------------------------------------------------------------
# 1. __exit__ 返回 True，会吞掉异常；不知道这一点会很难排查。
# 2. 在 __enter__ 里借了资源，却在 __exit__ 里忘了释放。
# 3. 用 with 包住整个程序，导致资源占用时间过长。
# 4. 把上下文管理器当成“异常处理器”，该处理的异常还是要在外面处理。
#
# 原则：with 负责收尾，不负责替你做业务判断。

# ------------------------------------------------------------
# 七、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 写一个 TemporaryDirectory 的上下文管理器，自动创建和删除目录。
# 2. 给 Timer 增加一个属性 elapsed，退出后可读取。
# 3. 写一个忽略 FileNotFoundError 的 suppress 示例。
#
# 提示：__exit__ 的参数固定是 (self, exc_type, exc_value, traceback)。

print("\n第 18 课结束：能自动收尾的代码，才适合长期运行。")
