def parse_part(row):
    # TODO: 必需 name/length/width/height/quantity
    # 缺字段、非数字、负库存、空名字都要抛 ValueError
    return {
        "name": row["name"],
        "length": 0.0,
        "width": 0.0,
        "height": 0.0,
        "quantity": 0,
    }


print(parse_part({"name": "底座", "length": "80", "width": "50", "height": "10", "quantity": "2"}))
