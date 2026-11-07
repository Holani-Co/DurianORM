import logging

import fastmcp  # noqa: F401 - sets up its own logging on import; quietened below

# The server's own lines (refusals at the door, one per API call) and FastMCP's (each 401, each failed tool call) are
# checked by the tests where they matter, not printed.
logging.getLogger('orm.mcp').addHandler(logging.NullHandler())
logging.getLogger('orm.mcp').propagate = False
logging.getLogger('fastmcp').setLevel(logging.CRITICAL)
