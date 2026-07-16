import sys
from pathlib import Path

# ponytail: ensure repo root is importable so `from agent import ...` works
# regardless of which directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).parent))
