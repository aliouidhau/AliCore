# ============================================================
# 第 16 课：推导式与高阶函数——把循环写得更清楚
# ============================================================
# 【本课目标】
# 1. 会用列表、字典、集合推导式。
# 2. 理解 map / filter / sorted / any / all 的用途。
# 3. 知道推导式不是越长越好，可读性优先。
#
# 【心智模型】
# 普通循环是“一步一步搬砖”。
# 推导式是“给我一张筛选和加工的规则，我一次给你结果”。
# 高阶函数是“把函数本身当成参数交给另一个函数”。

# ------------------------------------------------------------
# 一、列表推导式
# ------------------------------------------------------------
numbers = [1, 2, 3, 4, 5, 6, 7, 8]
squares = [number * number for number in numbers]
print("平方：", squares)

even_squares = [number * number for number in numbers if number % 2 == 0]
print("偶数的平方：", even_squares)

# 等价的普通循环：
result = []
for number in numbers:
    if number % 2 == 0:
        result.append(number * number)
print("普通循环结果：", result)

# ------------------------------------------------------------
# 二、字典推导式和集合推导式
# ------------------------------------------------------------
parts = [
    ("底座", 80, 50, 10),
    ("支架", 120, 60, 8),
    ("垫块", 30, 30, 5),
]

volumes = {
    name: length * width * height
    for name, length, width, height in parts
}
print("\n零件体积字典：", volumes)

lengths = {length for _, length, _, _ in parts}
print("出现过的长度集合：", lengths)

# ------------------------------------------------------------
# 三、map：对每个元素做同一件事
# ------------------------------------------------------------
# map(函数, 可迭代对象) 返回一个惰性迭代器。
# 需要列表时，再用 list() 取出来。

def format_part(part):
    name, length, width, height = part
    return f"{name}({length}×{width}×{height})"


formatted = list(map(format_part, parts))
print("\nmap 格式化：")
for line in formatted:
    print(" ", line)

# 常见写法：配合 lambda 做简单转换，复杂逻辑还是用 def。
areas = list(map(lambda part: part[1] * part[2], parts))
print("底面积：", areas)

# ------------------------------------------------------------
# 四、filter：只留下满足条件的元素
# ------------------------------------------------------------
def is_large(part):
    _, length, width, _ = part
    return length >= 80 and width >= 50


large_parts = list(filter(is_large, parts))
print("\n大件：", [part[0] for part in large_parts])

# ------------------------------------------------------------
# 五、sorted：按指定字段排序
# ------------------------------------------------------------
# key 接收一个函数，返回排序依据。
# 这里按体积从大到小排序，reverse=True 表示降序。

def volume(part):
    _, length, width, height = part
    return length * width * height


by_volume = sorted(parts, key=volume, reverse=True)
print("\n按体积排序：")
for part in by_volume:
    print(f"  {part[0]:<4} {volume(part):>6} mm³")

# 多关键字排序：先按长度升序，长度相同再按宽度降序。
by_size = sorted(parts, key=lambda part: (part[1], -part[2]))
print("按尺寸排序：", [part[0] for part in by_size])

# ------------------------------------------------------------
# 六、any / all：快速判断
# ------------------------------------------------------------
print("\n是否所有零件都合格：", all(min(p[1], p[2], p[3]) > 0 for p in parts))
print("是否存在长度超过 100 的零件：", any(p[1] > 100 for p in parts))

# ------------------------------------------------------------
# 七、zip：把两个序列并排组合
# ------------------------------------------------------------
names = ["底座", "支架", "垫块"]
quantities = [2, 1, 4]
for name, quantity in zip(names, quantities):
    print(f"{name}: {quantity} 件")

# ------------------------------------------------------------
# 八、常见错误
# ------------------------------------------------------------
# 1. 推导式里塞太多逻辑，读起来比普通循环还费劲。
# 2. 忘记 map/filter 返回的是迭代器，直接 print 会看到对象地址。
# 3. lambda 里写复杂表达式，后面没人看得懂。
# 4. sorted 的 key 写错，比如把 key=volume 写成 key=volume()。
#
# 经验：超过一行的推导式，通常该改回普通循环。

# ------------------------------------------------------------
# 九、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 用字典推导式统计每个零件名的首字母。
# 2. 用 filter 找出体积大于 30000 的零件。
# 3. 用 sorted 按“名称长度”排序。
#
# 参考：
first_letters = {name: name[0] for name, *_ in parts}
print("\n首字母字典：", first_letters)
print(
    "体积大于 30000：",
    [part[0] for part in filter(lambda p: volume(p) > 30000, parts)],
)
print("按名称长度排序：", sorted(parts, key=lambda part: len(part[0])))

print("\n第 16 课结束：先写清楚，再写短。")
