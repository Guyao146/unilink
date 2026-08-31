@echo off
rem ============================================================
rem  UniLink 一键初始化并推送 GitHub
rem  前置：本机已安装 Git（https://git-scm.com/download/win）
rem ============================================================
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo [UniLink] 未检测到 Git，请先安装：https://git-scm.com/download/win
    pause
    exit /b 1
)

if not exist .git (
    echo [UniLink] 初始化仓库...
    git init -b main || goto :fail
    git add .
    git commit -m "UniLink v1.1 手机电脑互联助手（服务器+PC端+Android端）"
)

echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │ 第一步：浏览器打开 https://github.com/new 创建一个空仓库   │
echo  │   - Repository name 随意，如 unilink                      │
echo  │   - 建议选 Private（含个人通讯配置）                       │
echo  │   - ⚠ 不要勾选 Add README / .gitignore / license          │
echo  └─────────────────────────────────────────────────────────┘
set "REMOTE="
set /p REMOTE=第二步：粘贴仓库地址后回车（如 https://github.com/yourname/unilink.git）: 
if "%REMOTE%"=="" (
    echo [UniLink] 未输入地址，已取消
    pause
    exit /b 1
)

git remote remove origin >nul 2>nul
git remote add origin "%REMOTE%"

echo [UniLink] 推送中...（如弹出浏览器登录窗口，按提示授权即可）
git push -u origin main
if errorlevel 1 (
    echo.
    echo [UniLink] 推送失败：常见原因是仓库非空或凭据问题，
    echo           可改用 SSH 地址重试，或删除远端仓库重建空仓库。
    pause
    exit /b 1
)

echo.
echo  ✅ 推送成功！接下来云端打包 APK：
echo     仓库页 → Actions → Build Android APK → Run workflow
echo     完成后在本次运行底部 Artifacts 下载 UniLink-debug-apk.zip
echo.
pause
exit /b 0

:fail
echo [UniLink] git 初始化失败，请把上方报错反馈
pause
exit /b 1
