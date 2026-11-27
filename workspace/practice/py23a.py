import json


def to_text(data):
    return json.dumps(data, indent=2)


print(to_text({"workshop": "一号车间"}))
