import os


APP_VERSION = os.getenv("PLANIX_API_VERSION", "1.1.4")


__all__ = ["APP_VERSION"]
