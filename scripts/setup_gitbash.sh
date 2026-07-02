#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

source .venv/Scripts/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo ""
echo "Setup complete."
echo ""
echo "Run the demo:"
echo "  python main.py"
echo ""
echo "Run tests:"
echo "  python -m pytest -v"
