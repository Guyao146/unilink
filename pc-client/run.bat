@echo off
rem ============================================================
rem  UniLink PC 端一键启动
rem  1. 用「同一个解释器」完成 pip 安装与运行，避免多版本 Python
rem     导致的 ModuleNotFoundError 问题
rem  2. 多版本并存时优先使用 Python 3.11 —— 可选功能 winsdk
rem     （电脑自身通知转发）仅支持 3.7~3.11
rem  如需强制其它版本：编辑下面 PYPREF，例如 -3.12 / 留空表示不指定
rem ============================================================
setlocal
cd /d "%~dp0"
set PYPREF=-3.11

set PY=

rem ── 1) 首选：py 启动器 + 指定版本（存在才用）──
where py >nul 2>nul
if not errorlevel 1 (
    %PYPREF% -c "import sys" >nul 2>nul && set PY=py %PYPREF%
)

rem ── 2) 回退：py 默认版本 ──
if not defined PY ( where py >nul 2>nul && set PY=py )

rem ── 3) 再回退：PATH 里的 python ──
if not defined PY ( where python >nul 2>nul && set PY=python )

if not defined PY (
    echo [UniLink] 未找到 Python，请安装 3.9+ 并勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo [UniLink] 使用解释器: %PY%
%PY% -c "import sys;print('[UniLink] Python 版本:',sys.version.split()[0])"
%PY% -c "import sys;print('[UniLink] 解释器路径:',sys.executable)"

echo [UniLink] 正在安装依赖...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [UniLink] 依赖安装失败，请将上方报错截图反馈
    pause
    exit /b 1
)

rem ── 可选功能依赖：winsdk 仅支持 3.7~3.11，装不上只影响「电脑通知→手机」──
echo [UniLink] 检查可选组件 winsdk（用于转发电脑自身通知）...
%PY% -c "import winsdk" >nul 2>nul
if errorlevel 1 (
    %PY% -m pip install winsdk==1.0.0b10
    if errorlevel 1 echo [UniLink] winsdk 未安装：仅「电脑自身通知→手机」不可用，其余功能正常
) else (
    echo [UniLink] winsdk 已就绪
)

echo [UniLink] 启动主程序...
%PY% main.py
if errorlevel 1 pause
endlocal
