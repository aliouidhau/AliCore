# ============================================================
# 第 21 课：单元测试——证明你的代码真的按预期工作
# ============================================================
# 【本课目标】
# 1. 理解“测试不是调试，测试是提前写好的验收标准”。
# 2. 会用 unittest 写测试类、测试方法。
# 3. 知道正常值、边界值、错误值都要测。
#
# 【心智模型】
# 函数像零件，测试像检验工序。
# 你不是等零件坏了再修，而是在出厂前把尺寸、强度、边界都验一遍。

import unittest

# ------------------------------------------------------------
# 一、被测代码
# ------------------------------------------------------------
# 先写一个简单、纯的函数：只依赖参数，不依赖全局状态。
# 这种函数最容易测试，也最容易复用。

def box_volume(length: float, width: float, height: float) -> float:
    if min(length, width, height) <= 0:
        raise ValueError("尺寸必须大于 0")
    return length * width * height


def classify_volume(volume: float) -> str:
    if volume < 10_000:
        return "小件"
    if volume < 50_000:
        return "中件"
    return "大件"


# 手工验证：先确认正常路径。
print("正常体积：", box_volume(80, 50, 10))
print("体积分类：", classify_volume(box_volume(80, 50, 10)))

# ------------------------------------------------------------
# 二、写第一组测试
# ------------------------------------------------------------
# 测试方法名必须以 test_ 开头，unittest 才会自动发现。
# 每个测试只验证一个明确的行为。
# AAA 结构：Arrange 准备，Act 执行，Assert 断言。

class TestBoxVolume(unittest.TestCase):
    def test_normal_box(self):
        # Arrange & Act
        result = box_volume(10, 20, 5)
        # Assert
        self.assertEqual(result, 1000)

    def test_unit_cube(self):
        self.assertEqual(box_volume(1, 1, 1), 1)

    def test_zero_side_is_rejected(self):
        # 错误值必须明确抛出异常，而不是悄悄返回 0。
        with self.assertRaises(ValueError):
            box_volume(0, 20, 5)

    def test_negative_side_is_rejected(self):
        with self.assertRaises(ValueError):
            box_volume(-10, 20, 5)

    def test_float_result(self):
        result = box_volume(1.5, 2.0, 3.0)
        self.assertAlmostEqual(result, 9.0, places=6)


class TestClassifyVolume(unittest.TestCase):
    def test_small_boundary(self):
        self.assertEqual(classify_volume(9_999), "小件")

    def test_medium_boundary(self):
        self.assertEqual(classify_volume(10_000), "中件")
        self.assertEqual(classify_volume(49_999), "中件")

    def test_large_boundary(self):
        self.assertEqual(classify_volume(50_000), "大件")


# ------------------------------------------------------------
# 三、测试不是只测“正常情况”
# ----------------------------------------------------
# 最容易被忽略的是边界：
# 0、1、最大值、刚好等于阈值、空列表、None、重复数据。
# 这些地方最容易出现“看起来能跑，实际算错”。

# ------------------------------------------------------------
# 四、测试应该独立
# ------------------------------------------------------------
# 每个测试都应该能单独运行，不依赖上一个测试留下的状态。
# 不要在一个测试里改全局变量，再让另一个测试读它。
# 如果必须共享准备代码，用 setUp，但不要共享可变结果。

class TestInventoryRules(unittest.TestCase):
    def test_quantity_must_be_positive(self):
        # 这里用一个最小规则函数代替完整库存类，突出测试思路。
        def total(quantity: int, unit_volume: float) -> float:
            if quantity < 1:
                raise ValueError("数量至少为 1")
            return quantity * unit_volume

        self.assertEqual(total(2, 100), 200)
        with self.assertRaises(ValueError):
            total(0, 100)


# ------------------------------------------------------------
# 五、运行测试
# ------------------------------------------------------------
# 在命令行里通常写：
#   python -m unittest test_file.py -v
#
# 在教程文件里直接运行：
if __name__ == "__main__":
    unittest.main(verbosity=2)

# ------------------------------------------------------------
# 六、测试和调试的区别
# ----------------------------------------------------
# 调试：程序已经出错，去找原因。
# 测试：提前定义“什么叫做对”，让错误尽早暴露。
# 先写测试再写实现叫 TDD，但初学不必强求；
# 至少要养成“改完代码，跑一遍测试”的习惯。

# ------------------------------------------------------------
# 七、常见错误
# ----------------------------------------------------
# 1. 测试只覆盖正常值，不覆盖 0、负数、空值。
# 2. 一个测试里塞十个断言，失败时不知道哪一条坏了。
# 3. 测试依赖当前时间、随机数、网络，结果不稳定。
# 4. 为了让测试通过而改测试，而不是改代码。
#
# 好测试的特征：快、独立、可重复、失败信息清楚。

# ------------------------------------------------------------
# 八、动手练习
# ----------------------------------------------------
# 复制练习后完成：
# 1. 给 classify_volume 增加“刚好 0 体积”的测试。
# 2. 写一个测试，验证 1.5×2×3 的浮点误差。
# 3. 写一个故意失败的测试，观察 unittest 的输出结构。
#
# 提示：断言失败不是坏事，它是在告诉你契约没有被满足。

print("\n第 21 课结束：能重复验收的代码，才算真的完成。")
