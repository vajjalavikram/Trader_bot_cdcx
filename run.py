#!/usr/bin/env python3
"""
Launch the CoinDCX Futures Dip/Rise trading bot from the command line.

Configuration precedence (highest → lowest):
  1. runtime_config.json  (written by the Streamlit UI or manually)
  2. .env file            (loaded via python-dotenv)
  3. Environment variables
"""

import os
import sys

# Load .env file if python-dotenv is installed and .env exists
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded environment from {env_path}")
except ImportError:
    pass

# Re-import config AFTER loading .env so env vars are picked up
import importlib
import bot.config
importlib.reload(bot.config)

# Apply runtime_config.json on top (if it exists)
runtime_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
if os.path.exists(runtime_path):
    bot.config.load_from_runtime_config(runtime_path)
    print(f"Applied runtime config from {runtime_path}")

from bot.main import run, BotError

if __name__ == "__main__":
    try:
        run()
    except BotError as exc:
        print(f"\nBot error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        sys.exit(0)
