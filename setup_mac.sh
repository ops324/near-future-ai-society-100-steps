#!/bin/bash
# Setup script for LLM Multi-Agent 2D Simulation

echo "Setting up virtual environment..."

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Playwright のブラウザ（4K HTML レンダラ用）
echo "Installing Playwright chromium..."
playwright install chromium

echo ""
echo "Setup complete!"
echo "（別途: Ollama 起動と 'ollama pull qwen2.5:14b'、IPAGothic フォント導入が必要）"
echo ""
echo "To activate the virtual environment, run:"
echo "  source venv/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"

