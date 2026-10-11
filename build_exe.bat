@echo off
chcp 65001 > nul
echo ========================================================
echo   複合機 印刷拡縮検証用PDF生成ツール (PDFMaker)
echo   スタンドアロンEXE ビルドスクリプト (Windows 11)
echo ========================================================
echo.

cd /d "%~dp0"

echo [1/4] Python仮想環境 (.venv) の確認・セットアップ...
if not exist ".venv" (
    py -m venv .venv
    if errorlevel 1 (
        echo [エラー] Python仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
)

echo [2/4] pip および必要ライブラリのインストール...
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [エラー] ライブラリのインストールに失敗しました。
    pause
    exit /b 1
)

echo [3/4] 単体テスト (pytest) の実行...
.\.venv\Scripts\python -m pytest -q
if errorlevel 1 (
    echo [エラー] 単体テストが失敗しました。ビルドを中断します。
    pause
    exit /b 1
)

echo [4/4] PyInstallerによる単一exeファイルのビルド中...
.\.venv\Scripts\python -m PyInstaller --onefile --noconsole --clean --icon=app.ico --add-data "app.ico;." --name "PDFMaker" app.py
if errorlevel 1 (
    echo [エラー] PyInstallerビルドに失敗しました。
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   ビルド完了!
echo   出力先: dist\PDFMaker.exe
echo ========================================================
echo.
pause
