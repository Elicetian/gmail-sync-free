import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

sys.modules.setdefault("boto3", MagicMock())
