# ============================================================
# 第 14 课：类与对象入门——把数据和行为放在一起
# ============================================================
# 【本课目标】
# 1. 理解“类是图纸，对象是实物”。
# 2. 会定义简单类、创建对象、调用方法。
# 3. 知道什么时候用字典就够，什么时候类更清楚。

# ------------------------------------------------------------
# 一、类和对象到底是什么？
# ------------------------------------------------------------
# 用机械来类比：
# 类 class 像零件图纸，规定零件有哪些参数、能做什么。
# 对象 object 像按图纸制造出来的具体零件。
# 同一张图纸可以做出很多个对象，每个对象的尺寸可以不同。

class BoxPart:
    """一个长方体零件：保存名称和尺寸，并提供体积计算。"""

    def __init__(self, name, length, width, height):
        # 创建对象时，__init__ 会自动执行，用来保存初始数据。
        # self 指“当前这个对象本身”。
        self.name = name
        self.length = length
        self.width = width
        self.height = height

    def volume(self):
        """返回当前零件的体积。"""
        return self.length * self.width * self.height

    def describe(self):
        """返回当前零件的描述文字。"""
        return f"{self.name}：{self.length}×{self.width}×{self.height} mm"


# ------------------------------------------------------------
# 二、创建对象
# ------------------------------------------------------------
# 类名后面加括号，就会创建对象并调用 __init__。
# self 不用手动传，Python 会自动处理。

base = BoxPart("底座", 80, 50, 10)
bracket = BoxPart("支架", 120, 60, 8)

print(base.describe())
print("底座体积：", base.volume(), "mm³")
print(bracket.describe())
print("支架体积：", bracket.volume(), "mm³")

# base 和 bracket 来自同一个类，但各自保存不同的数据。
print("两个对象是否同一个：", base is bracket)

# ------------------------------------------------------------
# 三、属性与方法
# ------------------------------------------------------------
# 属性：对象保存的数据，例如 base.length。
# 方法：对象能执行的动作，例如 base.volume()。

print("底座的长度属性：", base.length)

# 属性可以修改，但正式项目里常会加检查，防止改成不合理数值。
base.length = 90
print("修改长度后：", base.describe())
print("修改后的体积：", base.volume())

# ------------------------------------------------------------
# 四、在创建对象时检查数据
# ------------------------------------------------------------
# 如果长度允许为负数，后面所有计算都会失去意义。
# 可以在 __init__ 里拒绝非法数据，让错误尽早暴露。

class CheckedBoxPart(BoxPart):
    """带尺寸检查的长方体零件，继承 BoxPart 的体积和描述能力。"""

    def __init__(self, name, length, width, height):
        if min(length, width, height) <= 0:
            raise ValueError("长、宽、高必须大于 0")
        # super() 调用父类的初始化方法，避免重复写保存属性的代码。
        super().__init__(name, length, width, height)


valid_part = CheckedBoxPart("垫块", 30, 30, 5)
print("\n合法零件：", valid_part.describe())

try:
    invalid_part = CheckedBoxPart("错误零件", -30, 30, 5)
except ValueError as e:
    print("创建失败，已处理：", e)

# ------------------------------------------------------------
# 五、继承：复用已有类的能力
# ------------------------------------------------------------
# 上面的 CheckedBoxPart(BoxPart) 就是继承。
# 它不用重新写 volume() 和 describe()，也能直接使用。

print("继承来的体积方法：", valid_part.volume())

# 初学阶段不要为了追求“高级”而乱用继承。
# 只有新类确实是一种更具体的旧类时，继承才自然。

# ------------------------------------------------------------
# 六、字典和类怎么选？
# ------------------------------------------------------------
# 只是临时保存几个字段：字典通常更简单。
# 数据和行为紧密相关，而且要创建很多同类对象：类更合适。
#
# 例如：
# part = {"length": 80, "width": 50}  # 临时数据，够用
# base = BoxPart(...)                 # 需要体积、描述、校验等行为

# ------------------------------------------------------------
# 七、课堂练习：给类增加质量计算
# ------------------------------------------------------------
# 下面定义一个新类，继承 BoxPart，并增加 density 属性和 mass_g 方法。
# 注意单位：mm³ 除以 1000 得到 cm³。

class MaterialPart(BoxPart):
    def __init__(self, name, length, width, height, density):
        super().__init__(name, length, width, height)
        self.density = density

    def mass_g(self):
        volume_cm3 = self.volume() / 1000
        return volume_cm3 * self.density


aluminum_base = MaterialPart("铝合金底座", 80, 50, 10, 2.70)
print("\n练习对象：", aluminum_base.describe())
print(f"质量：{aluminum_base.mass_g():.2f} g")

print("\n第 14 课结束：类是图纸，对象是实物，方法是对象能做的事。")
