import json
import os
import socket
import stat
import threading

MAX_FRAME = 65536
DEFAULT_TIMEOUT = 5.0
VALID_DECISIONS = frozenset({"allow", "deny", "needs_approval"})


def deny(reason):
    return {
        "decision": "deny",
        "reason": reason,
        "action_class": None,
        "effective_level": None,
        "request_hash": None,
        "policy_hash": None,
        "audit_sequence": None,
    }


def observe_fail(reason):
    return {"ok": False, "reason": reason, "audit_sequence": None}


def _observe_internal_error():
    return {"ok": False, "reason": "internal_error", "audit_sequence": None}


def _read_line(sock, max_bytes):
    buf = bytearray()
    while True:
        if len(buf) > max_bytes:
            return None
        if len(buf) == max_bytes and b"\n" not in buf:
            return None
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, TimeoutError):
            raise
        except OSError:
            return None
        if not chunk:
            if b"\n" in buf:
                idx = buf.index(b"\n")
                if idx > max_bytes:
                    return None
                return bytes(buf[:idx])
            return None
        buf.extend(chunk)
        if b"\n" in buf:
            idx = buf.index(b"\n")
            if idx > max_bytes:
                return None
            return bytes(buf[:idx])


def _send_json(sock, payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    sock.sendall(text.encode("utf-8"))


class GateServer:
    def __init__(self, path, check, observe=None, mode=0o600, group=None):
        self.path = path
        self.check = check
        self.observe = observe
        self.mode = mode
        self.group = group
        self._listen = None
        self._thread = None

    def _validate_start(self):
        if not isinstance(self.mode, int) or isinstance(self.mode, bool) or self.mode not in (0o600, 0o660):
            raise ValueError("invalid_socket_mode")
        if self.group is not None:
            if isinstance(self.group, bool) or not isinstance(self.group, int) or self.group < 0:
                raise ValueError("invalid_socket_group")
        if self.group is not None and self.mode == 0o600:
            raise ValueError("group_requires_0660")
        parent = os.path.dirname(os.path.abspath(self.path))
        parent_stat = os.stat(parent)
        if parent_stat.st_mode & 0o002:
            raise ValueError("socket_dir_insecure")

    def _cleanup_socket(self):
        listen = self._listen
        self._listen = None
        if listen is not None:
            try:
                listen.close()
            except OSError:
                pass
        if os.path.exists(self.path):
            try:
                if stat.S_ISSOCK(os.stat(self.path).st_mode):
                    os.unlink(self.path)
            except OSError:
                pass

    def start(self):
        self._validate_start()
        if os.path.exists(self.path):
            if stat.S_ISSOCK(os.stat(self.path).st_mode):
                os.unlink(self.path)
            else:
                raise FileExistsError(self.path)
        self._listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._listen.bind(self.path)
            try:
                if self.group is not None:
                    os.chown(self.path, -1, self.group)
                os.chmod(self.path, self.mode)
            except Exception:
                self._cleanup_socket()
                raise
        except Exception:
            if self._listen is not None and not os.path.exists(self.path):
                try:
                    self._listen.close()
                except OSError:
                    pass
                self._listen = None
            raise
        self._listen.listen()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._listen.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        conn.settimeout(10.0)
        try:
            try:
                line = _read_line(conn, MAX_FRAME)
            except (socket.timeout, TimeoutError):
                _send_json(conn, deny("invalid_frame"))
                return
            if line is None:
                _send_json(conn, deny("invalid_frame"))
                return
            try:
                frame = json.loads(line.decode("utf-8"))
            except Exception:
                _send_json(conn, deny("invalid_frame"))
                return
            if not isinstance(frame, dict):
                _send_json(conn, deny("invalid_frame"))
                return
            keys = set(frame.keys())
            if keys == {"request"}:
                try:
                    result = self.check(frame["request"])
                except Exception:
                    _send_json(conn, deny("internal_error"))
                    return
                if not isinstance(result, dict):
                    _send_json(conn, deny("internal_error"))
                    return
                _send_json(conn, result)
                return
            if keys == {"observation"}:
                if self.observe is None:
                    _send_json(conn, deny("invalid_frame"))
                    return
                try:
                    result = self.observe(frame["observation"])
                except Exception:
                    result = _observe_internal_error()
                if not isinstance(result, dict):
                    result = _observe_internal_error()
                _send_json(conn, result)
                return
            _send_json(conn, deny("invalid_frame"))
            return
        except Exception:
            try:
                _send_json(conn, deny("invalid_frame"))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        listen = self._listen
        self._listen = None
        if listen is not None:
            try:
                listen.close()
            except OSError:
                pass
        if os.path.exists(self.path):
            try:
                if stat.S_ISSOCK(os.stat(self.path).st_mode):
                    os.unlink(self.path)
            except OSError:
                pass


def socket_check(path, timeout=DEFAULT_TIMEOUT):
    def check(request):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                return deny("core_unreachable")
            frame = json.dumps({"request": request}, ensure_ascii=False, sort_keys=True) + "\n"
            sock.sendall(frame.encode("utf-8"))
            try:
                line = _read_line(sock, MAX_FRAME)
            except (socket.timeout, TimeoutError):
                return deny("core_timeout")
            if line is None:
                return deny("invalid_response")
            try:
                result = json.loads(line.decode("utf-8"))
            except Exception:
                return deny("invalid_response")
            if not isinstance(result, dict):
                return deny("invalid_response")
            decision = result.get("decision")
            if decision not in VALID_DECISIONS:
                return deny("invalid_response")
            return result
        except (socket.timeout, TimeoutError):
            return deny("core_timeout")
        except Exception:
            return deny("core_unreachable")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return check


def socket_observe(path, timeout=DEFAULT_TIMEOUT):
    def report(observation):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                return observe_fail("core_unreachable")
            frame = json.dumps({"observation": observation}, ensure_ascii=False, sort_keys=True) + "\n"
            sock.sendall(frame.encode("utf-8"))
            try:
                line = _read_line(sock, MAX_FRAME)
            except (socket.timeout, TimeoutError):
                return observe_fail("core_timeout")
            if line is None:
                return observe_fail("invalid_response")
            try:
                result = json.loads(line.decode("utf-8"))
            except Exception:
                return observe_fail("invalid_response")
            if not isinstance(result, dict):
                return observe_fail("invalid_response")
            if not isinstance(result.get("ok"), bool):
                return observe_fail("invalid_response")
            return result
        except (socket.timeout, TimeoutError):
            return observe_fail("core_timeout")
        except Exception:
            return observe_fail("core_unreachable")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    return report