@echo off
rem Builds dist\TenetHub.exe on your own PC (GitHub Actions does the same for releases).
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py") || (set "PY=python")
%PY% -m pip install -r requirements.txt pyinstaller || goto :fail
%PY% -m PyInstaller --noconfirm --onefile --noconsole --name TenetHub ^
  --paths app --add-data "packages;packages" ^
  --collect-all winrt --collect-all soundcard --collect-submodules pystray ^
  --hidden-import hub._bundle ^
  app\tenet_hub.py || goto :fail
echo.
echo Built dist\TenetHub.exe
pause
exit /b 0
:fail
echo Build failed (see above).
pause
exit /b 1
