#!/bin/bash
set -e

# Change to the directory of the script
cd "$(dirname "$0")"

# Detect Python executable
if command -v python3.11 &>/dev/null; then
    PYTHON_CMD="python3.11"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "Error: Python is not installed."
    exit 1
fi

# Create venv if missing
if [ ! -d "venv" ]; then
    echo "Creating virtual environment using $PYTHON_CMD..."
    $PYTHON_CMD -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install requirements
echo "Installing dependencies..."
pip install -r requirements.txt

# Run database setup (table creation)
echo "Running database setup..."
python -c "from app.database import engine, Base; from app import models; Base.metadata.create_all(bind=engine)"

# Run the server
echo "Starting Uvicorn server..."
uvicorn app.main:app --host 127.0.0.1 --port 5001 --workers 4

