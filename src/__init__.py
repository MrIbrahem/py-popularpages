
import sys
from pathlib import Path

main_path = Path(__file__).parent
sys.path.insert(0, str(main_path))

__version__ = "0.1.0"
