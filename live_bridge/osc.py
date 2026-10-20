"""Minimal OSC 1.0 message codec over UDP, so the bridge needs no dependencies.

Only plain messages are handled (no bundles), which is all AbletonOSC sends back.
"""

import socket
import struct
import time


def _pad(data: bytes) -> bytes:
    return data + b"\x00" * (4 - len(data) % 4)


def _encode_string(value: str) -> bytes:
    # OSC strings are null-terminated and padded to a multiple of 4 bytes.
    return _pad(value.encode("utf-8"))


def encode_message(address: str, args=()) -> bytes:
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "T" if arg else "F"
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags += "s"
            payload += _encode_string(arg)
        else:
            raise TypeError(f"Unsupported OSC argument type: {type(arg).__name__}")
    return _encode_string(address) + _encode_string(tags) + payload


def _read_string(data: bytes, offset: int):
    end = data.index(b"\x00", offset)
    value = data[offset:end].decode("utf-8", errors="replace")
    return value, (end // 4 + 1) * 4


def decode_message(data: bytes):
    address, offset = _read_string(data, 0)
    tags, offset = _read_string(data, offset)
    args = []
    for tag in tags[1:]:
        if tag == "i":
            args.append(struct.unpack_from(">i", data, offset)[0])
            offset += 4
        elif tag == "f":
            args.append(struct.unpack_from(">f", data, offset)[0])
            offset += 4
        elif tag == "h":
            args.append(struct.unpack_from(">q", data, offset)[0])
            offset += 8
        elif tag == "d":
            args.append(struct.unpack_from(">d", data, offset)[0])
            offset += 8
        elif tag == "s":
            value, offset = _read_string(data, offset)
            args.append(value)
        elif tag == "b":
            size = struct.unpack_from(">i", data, offset)[0]
            offset += 4
            args.append(data[offset:offset + size])
            offset += (size + 3) // 4 * 4
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag in "NI":
            args.append(None)
        else:
            raise ValueError(f"Unsupported OSC type tag: {tag}")
    return address, args


class OscClient:
    """Sends OSC requests and collects the replies, which arrive on a separate port."""

    def __init__(self, host="127.0.0.1", send_port=11000, receive_port=11001):
        self._target = (host, send_port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Large batches (one request per device parameter) produce many replies at once.
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        try:
            self._sock.bind(("0.0.0.0", receive_port))
        except OSError as exc:
            raise ConnectionError(
                f"Port {receive_port} is busy. Is another AbletonOSC client running?"
            ) from exc

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _drain(self):
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recv(65536)
        except BlockingIOError:
            pass
        finally:
            self._sock.setblocking(True)

    def request_many(self, requests, key_len, timeout=2.0):
        """Send every (address, args) request, then wait for all replies.

        Replies are matched to requests by address plus the first `key_len[address]`
        arguments (the track/device/parameter ids AbletonOSC echoes back). Returns a
        dict of (address, *ids) -> remaining reply args. Missing replies are left out.
        """
        self._drain()
        pending = set()
        for address, args in requests:
            pending.add((address, *args[:key_len.get(address, 0)]))
            self._sock.sendto(encode_message(address, args), self._target)

        replies = {}
        deadline = time.monotonic() + timeout
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._sock.settimeout(remaining)
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                break
            address, args = decode_message(data)
            n = key_len.get(address, 0)
            key = (address, *args[:n])
            if key in pending:
                pending.discard(key)
                replies[key] = args[n:]
                # Each reply means Live is still answering, so keep waiting.
                deadline = time.monotonic() + timeout
        return replies

    def request(self, address, args=(), key_len=0, timeout=2.0):
        """Send one request and return its reply args, or None on timeout."""
        replies = self.request_many([(address, list(args))], {address: key_len}, timeout)
        return replies.get((address, *list(args)[:key_len]))
