import os
import stat
import tempfile
import time
import unittest

from framework.gatesocket import GateServer, socket_check

from test_gate import sample_request


class GateSocketModeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.path = self.folder + "/g.sock"
        self.check = lambda req: {"decision": "allow", "reason": "ok"}
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def _start(self, **kwargs):
        self.server = GateServer(self.path, self.check, **kwargs)
        self.server.start()
        time.sleep(0.05)

    def test_sm01_default_still_0600(self):
        self._start()
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_sm02_mode_0660_with_own_group(self):
        gid = os.getgid()
        self._start(mode=0o660, group=gid)
        st = os.stat(self.path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        self.assertEqual(st.st_gid, gid)
        out = socket_check(self.path)(sample_request())
        self.assertEqual(out["decision"], "allow")

    def test_sm03_invalid_mode(self):
        for mode in (0o666, 0o644, True, "0600"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError) as ctx:
                    GateServer(self.path, self.check, mode=mode).start()
                self.assertEqual(str(ctx.exception), "invalid_socket_mode")
                self.assertFalse(os.path.exists(self.path))

    def test_sm04_invalid_group(self):
        for group in (-1, True, "staff"):
            with self.subTest(group=group):
                with self.assertRaises(ValueError) as ctx:
                    GateServer(self.path, self.check, mode=0o660, group=group).start()
                self.assertEqual(str(ctx.exception), "invalid_socket_group")
                self.assertFalse(os.path.exists(self.path))
        with self.assertRaises(ValueError) as ctx:
            GateServer(self.path, self.check, mode=0o600, group=os.getgid()).start()
        self.assertEqual(str(ctx.exception), "group_requires_0660")
        self.assertFalse(os.path.exists(self.path))

    def test_sm05_insecure_dir(self):
        os.chmod(self.folder, 0o777)
        try:
            with self.assertRaises(ValueError) as ctx:
                GateServer(self.path, self.check).start()
            self.assertEqual(str(ctx.exception), "socket_dir_insecure")
            self.assertFalse(os.path.exists(self.path))
        finally:
            os.chmod(self.folder, 0o700)

    def test_sm06_existing_socket_untouched_on_error(self):
        running = GateServer(self.path, self.check)
        running.start()
        time.sleep(0.05)
        try:
            with self.assertRaises(ValueError) as ctx:
                GateServer(self.path, self.check, mode=0o644).start()
            self.assertEqual(str(ctx.exception), "invalid_socket_mode")
            self.assertTrue(os.path.exists(self.path))
            out = socket_check(self.path)(sample_request())
            self.assertEqual(out["decision"], "allow")
        finally:
            running.close()


if __name__ == "__main__":
    unittest.main()