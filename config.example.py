"""Template for the SMTP configuration.

Copy this file to ``config.py`` and fill in real values, or (preferred) set the
matching environment variables ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USER`` and
``SMTP_PASS_ENCODED``. ``config.py`` is git-ignored and must never be committed.

``SMTP_PASS_ENCODED`` is the SMTP password encoded with base64 (as done by the
legacy ``encode_pass.py`` helper). Base64 is only an encoding, so treat it as a
plain-text secret.
"""

import os

# SMTP settings
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "user@example.com")
SMTP_PASS_ENCODED = os.getenv("SMTP_PASS_ENCODED", "")
