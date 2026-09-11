"""Private stdlib bootstrap. Wait for parent ownership before spawning a tool."""
import json
import os
import subprocess
import sys


def main():
    payload = sys.stdin.buffer.read(65537)
    if len(payload) > 65536:
        return 120
    command = json.loads(payload)
    if (not isinstance(command, list) or not command or not os.path.isabs(command[0])
            or any(not isinstance(value, str) or '\0' in value for value in command)):
        return 120
    # Only the parent internal adapter supplies argv, via a private pipe after
    # Job assignment. No project imports, module dispatch or public argv route.
    return subprocess.call(command, stdin=subprocess.DEVNULL, shell=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


if __name__ == '__main__':
    raise SystemExit(main())
