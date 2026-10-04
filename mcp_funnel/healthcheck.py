"""Local readiness only. No public requests and no diagnostic secret output."""
import os
from runtime import UID, SOCKET, cli_args, command_json, connected, funnel_correct, gateway_ready, hostname


def check() -> bool:
    status = command_json(cli_args("status", "--json", socket=SOCKET))
    if not connected(status):
        return False  # LocalAPI answering also proves the daemon is alive.
    domain = hostname(status)
    return bool(domain and gateway_ready() and funnel_correct(
        command_json(cli_args("funnel", "status", "--json", socket=SOCKET)), domain))


if __name__ == "__main__":
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(UID)
        os.setuid(UID)
    raise SystemExit(0 if check() else 1)
