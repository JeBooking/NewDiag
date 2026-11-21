"""清理执行目录里遗留的工程副本。

旧版本每次执行都会整份复制接入工程到 executions/<id>/modules；现在改成按发布版本共享快照，
已结束实例目录里的 modules 只是占空间。默认只统计，加 --yes 才删除。

用法：backend/.venv/Scripts/python scripts/prune_execution_modules.py [--yes]
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.db import SessionLocal, data_dir  # noqa: E402

TERMINAL = {'success', 'failed', 'escalated', 'terminated'}


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--yes', action='store_true', help='真正删除，默认只统计')
    args = parser.parse_args()

    executions = data_dir() / 'executions'
    if not executions.is_dir():
        print('没有执行目录')
        return 0

    total = 0
    removed = 0
    with SessionLocal() as db:
        statuses = dict(db.execute(select(models.Instance.id, models.Instance.status)).all())
        for directory in sorted(executions.iterdir()):
            modules = directory / 'modules'
            if not modules.is_dir():
                continue
            status = statuses.get(directory.name)
            if status not in TERMINAL:
                print(f'skip {directory.name} status={status or "unknown"}')
                continue
            size = directory_size(modules)
            total += size
            removed += 1
            print(f'{directory.name} {size / 1024 / 1024:.1f} MB')
            if args.yes:
                shutil.rmtree(modules, ignore_errors=True)

    print(f'共 {removed} 个已结束实例，{total / 1024 / 1024:.1f} MB' + ('' if args.yes else '（未删除，加 --yes）'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
