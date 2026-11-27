# ============================================================
# 第 11 课：异常与调试——报错以后怎么办
# ============================================================
# 【本课目标】
# 1. 看懂 traceback 的基本结构。
# 2. 会用 try / except 处理能预料的错误。
# 3. 掌握 print 调试和分步定位问题的方法。

# ------------------------------------------------------------
# 一、报错不是失败，而是线索
# ------------------------------------------------------------
# 看到报错先别整段重写，按这个顺序看：
# 1. 最后一行：错误类型和原因。
# 2. 往上找自己的文件名和行号。
# 3. 回到那一行，检查变量值和类型。

import traceback

try:
    numbers = []
    average = sum(numbers) / len(numbers)
except ZeroDivisionError:
    print("下面演示一段 traceback：")
    traceback.print_exc()   # 打印完整报错，但程序不会因此中断

# ------------------------------------------------------------
# 二、try / except：接住能预料的问题
# ------------------------------------------------------------
# try 里放可能出错的代码。
# except 指定要接住的错误类型。
# 不建议上来就写光秃秃的 except:，那会把真正的问题也吞掉。

text = "95"

try:
    score = int(text)
    print("转换成功：", score)
except ValueError:
    print("转换失败：请输入像 95 这样的整数字符串")

bad_text = "九十五"
try:
    score = int(bad_text)
    print("这行不会执行：", score)
except ValueError:
    print("bad_text 转换失败，已处理")

# ------------------------------------------------------------
# 三、文件不存在与除以零
# ------------------------------------------------------------
# 不同的错误用不同的 except，处理办法才可能准确。

from pathlib import Path

try:
    content = Path("肯定不存在的文件.txt").read_text(encoding="utf-8")
    print(content)
except FileNotFoundError:
    print("文件不存在：先检查文件名和路径")

for number in [10, 2, 0, 5]:
    try:
        print(f"100 / {number} = {100 / number}")
    except ZeroDivisionError:
        print("除数不能为 0，跳过这一项")

# ------------------------------------------------------------
# 四、else 与 finally
# ------------------------------------------------------------
# else：try 没出错时执行。
# finally：无论出不出错都执行，适合做收尾。

try:
    value = int("80")
except ValueError:
    print("转换失败")
else:
    print("没有异常，转换结果是：", value)
finally:
    print("这一段无论如何都会执行")

# ------------------------------------------------------------
# 五、raise：主动拒绝不合理的数据
# ------------------------------------------------------------
# 有些情况 Python 本身不会报错，但业务规则不允许。
# 例如零件长度不能是负数，这时可以主动抛出异常。

def rectangle_area(length, width):
    if length <= 0 or width <= 0:
        raise ValueError("长和宽必须大于 0")
    return length * width


print("正常面积：", rectangle_area(80, 50))

try:
    print(rectangle_area(-80, 50))
except ValueError as e:
    print("参数不合法：", e)

# ------------------------------------------------------------
# 六、print 调试法
# ------------------------------------------------------------
# 程序结果不对但没报错时，把关键变量的“值和类型”打印出来。
# 不要只盯着代码猜，先让程序把真实状态说出来。

length_text = "80"
width_text = "50"

print("调试：", repr(length_text), type(length_text))
print("调试：", repr(width_text), type(width_text))

# 错误写法：area = length_text * width_text
# 字符串 * 字符串会报 TypeError；先转换类型。
area = int(length_text) * int(width_text)
print("修正后的面积：", area)

# ------------------------------------------------------------
# 七、课堂练习：安全计算平均值
# ------------------------------------------------------------
# 要求：空列表返回 0；列表里有非数字时给出提示，不让程序崩。

def safe_average(values):
    if not values:
        return 0
    try:
        return sum(values) / len(values)
    except TypeError:
        print("列表里混入了不能求和的数据")
        return 0


print("正常平均分：", safe_average([80, 90, 85]))
print("空列表平均分：", safe_average([]))
print("异常数据平均分：", safe_average([80, "缺考", 85]))

print("\n第 11 课结束：先看最后错误行，再定位行号，最后检查变量。")
