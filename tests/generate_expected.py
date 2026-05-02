import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from backend.app.analyzers.python_analyzer import analyze_python
import json

repo_root = "tests/fixtures/sample_py"
records = analyze_python(repo_root)
with open("tests/expected/phase1_python.json", "w") as f:
    json.dump(records, f, default=lambda o: o.__dict__, indent=2)