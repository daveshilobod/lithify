#!/usr/bin/env python3
"""
Quick start script for lithify development.
"""

import subprocess
import sys
from pathlib import Path

def main():
    print("Lithify Quick Start\n")
    
    if sys.version_info < (3, 11):
        print(f"Python 3.11+ required, you have {sys.version}")
        sys.exit(1)
    
    print(f"Using Python {sys.version}")
    
    # Install in dev mode
    print("Installing lithify in development mode...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"])
    
    print("Testing installation...")
    subprocess.run([sys.executable, "-m", "lithify.cli", "info"])
    
    print("Ready to lithify!")
    print("\nTry these commands:")
    print("  lithify info                    # See mutability modes")
    print("  lithify diagnose                 # Check environment")
    print("  python tests/test_basic.py     # Run basic tests")
    print("\nGenerate from example schemas:")
    print("  lithify generate --schemas examples/schemas --models-out examples/models --package-name test")

if __name__ == "__main__":
    main()
