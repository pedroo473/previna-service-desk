@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Criando ambiente virtual Python...
    py -m venv .venv
    if errorlevel 1 (
        echo ERRO: nao foi possivel criar o ambiente virtual. Verifique se o Python esta instalado.
        pause
        exit /b 1
    )
)
echo [2/3] Conferindo dependencias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERRO: falha ao instalar dependencias. Verifique a conexao e tente novamente.
    pause
    exit /b 1
)
echo [3/3] Iniciando Previna Chamados localmente...
echo Acesse http://127.0.0.1:5000 no navegador.
".venv\Scripts\python.exe" app.py
pause
