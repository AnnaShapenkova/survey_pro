"""One image, independent collector / worker / bot / web processes."""
import importlib
import os
import sys

from dotenv import load_dotenv

load_dotenv()
BLOCKS = {"collector", "worker", "bot", "web", "scheduler", "import", "check"}


def main():
    block = sys.argv[1] if len(sys.argv) > 1 else os.getenv("BLOCK", os.getenv("RAILWAY_SERVICE_NAME", ""))
    if block not in BLOCKS:
        raise SystemExit("Usage: python main.py " + "|".join(sorted(BLOCKS)))
    module = {"import": "kb.ingest", "check": "kb.check", "scheduler": "kb.scheduler"}.get(block, f"{block}.main")
    importlib.import_module(module).main()


if __name__ == "__main__":
    main()
