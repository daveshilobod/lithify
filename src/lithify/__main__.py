"""
Allow lithify to be run as a module with python -m lithify
"""

from .cli import app

if __name__ == "__main__":
    app()
