"""Minimal OSC 1.0 message codec over UDP, so the bridge needs no dependencies.

Only plain messages are handled (no bundles), which is all AbletonOSC sends back.
"""

import logging
import queue
import socket
import struct
import threading
import time

log = logging.getLogger(__name__)


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


class _Batch:
    """Requests waiting for replies. Filled in by the receive loop."""

    def __init__(self, keys, key_len):
        self.pending = set(keys)
        self.key_len = key_len
        self.replies = {}
        self.last_reply = time.monotonic()

    def offer(self, address, args):
        n = self.key_len.get(address, 0)
        key = (address, *args[:n])
        if key not in self.pending:
            return False
        self.pending.discard(key)
        self.replies[key] = args[n:]
        self.last_reply = time.monotonic()
        return True


class OscClient:
    """Sends OSC requests and routes what comes back, which arrives on a separate port.

    One background thread receives everything. A message that answers a waiting request
    goes to that request; anything else (listener pushes, late replies) goes to the
    callbacks subscribed to its address, which run on their own dispatch thread so they
    can make requests themselves.
    """

    def __init__(self, host="127.0.0.1", send_port=11000, receive_port=11001):
        self._target = (host, send_port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Large batches (one request per device parameter) produce many replies at once.
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        try:
            self._sock.bind(("0.0.0.0", receive_port))
        except OSError as exc:
            self._sock.close()
            raise ConnectionError(
                f"Port {receive_port} is busy. Is another AbletonOSC client running?"
            ) from exc
        # Lets the receive loop notice close() without a message arriving.
        self._sock.settimeout(0.2)

        self._cond = threading.Condition()
        self._batches = []
        self._subscribers = {}
        self._events = queue.Queue()
        self._closed = threading.Event()
        self._receiver = threading.Thread(target=self._receive_loop, name="osc-receive", daemon=True)
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="osc-dispatch", daemon=True)
        self._receiver.start()
        self._dispatcher.start()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._events.put(None)
        self._receiver.join(timeout=1)
        self._dispatcher.join(timeout=1)
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, address, args=()):
        self._sock.sendto(encode_message(address, list(args)), self._target)

    def subscribe(self, address, callback):
        """Call `callback(args)` for each message on `address` that no request claimed.

        Returns a function that removes the subscription.
        """
        with self._cond:
            self._subscribers.setdefault(address, []).append(callback)

        def unsubscribe():
            with self._cond:
                callbacks = self._subscribers.get(address, [])
                if callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    def _receive_loop(self):
        while not self._closed.is_set():
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                address, args = decode_message(data)
            except (ValueError, IndexError, struct.error):
                continue
            with self._cond:
                claimed = any(batch.offer(address, args) for batch in self._batches)
                if claimed:
                    self._cond.notify_all()
                    continue
                callbacks = list(self._subscribers.get(address, ()))
            for callback in callbacks:
                self._events.put((callback, args))

    def _dispatch_loop(self):
        while True:
            item = self._events.get()
            if item is None:
                break
            callback, args = item
            try:
                callback(args)
            except Exception:
                log.exception("OSC subscriber failed")

    def request_many(self, requests, key_len, timeout=2.0):
        """Send every (address, args) request, then wait for all replies.

        Replies are matched to requests by address plus the first `key_len[address]`
        arguments (the track/device/parameter ids AbletonOSC echoes back). Returns a
        dict of (address, *ids) -> remaining reply args. Missing replies are left out.
        The timeout restarts with each reply, since a reply means Live is still answering.
        """
        requests = [(address, list(args)) for address, args in requests]
        batch = _Batch(
            [(address, *args[:key_len.get(address, 0)]) for address, args in requests], key_len
        )
        with self._cond:
            # Register before sending, so a fast reply can't slip past.
            self._batches.append(batch)
        try:
            for address, args in requests:
                self.send(address, args)
            with self._cond:
                while batch.pending and not self._closed.is_set():
                    remaining = batch.last_reply + timeout - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)
        finally:
            with self._cond:
                self._batches.remove(batch)
        return batch.replies

    def request(self, address, args=(), key_len=0, timeout=2.0):
        """Send one request and return its reply args, or None on timeout."""
        replies = self.request_many([(address, list(args))], {address: key_len}, timeout)
        return replies.get((address, *list(args)[:key_len]))
