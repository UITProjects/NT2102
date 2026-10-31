"""`python -m etwcollect ...` = the `etwcollect` console script (the SYSTEM task calls it this way)."""
import sys

from .cli import main

sys.exit(main())
