@echo off
rem RVP exe化ビルド(=259)。手順の詳細は BUILD_EXE.md を参照。
rem =291: このファイルは CP932(Shift-JIS)+CRLF で保存すること(cmd.exe の要件。
rem UTF-8/LF だと何も実行されずに閉じる)。
rem 初回のみ: py -m pip install pyinstaller
rem tkinterdnd2 を入れていない環境では --collect-all tkinterdnd2 の行を削る。
cd /d "%~dp0"

py -m PyInstaller rvp_launcher.py --name RVP --onedir --windowed ^
  --icon icon\rvp.ico ^
  --add-data "rvp\assets;rvp\assets" ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --noconfirm
if errorlevel 1 goto :fail

rem ライセンス類の同梱(配布時は必須。アプリ内ライセンス画面も
rem exeと同じフォルダの THIRD-PARTY-LICENSES.txt を探す=139)
copy /Y LICENSE dist\RVP\ >nul
if errorlevel 1 goto :fail
copy /Y THIRD-PARTY-LICENSES.txt dist\RVP\ >nul
if errorlevel 1 goto :fail
copy /Y README.md dist\RVP\ >nul

echo.
echo ビルド完了: dist\RVP\RVP.exe
echo (LICENSE / THIRD-PARTY-LICENSES.txt / README.md も同梱済み)
echo Releases 用 zip の作り方は BUILD_EXE.md の 5. を参照。
pause
exit /b 0

:fail
echo.
echo ビルドに失敗しました。BUILD_EXE.md の 1. 準備 と 6. ハマりどころ を確認してください。
pause
exit /b 1
