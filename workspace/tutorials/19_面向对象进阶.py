# ============================================================
# 第 19 课：面向对象进阶——dataclass、property、组合
# ============================================================
# 【本课目标】
# 1. 会用 dataclass 少写重复的初始化代码。
# 2. 会用 property 保护数据并做只读计算。
# 3. 理解继承不是唯一选择，组合往往更清楚。
#
# 【心智模型】
# 类不是“越复杂越高级”。
# 好的类只负责一件事：保存它该保存的数据，提供它该提供的操作。

from dataclasses import dataclass, field

# ------------------------------------------------------------
# 一、dataclass：自动生成 __init__、__repr__、__eq__
# ------------------------------------------------------------
# 普通类要手写 __init__，字段一多就很啰嗦。
# dataclass 会根据字段声明自动生成常用方法。

@dataclass
class Part:
    name: str
    length: float
    width: float
    height: float
    quantity: int = 1

    def __post_init__(self):
        if min(self.length, self.width, self.height) <= 0:
            raise ValueError("尺寸必须大于 0")
        if self.quantity < 1:
            raise ValueError("数量至少为 1")

    @property
    def volume(self):
        """单件体积，只读计算属性。"""
        return self.length * self.width * self.height

    @property
    def total_volume(self):
        return self.volume * self.quantity

    def __str__(self):
        return f"{self.name} x{self.quantity} ({self.length}×{self.width}×{self.height})"


base = Part("底座", 80, 50, 10, 2)
bracket = Part("支架", 120, 60, 8, 1)
print("对象：", base)
print("单件体积：", base.volume)
print("总体积：", base.total_volume)
print("两个对象相等吗：", base == bracket)

# ------------------------------------------------------------
# 二、property：把计算结果伪装成属性
# ------------------------------------------------------------
# 调用时写 base.volume，不加括号。
# 好处是：对外像数据，内部可以随时改算法，不用改调用方。

class Cylinder:
    def __init__(self, name, radius, height):
        if radius <= 0 or height <= 0:
            raise ValueError("半径和高度必须大于 0")
        self.name = name
        self.radius = radius
        self.height = height

    @property
    def volume(self):
        return 3.14159 * self.radius * self.radius * self.height

    @property
    def surface_area(self):
        return 2 * 3.14159 * self.radius * (self.radius + self.height)


cylinder = Cylinder("圆柱", 10, 30)
print("\n圆柱体积：", round(cylinder.volume, 2))
print("圆柱表面积：", round(cylinder.surface_area, 2))

# ------------------------------------------------------------
# 三、只读属性与 setter
# ------------------------------------------------------------
# 只给 @property 不给 setter，外部就不能直接赋值。
# 如果确实需要修改，可以在 setter 里做校验。

class Motor:
    def __init__(self, power):
        self._power = 0
        self.power = power

    @property
    def power(self):
        return self._power

    @power.setter
    def power(self, value):
        if not 0 <= value <= 100:
            raise ValueError("功率百分比必须在 0-100 之间")
        self._power = value

    @property
    def level(self):
        if self.power < 30:
            return "低速"
        if self.power < 70:
            return "中速"
        return "高速"


motor = Motor(45)
print("\n电机功率：", motor.power, "等级：", motor.level)
motor.power = 85
print("调整后：", motor.power, "等级：", motor.level)

try:
    motor.power = 120
except ValueError as error:
    print("非法赋值被拦截：", error)

# ------------------------------------------------------------
# 四、组合：一个对象拥有另一个对象
# ------------------------------------------------------------
# 继承表示“是一种”；组合表示“拥有一个”。
# 库存拥有零件，不需要让库存继承零件。

@dataclass
class Inventory:
    name: str
    items: list[Part] = field(default_factory=list)

    def add(self, part: Part):
        self.items.append(part)

    def total_volume(self):
        return sum(part.total_volume for part in self.items)

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


inventory = Inventory("一号库")
inventory.add(base)
inventory.add(bracket)
print("\n库存：", inventory.name)
print("条目数：", len(inventory))
print("总体积：", inventory.total_volume())

for part in inventory:
    print(" -", part)

# ------------------------------------------------------------
# 五、什么时候不要用类
# ------------------------------------------------------------
# 如果只是一组固定字段、没有复杂行为，字典或 namedtuple 也够。
# 如果只是做一次计算，函数比类更直接。
# 类的价值在于：把数据和相关操作长期绑在一起，并保护不变量。

# ------------------------------------------------------------
# 六、常见错误
# ------------------------------------------------------------
# 1. dataclass 字段可变默认值写成 []，多个对象会共享同一个列表。
#    正确写法：field(default_factory=list)。
# 2. property 没有 setter，却直接 obj.volume = 10。
# 3. 滥用继承，把“拥有”写成“是一种”。
# 4. 在 __post_init__ 外修改尺寸，绕过校验。
#
# 设计原则：能用一个函数解决，就别造一个类。

# ------------------------------------------------------------
# 七、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 给 Part 增加 `is_valid` 属性。
# 2. 给 Inventory 增加 `remove(name)` 方法。
# 3. 写一个 Motor 子类，增加 `boost()` 方法，但保持功率上限。
#
# 提示：remove 可以用列表推导式重建 items，避免边遍历边删除。

print("\n第 19 课结束：类要保护数据，不要只做字段容器。")
