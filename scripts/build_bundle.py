"""打离线包：内嵌 Python + 依赖 + 前端产物，目标机器无需安装 Node/Python。

用法：backend/.venv/Scripts/python scripts/build_bundle.py [--skip-build]

产物：deploy/newdiag/（目录）与 deploy/newdiag-win64.zip
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'deploy'
BUNDLE = DEPLOY / 'newdiag'
CACHE = ROOT / '.bundle-cache'
PY_VERSION = '3.11.9'  # 3.11 最后一个带 Windows 二进制的补丁版本
PY_ABI = 'cp311'
PY_STEM = 'python311'
PY_ZIP = f'python-{PY_VERSION}-embed-amd64.zip'
PY_URL = f'https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP}'


def run(cmd: list[str], **kwargs) -> None:
    print('+', ' '.join(str(part) for part in cmd))
    subprocess.run(cmd, check=True, **kwargs)


def build_frontend() -> None:
    print('+ pnpm build')
    subprocess.run('pnpm build', cwd=ROOT, shell=True, check=True)


def fetch_python() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / PY_ZIP
    if target.is_file() and target.stat().st_size > 1_000_000:
        return target
    print(f'下载 {PY_URL}')
    with urllib.request.urlopen(PY_URL, timeout=120) as response, target.open('wb') as fh:
        shutil.copyfileobj(response, fh)
    return target


def extract_python(zip_path: Path) -> Path:
    runtime = BUNDLE / 'runtime' / 'python'
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(runtime)

    pth = next(runtime.glob('python*._pth'), None)
    if pth is None:
        raise SystemExit('嵌入版 Python 里找不到 ._pth')
    # ..\.. 指向包根，让 app 包可被 import；其余保持官方默认
    pth.write_text(
        f'{PY_STEM}.zip\n.\nLib\\site-packages\n..\\..\n',
        encoding='utf-8',
    )
    return runtime


def install_deps(runtime: Path) -> None:
    site = runtime / 'Lib' / 'site-packages'
    site.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            '-m',
            'pip',
            'install',
            '--target',
            str(site),
            '--upgrade',
            '--only-binary=:all:',
            '--python-version',
            PY_VERSION.rsplit('.', 1)[0],
            '--implementation',
            'cp',
            '--abi',
            PY_ABI,
            '--platform',
            'win_amd64',
            '--index-url',
            'https://pypi.org/simple',
            '-r',
            str(ROOT / 'backend' / 'requirements-runtime.txt'),
        ]
    )


def copy_app() -> None:
    shutil.copytree(
        ROOT / 'backend' / 'app',
        BUNDLE / 'app',
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
    )
    shutil.copytree(ROOT / 'examples', BUNDLE / 'examples')
    shutil.copytree(ROOT / 'dist', BUNDLE / 'web')
    (BUNDLE / 'data').mkdir(parents=True, exist_ok=True)
    (BUNDLE / 'logs').mkdir(parents=True, exist_ok=True)


START = r'''@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NEWDIAG_DB=%HERE%data\newdiag.db"
set "NEWDIAG_WEB_DIR=%HERE%web"
set "NEWDIAG_LOG_DIR=%HERE%logs"
if "%NEWDIAG_LOG_LEVEL%"=="" set "NEWDIAG_LOG_LEVEL=INFO"
echo 诊断流程平台启动中：http://127.0.0.1:8765/
echo 日志：%HERE%logs\app.log    停止：Ctrl+C
"%HERE%runtime\python\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir "%HERE%"
endlocal
'''

START_LAN = r'''@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NEWDIAG_DB=%HERE%data\newdiag.db"
set "NEWDIAG_WEB_DIR=%HERE%web"
set "NEWDIAG_LOG_DIR=%HERE%logs"
if "%NEWDIAG_LOG_LEVEL%"=="" set "NEWDIAG_LOG_LEVEL=INFO"
echo 诊断流程平台启动中（监听所有网卡）：http://<本机IP>:8765/
echo 注意：这套没有登录，仅限内网自用。
"%HERE%runtime\python\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir "%HERE%"
endlocal
'''

README = '''# 诊断流程平台（离线包）

解压后双击 `start.cmd`，浏览器打开 http://127.0.0.1:8765/ 。
不需要安装 Node、Python 或任何依赖。

## 首次使用

1. 打开「工程」页，登记业务 Python 工程：
   - 路径：本机上的工程目录（例如包内 `examples\\cooling` 的绝对路径）
   - 模块：`cooling.diagnosis`
   - 解释器、工厂按需填写，可留空
2. 打开「流程」页，新建流程，画图、绑定接口、保存、发布。
3. 在版本列表点「执行」，填入启动输入（例如 `{"voltage": 198.4}`）。
4. 处置待办出现后，在右侧面板点「完成」或「回退」。

## 内网访问

`start-lan.cmd` 监听所有网卡，别人用 `http://<本机IP>:8765/` 访问。
这套没有登录与权限，仅建议自己测试时使用，别长期对内网开放。

## 数据与日志

- 数据库：`data\\newdiag.db`（SQLite，备份直接复制这个文件）
- 附件：`data\\files\\`
- 日志：`logs\\app.log`（5MB 轮转）

调试时用环境变量提高日志级别再启动：

```
set NEWDIAG_LOG_LEVEL=DEBUG
set NEWDIAG_LOG_PAYLOAD=1
start.cmd
```

`NEWDIAG_LOG_PAYLOAD=1` 会把接口参数与返回值写进日志，内含业务数据时慎用。

## 业务工程要求

- Python 3.9+，依赖需装在业务工程自己的解释器里
- 平台通过子进程调用业务函数，函数必须是只读的（采集/判断），动作由人完成
- 接口参数按函数签名自动匹配：同名快照字段 → 上游唯一同名字段 → 默认值
- 改了函数签名需要重新发布会话，否则执行前会被签名指纹拦下
'''


def write_launchers() -> None:
    (BUNDLE / 'start.cmd').write_text(START, encoding='utf-8-sig')
    (BUNDLE / 'start-lan.cmd').write_text(START_LAN, encoding='utf-8-sig')
    (BUNDLE / 'README.md').write_text(README, encoding='utf-8')


def zip_bundle(bundle: Path) -> Path:
    """压缩包内统一用 newdiag/ 作为根目录，与构建目录名无关。"""
    stamp = date.today().strftime('%Y%m%d')
    archive = DEPLOY / f'newdiag-win64-{stamp}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zf.write(path, arcname=str(Path('newdiag') / path.relative_to(bundle)))
    return archive


def pick_bundle_dir() -> Path:
    """构建目录被占用时递增后缀；压缩包内仍是 newdiag/。"""
    base = DEPLOY / 'newdiag'
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = DEPLOY / f'newdiag-{index}'
        if not candidate.exists():
            return candidate
    raise SystemExit('deploy 下没有可用目录名')


def main() -> None:
    global BUNDLE
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-build', action='store_true', help='复用已有 dist')
    args = parser.parse_args()

    if not args.skip_build or not (ROOT / 'dist' / 'index.html').is_file():
        build_frontend()

    BUNDLE = pick_bundle_dir()
    BUNDLE.mkdir(parents=True)

    runtime = extract_python(fetch_python())
    install_deps(runtime)
    copy_app()
    write_launchers()

    total = sum(p.stat().st_size for p in BUNDLE.rglob('*') if p.is_file())
    print(f'目录：{BUNDLE}（{total / 1024 / 1024:.0f} MB）')
    print(f'压缩包：{zip_bundle(BUNDLE)}')


if __name__ == '__main__':
    main()
