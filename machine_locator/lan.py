"""Work out the address an iPad (or phone, or any other device) should open.

Nothing else in the app imports this. It exists so a launcher can print the
right thing on screen before handing over to the server, without any of the
app's own behaviour changing.

Run it directly:

    python -m machine_locator.lan 5000
"""

from __future__ import annotations

import socket
import sys
from typing import List, Optional


def local_ip() -> Optional[str]:
    """This machine's address on the local network.

    Opens a UDP socket toward a public address and reads back which local
    interface the routing table chose. UDP is connectionless, so nothing is
    actually sent and it works with no internet connection -- it is just a way
    to ask the OS "which of my addresses would you use to leave the house?",
    which is exactly the one the iPad needs.
    """
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.4)
            sock.connect((probe, 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
        except OSError:
            continue
        finally:
            sock.close()

    # Fall back to whatever the hostname resolves to.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


def qr_lines(text: str) -> List[str]:
    """A scannable QR block for the terminal, or [] if we can't draw one."""
    try:
        import qrcode  # optional; the address alone works fine without it
    except ImportError:
        return []
    try:
        code = qrcode.QRCode(border=1)
        code.add_data(text)
        code.make(fit=True)
        matrix = code.get_matrix()
    except Exception:
        return []

    # Two rows per character cell using half-block glyphs, so the code stays
    # square in a terminal where cells are twice as tall as they are wide.
    lines: List[str] = []
    for top in range(0, len(matrix), 2):
        row = ""
        for col in range(len(matrix[top])):
            upper = matrix[top][col]
            lower = matrix[top + 1][col] if top + 1 < len(matrix) else False
            if upper and lower:
                row += "█"
            elif upper:
                row += "▀"
            elif lower:
                row += "▄"
            else:
                row += " "
        lines.append(row)
    return lines


def banner(port: int = 5000) -> str:
    address = local_ip()
    if not address:
        return (
            "\n  Could not work out this computer's network address.\n"
            "  Check you are connected to Wi-Fi, then try again.\n"
        )

    url = f"http://{address}:{port}"
    out = [
        "",
        "  ┌" + "─" * 52 + "┐",
        "  │" + "  ON YOUR IPAD, OPEN SAFARI AND GO TO:".ljust(52) + "│",
        "  │" + " " * 52 + "│",
        "  │" + f"      {url}".ljust(52) + "│",
        "  │" + " " * 52 + "│",
        "  └" + "─" * 52 + "┘",
        "",
    ]

    code = qr_lines(url)
    if code:
        out.append("  ...or point the iPad camera at this:")
        out.append("")
        out.extend("    " + line for line in code)
        out.append("")

    out += [
        "  Both devices must be on the same Wi-Fi.",
        "",
    ]
    return "\n".join(out)


def reachable(port: int, timeout: float = 1.5) -> bool:
    """Can something actually connect to us on the network address?

    Checked over the real interface rather than localhost, because binding
    correctly and being reachable are different things -- a firewall sits
    between them.
    """
    address = local_ip()
    if not address:
        return False
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def trouble(port: int) -> str:
    """Shown when the address does not answer, so the window the user is
    already looking at explains itself."""
    return "\n".join([
        "",
        "  Could not reach this computer on its own network address.",
        "",
        "  The iPad would see a blank or 'cannot connect' page. Usually:",
        "",
        "    1. A firewall prompt is waiting for an answer. Look for a box",
        "       asking whether to allow incoming connections, and say Allow.",
        "    2. This computer is on a wired connection or a different Wi-Fi",
        "       network from the iPad. They have to be on the same one.",
        "    3. Some guest and public Wi-Fi networks block devices from",
        "       seeing each other. A home network normally does not.",
        "",
        "  The app is still running -- it just may not be reachable yet.",
        "",
    ])


def main() -> int:
    args = [a for a in sys.argv[1:]]
    mode = ""
    if args and args[0].startswith("--"):
        mode = args.pop(0)

    port = 5000
    if args:
        try:
            port = int(args[0])
        except ValueError:
            pass

    if mode == "--ip":
        address = local_ip()
        if not address:
            return 1
        print(address)
        return 0

    if mode == "--verify":
        if reachable(port):
            print(f"  Confirmed: reachable at http://{local_ip()}:{port}")
            return 0
        print(trouble(port))
        return 1

    print(banner(port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
