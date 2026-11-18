@echo off
rem Builds dashboard.html from logs\ and state.json and opens it in the browser.
rem Double-click this file, or make a desktop shortcut to it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m bot.dashboard %*
