@echo off
chcp 65001 > nul
title Digital Oscilloscope

.venv\python.exe main.py

if errorlevel 1 (
    echo.
    echo Program exited with code %errorlevel%
    pause
)
