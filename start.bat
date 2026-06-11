@echo off
title ChatAtender - Servidor
cd /d "%~dp0"
echo ============================================
echo   ChatAtender - Iniciando...
echo   Acesse: http://localhost:5000
echo   Login: medeirosassessor.adv@gmail.com
echo   Senha: Aa213780@
echo ============================================

:loop
echo [%date% %time%] Iniciando servidor...
"C:\Users\wmmar\AppData\Local\Programs\Python\Python314\python.exe" app.py
echo [%date% %time%] Servidor parou. Reiniciando em 3 segundos...
timeout /t 3 /nobreak >nul
goto loop
