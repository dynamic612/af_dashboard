#!/usr/bin/env python3
"""
Run the standalone Flask dashboard (scores + dominance, same API as af get-rank).
Usage:
  cd /root/affine-cortex
  python -m pip install -r standalone_dashboard/requirements.txt   # use same python as below
  python -m standalone_dashboard.run

  Or set API_URL to use a different Affine API (default: https://api.affine.io/api/v1):
  API_URL=http://localhost:1999/api/v1 python -m standalone_dashboard.run
"""
import os
import sys

# Ensure affine-cortex is on path when run as __main__
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from standalone_dashboard.app import app
except ModuleNotFoundError as e:
    if "flask" in str(e).lower():
        print("Flask not found. Install with the same Python you use to run this script:")
        print("  python -m pip install flask requests")
        sys.exit(1)
    raise

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
