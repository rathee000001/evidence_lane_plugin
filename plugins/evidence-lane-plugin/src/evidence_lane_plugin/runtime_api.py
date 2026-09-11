"""Explicit remote deployment entrypoint; the hidden bearer tunnel is retired."""
from .remote_server import main, server_config

__all__ = ["main", "server_config"]

if __name__ == "__main__":
    main()