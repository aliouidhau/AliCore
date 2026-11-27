# ============================================================
# 第 13 课：模块与标准库——站在现成工具上做事
# ============================================================
# 【本课目标】
# 1. 理解 import 是在引入工具包。
# 2. 会用 math、random、datetime、pathlib。
# 3. 知道何时查文档，而不是死背所有功能。

# ------------------------------------------------------------
# 一、模块是什么？
# ------------------------------------------------------------
# 模块就是一个提供特定功能的 Python 文件或工具包。
# Python 自带的一批模块叫“标准库”，安装 Python 后就能直接用。

import math
import random
from datetime import datetime
from pathlib import Path

# import math：引入整个模块，使用时写 math.sqrt。
# from datetime import datetime：只取其中一个名字，使用时直接写 datetime。

# ------------------------------------------------------------
# 二、math：数学工具
# ------------------------------------------------------------
# 前面已经见过平方根和圆周率，这里再看三角函数。
# 注意：math 的三角函数使用弧度，不是角度。

angle_degrees = 30
angle_radians = math.radians(angle_degrees)
print("30 度的正弦值：", math.sin(angle_radians))
print("60 度的余弦值：", math.cos(math.radians(60)))

# 直角三角形斜边：c = √(a²+b²)，也可以直接用 hypot。
a = 30
b = 40
print("斜边长度：", math.hypot(a, b))

# ------------------------------------------------------------
# 三、random：随机数
# ------------------------------------------------------------
# 随机数适合抽样、模拟、生成练习数据。
# 为了让你每次运行这节课都得到同样结果，先设置固定随机种子。

random.seed(42)

print("0 到 1 的随机小数：", random.random())
print("1 到 100 的随机整数：", random.randint(1, 100))

parts = ["底座", "支架", "盖板", "垫块"]
print("随机抽一个零件：", random.choice(parts))

# 生成 5 个在 20 mm 附近波动的模拟测量值。
measurements = [round(random.uniform(19.8, 20.2), 3) for _ in range(5)]
print("模拟测量值：", measurements)

# ------------------------------------------------------------
# 四、datetime：日期和时间
# ------------------------------------------------------------
# now() 获取当前时间；strftime 按格式转成字符串。

now = datetime.now()
print("当前时间对象：", now)
print("格式化日期：", now.strftime("%Y-%m-%d"))
print("格式化时间：", now.strftime("%H:%M:%S"))
print("中文格式：", now.strftime("%Y 年 %m 月 %d 日"))

# 给输出文件加时间戳时很实用，例如：报告_2026-11-22.txt。
report_name = "报告_" + now.strftime("%Y%m%d_%H%M%S") + ".txt"
print("自动生成的文件名：", report_name)

# ------------------------------------------------------------
# 五、pathlib：路径工具
# ------------------------------------------------------------
# 路径不要靠手猜斜杠，Path 会按当前系统处理。

current_file = Path(__file__)
print("当前教程文件：", current_file.name)
print("所在目录：", current_file.parent)
print("后缀：", current_file.suffix)
print("是否为 Python 文件：", current_file.suffix == ".py")

# ------------------------------------------------------------
# 六、import 的两种风格
# ------------------------------------------------------------
# 风格 1：import 模块名
# 优点：看到 math.pi 就知道 pi 来自 math，不容易混淆。
#
# 风格 2：from 模块名 import 功能名
# 优点：代码短；缺点：引入太多时不容易看出功能来自哪里。
#
# 初学建议：默认用 import 模块名；名字太长或很常用时再 from。

# ------------------------------------------------------------
# 七、课堂练习：生成带日期的随机测量报告
# ------------------------------------------------------------
# 要求：
# 1. 生成 3 个 19.8～20.2 的测量值。
# 2. 打印平均值。
# 3. 打印今天的日期。

random.seed(7)
values = [random.uniform(19.8, 20.2) for _ in range(3)]
average = sum(values) / len(values)

print("\n练习结果：")
print("测量值：", [round(v, 3) for v in values])
print("平均值：", round(average, 3))
print("日期：", datetime.now().strftime("%Y-%m-%d"))

print("\n第 13 课结束：标准库功能很多，掌握查用方法比背诵重要。")
