@echo off
chcp 65001 > nul
title Digital Oscilloscope [Mock]

.venv\python.exe main.py --mock

if errorlevel 1 (
    echo.
    echo Program exited with code %errorlevel%
    pause
)
