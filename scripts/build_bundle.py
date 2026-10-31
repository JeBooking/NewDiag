"""打离线包：应用代码 + 前端产物 + 运行时依赖轮子，用目标机器自己的 Python 建虚拟环境。

用法：backend/.venv/Scripts/python scripts/build_bundle.py [--skip-build]

目标机器需自带 Python 3.9+（推荐 3.11 / 64 位）。首次解压后先跑 setup.cmd 建虚拟环境并离线装依赖，
之后双击 start.cmd 即可。产物：deploy/newdiag-win64-<日期>.zip
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'deploy'
PY_VERSION = '3.11'  # 只影响下载的轮子 ABI，与目标机器版本需一致
PY_ABI = 'cp311'
RUNTIME_REQS = ROOT / 'backend' / 'requirements-runtime.txt'


def build_frontend() -> None:
    print('+ pnpm build')
    subprocess.run('pnpm build', cwd=ROOT, shell=True, check=True)


def download_wheels(bundle: Path) -> None:
    wheels = bundle / 'wheels'
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'download',
            '--dest',
            str(wheels),
            '--only-binary=:all:',
            '--python-version',
            PY_VERSION,
            '--implementation',
            'cp',
            '--abi',
            PY_ABI,
            '--platform',
            'win_amd64',
            '--index-url',
            'https://pypi.org/simple',
            '-r',
            str(RUNTIME_REQS),
        ],
        check=True,
    )


def copy_files(bundle: Path) -> None:
    import shutil

    shutil.copytree(
        ROOT / 'backend' / 'app',
        bundle / 'app',
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
    )
    shutil.copytree(ROOT / 'examples', bundle / 'examples')
    shutil.copytree(ROOT / 'dist', bundle / 'web')
    shutil.copy2(RUNTIME_REQS, bundle / 'requirements-runtime.txt')
    (bundle / 'data').mkdir(parents=True, exist_ok=True)
    (bundle / 'logs').mkdir(parents=True, exist_ok=True)


SETUP = r'''@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "VENV=%HERE%runtime\venv"
echo 正在创建虚拟环境（需要本机 Python 3.9+，推荐 3.11 64 位）...

set "BASE="
py -3.11 -V >nul 2>nul && set "BASE=py -3.11"
if "%BASE%"=="" (py -V >nul 2>nul && set "BASE=py")
if "%BASE%"=="" (python -V >nul 2>nul && set "BASE=python")
if "%BASE%"=="" (
  echo 未找到 Python，请先安装 Python 3.9+ 并加入 PATH。
  pause
  exit /b 1
)
echo 使用解释器：%BASE%

%BASE% -m venv "%VENV%"
"%VENV%\Scripts\python.exe" -m pip install --no-index --find-links "%HERE%wheels" -r "%HERE%requirements-runtime.txt"
if errorlevel 1 (
  echo 安装失败，检查 Python 是否为 64 位、版本是否匹配（默认按 3.11 打包）。
  pause
  exit /b 1
)
echo.
echo 完成。以后双击 start.cmd 启动。
pause
'''

START = r'''@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=%HERE%runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 首次使用请先运行 setup.cmd 安装依赖。
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NEWDIAG_DB=%HERE%data\newdiag.db"
set "NEWDIAG_WEB_DIR=%HERE%web"
set "NEWDIAG_LOG_DIR=%HERE%logs"
if "%NEWDIAG_LOG_LEVEL%"=="" set "NEWDIAG_LOG_LEVEL=INFO"
echo 诊断流程平台启动中：http://127.0.0.1:8765/
echo 日志：%HERE%logs\app.log    停止：Ctrl+C
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir "%HERE%"
endlocal
'''

START_LAN = r'''@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=%HERE%runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 首次使用请先运行 setup.cmd 安装依赖。
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NEWDIAG_DB=%HERE%data\newdiag.db"
set "NEWDIAG_WEB_DIR=%HERE%web"
set "NEWDIAG_LOG_DIR=%HERE%logs"
if "%NEWDIAG_LOG_LEVEL%"=="" set "NEWDIAG_LOG_LEVEL=INFO"
echo 诊断流程平台启动中（监听所有网卡）：http://<本机IP>:8765/
echo 注意：这套没有登录，仅限内网自用。
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir "%HERE%"
endlocal
'''

README = '''# 诊断流程平台（离线包）

目标机器需自带 Python 3.9+（推荐 3.11 / 64 位）。

## 首次使用

1. 双击 `setup.cmd`：创建虚拟环境并从本地 `wheels/` 离线安装依赖，无需联网。
2. 双击 `start.cmd`，浏览器打开 http://127.0.0.1:8765/ 。

内网他机访问用 `start-lan.cmd`（监听所有网卡），地址 http://<本机IP>:8765/ 。

## 登记业务工程

1. 打开「工程」页，登记业务 Python 工程：
   - 路径：本机工程目录绝对路径（示例 `examples\\cooling`）
   - 模块：如 `cooling.diagnosis`
   - 解释器：业务工程自己的 Python（平台不会往里面装依赖）
2. 打开「流程」页，新建、画图、绑定、保存、发布。
3. 版本列表点「执行」，填入启动输入（如 `{"voltage": 198.4}`）。
4. 处置待办在右侧面板点「完成」或「回退」。

## 说明

- 平台依赖在 `runtime\\venv`，由 `setup.cmd` 离线安装，`wheels/` 默认按 Python 3.11 / 64 位打包；
  如果目标机器版本不同，`setup.cmd` 会报错，需要重新打包。
- 数据库：`data\\newdiag.db`；附件：`data\\files\\`；日志：`logs\\app.log`
- 调试：先 `set NEWDIAG_LOG_LEVEL=DEBUG` 再运行 start.cmd
- 平台只通过子进程调用业务函数，业务依赖（numpy、串口 SDK 等）装在业务解释器里
- 改了函数签名要重新发布，执行前会被签名指纹拦下
'''


def write_launchers(bundle: Path) -> None:
    (bundle / 'setup.cmd').write_text(SETUP, encoding='utf-8-sig')
    (bundle / 'start.cmd').write_text(START, encoding='utf-8-sig')
    (bundle / 'start-lan.cmd').write_text(START_LAN, encoding='utf-8-sig')
    (bundle / 'README.md').write_text(README, encoding='utf-8')


def zip_bundle(bundle: Path) -> Path:
    stamp = date.today().strftime('%Y%m%d')
    archive = DEPLOY / f'newdiag-win64-{stamp}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zf.write(path, arcname=str(Path('newdiag') / path.relative_to(bundle)))
    return archive


def pick_bundle_dir() -> Path:
    base = DEPLOY / 'newdiag'
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = DEPLOY / f'newdiag-{index}'
        if not candidate.exists():
            return candidate
    raise SystemExit('deploy 下没有可用目录名')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-build', action='store_true', help='复用已有 dist')
    args = parser.parse_args()

    if not args.skip_build or not (ROOT / 'dist' / 'index.html').is_file():
        build_frontend()

    bundle = pick_bundle_dir()
    bundle.mkdir(parents=True)

    download_wheels(bundle)
    copy_files(bundle)
    write_launchers(bundle)

    total = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())
    print(f'目录：{bundle}（{total / 1024 / 1024:.0f} MB）')
    print(f'压缩包：{zip_bundle(bundle)}')


if __name__ == '__main__':
    main()
