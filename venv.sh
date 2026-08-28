#!/bin/bash

# use bash strict mode
set -euo pipefail

# Name of the virtual environment directory
VENV_DIR=".venv"

# Check if the virtual environment directory already exists
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists. Activating..."
    source "$VENV_DIR/bin/activate"
else
    echo "Virtual environment not found. Creating..."
    python3 -m venv "$VENV_DIR"

    echo "Activating virtual environment..."
    source "$VENV_DIR/bin/activate"

    # Upgrade pip to the latest version
    pip install --upgrade pip

    # Install requirements if the file exists
    if [ -f "requirements.txt" ]; then
        echo "Installing requirements.txt..."
        pip install -r requirements.txt
    fi

    # Install development requirements if the file exists
    if [ -f "dev-requirements.txt" ]; then
        echo "Installing dev-requirements.txt..."
        pip install -r dev-requirements.txt
    fi

    echo "Setup and installation completed successfully!"
fi
