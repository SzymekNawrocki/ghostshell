import os

# db.py reads DATABASE_URL at import time (os.environ["DATABASE_URL"]), and
# scan_tools.py imports db — so anything that imports scan_tools needs this
# set before that import happens, even though these tests never open a real
# connection. setdefault so a real .env value (if one is exported into the
# test environment) still wins.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
