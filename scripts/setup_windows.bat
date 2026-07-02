@echo off
cd /d "%~dp0\.."

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo Setup complete.
echo.
echo Run the demo:
echo   python main.py
echo.
echo Run tests:
echo   python -m pytest -v
