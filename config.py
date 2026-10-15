"""SMTP configuration.

Credentials are resolved from environment variables only, so that no secret is
stored in the repository. Set ``SMTP_SERVER``, ``SMTP_PORT``, ``SMTP_USER`` and
``SMTP_PASS_ENCODED`` (base64 of the SMTP password) in the service environment.
When they are missing, the password-recovery email endpoint answers with
"Email service is not configured." instead of sending anything.
"""

import os

# SMTP settings
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.mail.ovh.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS_ENCODED = os.getenv("SMTP_PASS_ENCODED", "")
