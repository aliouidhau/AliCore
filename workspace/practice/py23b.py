class Part:
    def __init__(self, name, length, width, height, quantity=1):
        self.name = name
        self.length = length
        self.width = width
        self.height = height
        self.quantity = quantity

    @property
    def volume(self):
        return self.length * self.width * self.height

    @classmethod
    def from_api(cls, row):
        # TODO: 缺 size、长度不是 3、非数字都要抛 ValueError
        return cls(str(row.get("name") or "?"), 0, 0, 0)


print(Part.from_api({"name": "底座", "size": [80, 50, 10]}).volume)
