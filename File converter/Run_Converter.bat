@echo off
title OmniConvert Pro - Bulk File Converter
cd /d "%~dp0"
start "" pythonw "app.py"
if errorlevel 1 (
    python "app.py"
)
