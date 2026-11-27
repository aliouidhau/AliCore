# ============================================================
# 第 12 课：综合练习——零件参数计算器
# ============================================================
# 【本课目标】
# 把变量、字典、列表、条件、循环、函数和异常串起来。
# 这一课不是学新语法，而是练习“把需求拆成步骤”。

# ------------------------------------------------------------
# 一、先明确需求
# ------------------------------------------------------------
# 我们有一批长方体零件，希望程序完成：
# 1. 根据长、宽、高计算体积。
# 2. 根据材料密度估算质量。
# 3. 检查尺寸是否合法。
# 4. 汇总所有零件的总体积和总质量。
#
# 老师提醒：写综合程序不要一口气从上往下硬写。
# 先拆小函数，每个函数只解决一个问题。

# ------------------------------------------------------------
# 二、准备基础数据
# ------------------------------------------------------------
# 密度单位：克 / 立方厘米。
# 代码里尺寸使用毫米，所以算出来先是立方毫米，再换算。

MATERIAL_DENSITY = {
    "铝合金": 2.70,
    "钢": 7.85,
    "黄铜": 8.50,
}

PARTS = [
    {"name": "底座", "length": 80, "width": 50, "height": 10, "material": "铝合金", "quantity": 2},
    {"name": "支架", "length": 120, "width": 60, "height": 8, "material": "钢", "quantity": 4},
    {"name": "垫块", "length": 30, "width": 30, "height": 5, "material": "黄铜", "quantity": 6},
]

# ------------------------------------------------------------
# 三、小函数 1：计算单个零件体积
# ------------------------------------------------------------
# 1 立方厘米 = 1000 立方毫米。
# 返回立方厘米，后面计算质量更方便。

def calculate_volume_cm3(part):
    length = part["length"]
    width = part["width"]
    height = part["height"]

    if min(length, width, height) <= 0:
        raise ValueError(f"{part['name']} 的尺寸必须大于 0")

    volume_mm3 = length * width * height
    return volume_mm3 / 1000

# ------------------------------------------------------------
# 四、小函数 2：计算单件质量
# ------------------------------------------------------------
# 质量 = 体积 × 密度。
# 材料不在字典里时，主动给出清楚的错误，而不是让 KeyError 突然冒出来。

def calculate_mass_g(part, volume_cm3):
    material = part["material"]
    if material not in MATERIAL_DENSITY:
        raise ValueError(f"没有记录 {material} 的密度")

    density = MATERIAL_DENSITY[material]
    return volume_cm3 * density

# ------------------------------------------------------------
# 五、小函数 3：生成一行报告
# ------------------------------------------------------------
# 把“计算”和“显示格式”分开，以后改格式不用动计算公式。

def build_report_line(part):
    try:
        volume = calculate_volume_cm3(part)
        mass = calculate_mass_g(part, volume)
        quantity = part["quantity"]
        return {
            "ok": True,
            "text": (
                f"{part['name']}：单件体积 {volume:.2f} cm³，"
                f"单件质量 {mass:.2f} g，数量 {quantity}"
            ),
            "volume": volume * quantity,
            "mass": mass * quantity,
        }
    except (ValueError, KeyError) as e:
        return {"ok": False, "text": f"{part.get('name', '未命名零件')} 计算失败：{e}"}

# ------------------------------------------------------------
# 六、主流程：遍历、展示、汇总
# ------------------------------------------------------------

total_volume = 0
total_mass = 0
success_count = 0

print("========== 零件参数报告 ==========")

for part in PARTS:
    result = build_report_line(part)
    print(result["text"])

    if result["ok"]:
        total_volume += result["volume"]
        total_mass += result["mass"]
        success_count += 1

print("----------------------------------")
print("成功计算：", success_count, "种零件")
print(f"总体积：{total_volume:.2f} cm³")
print(f"总质量：{total_mass:.2f} g")
print(f"总质量：{total_mass / 1000:.3f} kg")

# ------------------------------------------------------------
# 七、继续练习的方向
# ------------------------------------------------------------
# 点「复制练习」后，可以按顺序挑战：
# 1. 给 PARTS 增加一个“盖板”。
# 2. 增加一种材料“尼龙”，密度约 1.15。
# 3. 把报告写入“零件报告.txt”。
# 4. 增加合格判断：单件质量超过 500 g 时标为“较重”。
# 5. 把总质量最高的零件找出来。
#
# 做项目时每次都这样：先让最小版本跑通，再一项一项加功能。

print("\n第 12 课结束：复杂程序 = 清楚的数据 + 小函数 + 主流程。")
