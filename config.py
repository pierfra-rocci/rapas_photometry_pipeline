"""SMTP configuration.

Credentials are resolved from environment variables so that secrets are not
stored in the repository. The historical literals remain only as a fallback for
existing deployments that have not migrated to environment variables yet.
"""

import os

# SMTP settings
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.mail.ovh.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "rpp_support@saf-astronomie.fr")
SMTP_PASS_ENCODED = os.getenv("SMTP_PASS_ENCODED", "ejRVZzNtTVJ6ODM=")
