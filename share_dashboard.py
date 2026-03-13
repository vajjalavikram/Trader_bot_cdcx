"""
Expose the Streamlit dashboard via a public ngrok URL.

Usage:
    python share_dashboard.py

Requires a free ngrok account and auth token configured via:
    ngrok config add-authtoken <YOUR_TOKEN>

Or set the NGROK_AUTHTOKEN environment variable before running.
"""

import os
import signal
import subprocess
import sys
import time

from pyngrok import ngrok

STREAMLIT_PORT = 8501


def main():
    auth_token = os.getenv("NGROK_AUTHTOKEN")
    if auth_token:
        ngrok.set_auth_token(auth_token)

    streamlit_process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "ui.py",
         "--server.port", str(STREAMLIT_PORT),
         "--server.headless", "true"],
    )

    time.sleep(4)

    if streamlit_process.poll() is not None:
        print("Streamlit failed to start. Check ui.py for errors.")
        sys.exit(1)

    tunnel = ngrok.connect(STREAMLIT_PORT)
    public_url = tunnel.public_url

    print("\n" + "=" * 50)
    print("  Public Dashboard URL:")
    print(f"  {public_url}")
    print("=" * 50)
    print("\nShare this URL over Slack to access the dashboard.")
    print("Press Ctrl+C to stop.\n")

    def _shutdown(sig, frame):
        print("\nShutting down…")
        ngrok.disconnect(public_url)
        streamlit_process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        streamlit_process.wait()
    finally:
        ngrok.kill()


if __name__ == "__main__":
    main()
