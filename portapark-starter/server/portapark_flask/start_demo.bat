@echo off
title PortaPark v5 Server (AI-Lead + IoT Fusion)
cd /d "%~dp0"
echo ============================================================
echo   PortaPark v5 - Smart Portable Parking Monitor Server
echo   NEUST Sumacab Campus - IoT + Machine Learning Project
echo ============================================================
if exist key.txt (
  set /p GEMINI_API_KEY=<key.txt
  echo [OK] Loaded key.txt - Gemini Cloud AI will take the LEAD.
) else (
  echo [WARNING] No key.txt found in this folder!
  echo           Create key.txt with your Google AI Studio API key on one line.
  echo           Starting in OFFLINE FALLBACK mode ^(Edge Impulse + Sensors^).
)
echo.
python app.py || py app.py
pause
