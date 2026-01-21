@echo off
REM Build Windows single-file exe with PyInstaller
IF NOT EXIST dist mkdir dist
pyinstaller --onefile --noconsole --name JarvisAssistant src\jarvis\main.py
echo Done. Check dist\JarvisAssistant.exe
