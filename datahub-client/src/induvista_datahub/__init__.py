"""InduVista DataHub — edge collector for the INDUVISTA platform.

Reads samples from OPC UA and OPC DA servers and pushes them to the
INDUVISTA backend at /api/ingest. Local SQLite buffer survives
network outages and resumes cleanly on reconnect.

See docs/ARCHITECTURE.md for the design.
"""

__version__ = "0.1.0"
