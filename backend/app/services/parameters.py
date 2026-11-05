"""上传文件由平台解析路径；表单数组和对象保持结构传给业务方法。"""

from app.worker.node_sdk import validate_fields


def file_path(db, value):
    from app.models import FileBlob
    from app.services.storage import resolve

    digest = value.get("hash") if isinstance(value, dict) else None
    path = resolve(digest) if isinstance(digest, str) else None
    if path is None or not path.is_file() or db.get(FileBlob, digest) is None:
        raise ValueError("上传文件不存在，请重新上传")
    return str(path.resolve())


def form_values(db, fields, data, *, resolve_files=True):
    values = validate_fields(fields, data)
    for field in fields:
        if field.get("type") == "file" and field["name"] in values:
            path = file_path(db, values[field["name"]])
            if resolve_files:
                values[field["name"]] = path
            else:
                # 启动上下文固定保存上传引用，不采信客户端提供的本地路径。
                values[field["name"]] = {"hash": values[field["name"]]["hash"],
                                         **({"name": values[field["name"]]["name"]}
                                            if isinstance(values[field["name"]].get("name"), str) else {})}
    return values


def validate_start(db, fields, context):
    for field in fields:
        if field.get("scope") == "function":
            bucket = context.get("scope", {}).get(field.get("function"), {})
        else:
            bucket = context.get("global", {})
        name = field["name"]
        if name not in bucket or field.get("type") not in ("file", "array", "object", "json"):
            continue
        values = form_values(db, [field], {name: bucket[name]}, resolve_files=False)
        if name in values:
            bucket[name] = values[name]
        else:
            bucket.pop(name, None)
