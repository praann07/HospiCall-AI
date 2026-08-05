#!/usr/bin/env python3
"""
Test script to verify the HospiCall pipeline setup.
This script checks if the core components are properly structured and can be imported.
"""

import sys
import os

print("=== HospiCall Pipeline Test ===")

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from app.config import settings
    print("Config loaded successfully")
    print(f"  App Name: {settings.app_name}")
    print(f"  LLM Model: {settings.llm_model}")
except ImportError as e:
    print(f"Failed to load config: {e}")
    sys.exit(1)