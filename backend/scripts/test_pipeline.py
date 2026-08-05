#!/usr/bin/env python3
"""HospiCall pipeline test: verifies imports + runs the brain against a live Ollama."""
import sys
import os
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from tqdm import tqdm  # noqa: E402
from app.config import settings  # noqa: E402

print("=== HospiCall Pipeline Test ===")

ok = True

# 1. imports
with tqdm(total=4, desc="Importing modules", ascii=True) as bar:
    try:
        from app.services.brain import Brain
        bar.update(1)
        from app.services.voice import Voice
        bar.update(1)
        from app.services.session import SessionManager
        bar.update(1)
        from app.db import init_db
        bar.update(1)
        print(f"OK imports. App={settings.app_name}, Model={settings.llm_model}")
    except Exception as e:
        print(f"FAIL import: {e}")
        sys.exit(1)

# 2. live Ollama check
brain = Brain(settings.ollama_url, settings.llm_model)
print("\n--- Live test against Ollama ---")
try:
    t0 = time.time()
    intent = brain.classify_intent("I want a cardiologist appointment tomorrow")
    print(f"OK intent classification [{time.time()-t0:.2f}s]: {intent}")
except Exception as e:
    ok = False
    print(f"FAIL Ollama not reachable: {e}")

try:
    t0 = time.time()
    resp = brain.generate_response(
        [{"role": "patient", "content": "I want a cardiologist appointment tomorrow"}],
        {"intent": "book"},
    )
    print(f"OK response generation [{time.time()-t0:.2f}s]: {resp}")
except Exception as e:
    ok = False
    print(f"FAIL response generation: {e}")

print("\n" + ("PIPELINE PASSED" if ok else "PIPELINE PARTIAL — check errors above"))
sys.exit(0 if ok else 1)
