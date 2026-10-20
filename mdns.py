"""Minimal stdlib mDNS / DNS-SD browser (RFC 6762/6763), enough to find TVs.

Sends one-shot "legacy unicast" queries from an ephemeral port, so responders answer
straight back to us; no multicast group membership or port 5353 binding needed.
Works the same on macOS, Linux and Windows.
"""
from __future__ import annotations

import random
import socket
import struct
import time
from dataclasses import dataclass, field

MDNS_ADDR = ("224.0.0.251", 5353)
T_A, T_PTR, T_TXT, T_AAAA, T_SRV = 1, 12, 16, 28, 33


@dataclass
class Service:
    type: str        # e.g. "_androidtvremote2._tcp.local."
    instance: str    # e.g. "Living Room TV"
    target: str = ""  # SRV host name
    port: int = 0
    addresses: list[str] = field(default_factory=list)
    txt: dict[str, str] = field(default_factory=dict)


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _query(questions: list[tuple[str, int]]) -> bytes:
    header = struct.pack("!HHHHHH", random.randint(1, 0xFFFF), 0, len(questions), 0, 0, 0)
    return header + b"".join(_encode_name(n) + struct.pack("!HH", t, 1) for n, t in questions)


def _read_name(msg: bytes, off: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        if off >= len(msg):
            raise ValueError("truncated name")
        length = msg[off]
        if length & 0xC0 == 0xC0:  # compression pointer
            if end is None:
                end = off + 2
            off = ((length & 0x3F) << 8) | msg[off + 1]
            jumps += 1
            if jumps > 64:
                raise ValueError("pointer loop")
            continue
        off += 1
        if length == 0:
            break
        labels.append(msg[off:off + length].decode("utf-8", "replace"))
        off += length
    return ".".join(labels) + ".", (end if end is not None else off)


def _parse(msg: bytes):
    """Yield (name, type, rdata-decoded) for every resource record in a response."""
    _id, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", msg, 0)
    if not flags & 0x8000:  # not a response
        return
    off = 12
    for _ in range(qd):
        _, off = _read_name(msg, off)
        off += 4
    for _ in range(an + ns + ar):
        name, off = _read_name(msg, off)
        rtype, _rclass, _ttl, rdlen = struct.unpack_from("!HHIH", msg, off)
        off += 10
        rdata = msg[off:off + rdlen]
        if rtype == T_PTR:
            value = _read_name(msg, off)[0]
        elif rtype == T_SRV:
            _prio, _weight, port = struct.unpack_from("!HHH", msg, off)
            value = (_read_name(msg, off + 6)[0], port)
        elif rtype == T_A and rdlen == 4:
            value = socket.inet_ntop(socket.AF_INET, rdata)
        elif rtype == T_AAAA and rdlen == 16:
            value = socket.inet_ntop(socket.AF_INET6, rdata)
        elif rtype == T_TXT:
            items, i = {}, 0
            while i < len(rdata):
                n = rdata[i]
                entry = rdata[i + 1:i + 1 + n].decode("utf-8", "replace")
                key, _, val = entry.partition("=")
                items[key] = val
                i += 1 + n
            value = items
        else:
            value = None
        off += rdlen
        yield name, rtype, value


def _unescape_instance(full: str, service_type: str) -> str:
    return full[: -len(service_type) - 1] if full.endswith("." + service_type) else full


def browse(service_types: list[str], timeout: float = 2.5) -> list[Service]:
    """Return services of the given types (e.g. "_androidtvremote2._tcp") seen on the LAN."""
    types = [t.rstrip(".") + ".local." for t in service_types]
    records: dict[tuple[str, int], list] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(0.25)
    try:
        sock.bind(("", 0))
        deadline = time.monotonic() + timeout
        next_send, sends = 0.0, 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send and sends < 3:
                questions = [(t, T_PTR) for t in types]
                # Follow-up queries for any instance whose SRV / address we still lack
                for svc in _assemble(types, records):
                    if not svc.target:
                        questions.append((f"{svc.instance}.{svc.type}", T_SRV))
                    elif not svc.addresses:
                        questions.append((svc.target, T_A))
                sock.sendto(_query(questions), MDNS_ADDR)
                sends += 1
                next_send = now + 0.7
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                for name, rtype, value in _parse(data):
                    if value is not None:
                        bucket = records.setdefault((name.lower(), rtype), [])
                        if value not in bucket:
                            bucket.append(value)
            except (ValueError, struct.error):
                continue
    finally:
        sock.close()
    return _assemble(types, records)


def _assemble(types: list[str], records: dict) -> list[Service]:
    found = []
    for t in types:
        for full in records.get((t.lower(), T_PTR), []):
            svc = Service(type=t, instance=_unescape_instance(full, t))
            srv = records.get((full.lower(), T_SRV))
            if srv:
                svc.target, svc.port = srv[0]
                svc.addresses = list(records.get((svc.target.lower(), T_A), []))
            txt = records.get((full.lower(), T_TXT))
            if txt:
                svc.txt = txt[0]
            found.append(svc)
    return found


def resolve(svc: Service) -> str | None:
    """Best IPv4 address for a service: mDNS answer, else the OS resolver."""
    if svc.addresses:
        return svc.addresses[0]
    if svc.target:
        try:
            return socket.getaddrinfo(svc.target.rstrip("."), None, socket.AF_INET)[0][4][0]
        except OSError:
            return None
    return None
