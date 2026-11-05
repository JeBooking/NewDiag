"""表单类型只决定输入和校验方式，业务函数仍接收普通 Python 值。"""

import re

INPUT_TYPES = {"text", "number", "boolean", "datetime", "file", "array", "object", "json"}


def input_type(annotation):
    name = str(annotation or "").lower().replace("typing.", "").replace(" ", "")
    if name in INPUT_TYPES:
        return name
    if name.startswith("optional[") and name.endswith("]"):
        name = name[9:-1]
    names = re.split(r"[|,]", name)
    for member in names:
        base = member.split("[", 1)[0]
        if base in ("list", "tuple", "sequence", "set", "frozenset"):
            return "array"
        if base in ("dict", "mapping", "typeddict"):
            return "object"
        if base in ("path", "pathlib.path", "filepath"):
            return "file"
        if base in ("int", "float", "double", "decimal"):
            return "number"
        if base in ("bool", "boolean"):
            return "boolean"
        if base in ("datetime", "date", "time"):
            return "datetime"
    return "text"
