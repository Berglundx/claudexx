@echo off
rem Double-click to open C4D Render Guard on Windows.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw -m c4dguard gui) || (python -m c4dguard gui)
