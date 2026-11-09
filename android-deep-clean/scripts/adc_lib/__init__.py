"""android-deep-clean (ADC) core library.

Design goals:
- read-only scan by default;
- plan/select/token/execute separation;
- quarantine before permanent deletion;
- transport abstraction (adb / hdc / rish / local / mock).
"""

__version__ = "1.9.3"
SCHEMA_VERSION = 1
