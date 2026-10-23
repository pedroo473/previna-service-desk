@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERRO: ambiente virtual nao encontrado. Execute INICIAR_LOCAL.bat primeiro.
    pause
    exit /b 1
)
echo Executando testes automatizados do Previna Chamados...
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 (
    echo.
    echo Um ou mais testes falharam. Copie a saida completa para diagnostico.
) else (
    echo.
    echo Testes concluidos sem falhas.
)
pause
