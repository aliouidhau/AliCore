# ============================================================
# 第 22 课：并发入门——让等待的时间重叠起来
# ============================================================
# 【本课目标】
# 1. 理解并发不等于“同时计算”，而是“让多个任务交错推进”。
# 2. 会用 threading.Thread 和 ThreadPoolExecutor。
# 3. 知道线程适合 I/O 等待，CPU 密集计算要考虑多进程。
#
# 【心智模型】
# 一个人做饭：烧水时可以切菜。
# 这就是并发：不是多长出一双手，而是别干等着。
# Python 的 GIL 让同一时刻只有一个线程执行 Python 字节码，
# 所以线程对“等网络、等文件、等设备”更有价值。

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------------------------------------------------
# 一、顺序执行：一个做完再做下一个
# ------------------------------------------------------------
def measure_part(name, delay=0.08):
    # 用 sleep 模拟测量、读文件或等设备返回。
    time.sleep(delay)
    return f"{name} 测量完成"


started = time.perf_counter()
results = [measure_part(name) for name in ["底座", "支架", "垫块"]]
elapsed = time.perf_counter() - started
print("顺序执行：")
for result in results:
    print(" ", result)
print(f"顺序耗时：{elapsed:.3f} 秒")

# ------------------------------------------------------------
# 二、ThreadPoolExecutor：把任务交给线程池
# ------------------------------------------------------------
# max_workers=3 表示最多同时有 3 个任务在推进。
# 这里每个任务大部分时间都在 sleep，所以并发收益明显。

started = time.perf_counter()
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(measure_part, name) for name in ["底座", "支架", "垫块"]]
    for future in as_completed(futures):
        print(" ", future.result())
elapsed = time.perf_counter() - started
print(f"线程池耗时：{elapsed:.3f} 秒")

# 注意：输出顺序取决于谁先完成，不一定等于提交顺序。
# 需要保持顺序时，可以逐个 future.result()，而不是 as_completed。

# ------------------------------------------------------------
# 三、共享数据要加锁
# ------------------------------------------------------------
# 多个线程同时修改同一个变量，可能出现丢失更新。
# Lock 保证同一时刻只有一个线程进入临界区。

counter = 0
lock = threading.Lock()


def add_many(times):
    global counter
    for _ in range(times):
        with lock:
            current = counter
            # 故意在读和写之间留一点空隙，模拟真实业务里的计算。
            current += 1
            counter = current


threads = [threading.Thread(target=add_many, args=(1000,)) for _ in range(4)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

print("\n加锁后的计数：", counter)  # 应该是 4000

# 如果去掉 with lock，结果可能小于 4000。
# 这不是 Python 的 bug，而是共享状态没有同步。

# ------------------------------------------------------------
# 四、什么时候不该用线程
# ------------------------------------------------------------
# 1. 纯 Python 的 CPU 密集计算：GIL 会让多线程很难真正并行。
#    这种情况考虑 multiprocessing，或者用 NumPy 等释放 GIL 的库。
# 2. 任务之间有强顺序依赖：并发反而增加复杂度。
# 3. 共享状态太多：锁会拖慢速度，也容易死锁。
#
# 判断口诀：
# 等得多 -> 线程；算得多 -> 进程；结构复杂 -> 先串行。

# ------------------------------------------------------------
# 五、常见错误
# ------------------------------------------------------------
# 1. 忘记 join，主线程先结束，任务还没跑完。
# 2. 多个线程同时写同一个列表/字典，却不加锁。
# 3. 在锁里做耗时操作，导致其他线程排长队。
# 4. 以为线程越多越快；线程创建和切换也有成本。
#
# 并发不是免费的。先用测量证明它真的更快。

# ------------------------------------------------------------
# 六、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 把 measure_part 的 delay 改成 0.2，比较顺序和并发耗时。
# 2. 用 ThreadPoolExecutor 处理 6 个零件，限制 2 个线程。
# 3. 写一个会死锁的例子（两个锁、两个线程），再解释如何避免。
#
# 提示：死锁例子只写注释和思路，不要在教程里真的挂住进程。

print("\n第 22 课结束：并发先看任务类型，再选工具。")
