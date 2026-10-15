"""Resource location shared by source checkouts and PyInstaller bundles."""
from pathlib import Path
import sys

RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
