import collections
import socket


class FakeSocket:
    def __init__(
        self,
        responses: list[bytes],
    ) -> None:
        self.recv_bufs = collections.deque(responses)
        self.sent: list[bytes] = []
        self.closed = False
        self.connections: list[tuple[str, int]] = []

    def sendall(
        self,
        value: bytes,
    ) -> None:
        self.sent.append(value)

    def recv(
        self,
        size: int,
    ) -> bytes:
        if not self.recv_bufs:
            return b""
        value = self.recv_bufs.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    def settimeout(
        self,
        timeout: float | int,
    ) -> None:
        pass

    def connect(
        self,
        server: tuple[str, int],
    ) -> None:
        self.connections.append(server)

    def close(self) -> None:
        self.closed = True


class FakeSocketModule:
    AF_UNSPEC = socket.AF_UNSPEC
    AF_INET = socket.AF_INET
    AF_INET6 = socket.AF_INET6
    SOCK_STREAM = socket.SOCK_STREAM
    IPPROTO_TCP = socket.IPPROTO_TCP

    def __init__(
        self,
        responses: list[bytes],
    ) -> None:
        self._responses = responses
        self.sockets: list[FakeSocket] = []

    def socket(
        self,
        family: int,
        type: int,
        proto: int = 0,
        fileno: int | None = None,
    ) -> FakeSocket:
        s = FakeSocket(list(self._responses))
        self.sockets.append(s)
        return s

    def getaddrinfo(
        self,
        host: str,
        port: int,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        family = family or socket.AF_INET
        type = type or socket.SOCK_STREAM
        proto = proto or socket.IPPROTO_TCP
        sockaddr = ("127.0.0.1", port)
        return [(family, type, proto, "", sockaddr)]
