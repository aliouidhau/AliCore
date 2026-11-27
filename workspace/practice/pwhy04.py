import copy
a = {"parts": [1, 2, 3]}
b = copy.copy(a)
b["parts"].append(4)
print(a)
