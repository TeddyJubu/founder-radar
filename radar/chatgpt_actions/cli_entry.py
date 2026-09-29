"""Entry point for ``python -m radar.chatgpt_actions`` / systemd ExecStart."""

from __future__ import annotations

from radar.chatgpt_actions.server import listen_port, serve_forever


def main() -> None:
    serve_forever(port=listen_port())


if __name__ == "__main__":
    main()
