@echo off
REM === Seismic Volume Explorer launcher ===
REM Bu skript fresh checkout-da her sheyi avtomatik qurasdirir.
cd /d "%~dp0"
REM 1. Virtual environment yoxdursa yarat
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: python -m venv failed. Is Python installed and on PATH?
        pause
        exit /b 1
    )
    echo Installing dependencies...
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
)
REM 2. C# modulu yoxdursa kompil et
if not exist csharp\bin\SpectrumService.exe (
    if exist csharp\build.bat (
        echo Building C# spectrum module...
        call csharp\build.bat
    )
)
REM 3. Sintetik data yoxdursa yarat
if not exist data\seismic_synthetic.npy (
    if exist tools\make_synthetic.py (
        echo Generating synthetic volume...
        .venv\Scripts\python.exe tools\make_synthetic.py
    )
)
REM 4. Proqrami isle sal
echo Starting Seismic Volume Explorer...
.venv\Scripts\python.exe -m app.main %*