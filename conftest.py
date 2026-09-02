"""
Root conftest: patches gltest's temp-file stdin injection for Windows.

On Windows, os.unlink() fails with WinError 32 when fd 0 (stdin) still
points to the temp file. The fix: skip the unlink on Windows. The OS
cleans up temp files on process exit.

NOTE: This patch is required for direct-mode tests to run on Windows.
It is NOT needed on Linux/macOS (CI runs on ubuntu-latest without this).
Do not remove this unless the upstream gltest package fixes the issue.
"""
import os
import sys
import tempfile

if sys.platform == "win32":
    import gltest.direct.loader as _loader

    _original_inject = _loader._inject_message_to_fd0

    def _patched_inject_message_to_fd0(vm):
        """Windows-safe version that skips os.unlink."""
        try:
            from genlayer.py import calldata
            from genlayer.py.types import Address
        except ImportError:
            return

        sender_addr = vm.sender
        if isinstance(sender_addr, bytes):
            sender_addr = Address(sender_addr)

        contract_addr = vm._contract_address
        if isinstance(contract_addr, bytes):
            contract_addr = Address(contract_addr)

        origin_addr = vm.origin
        if isinstance(origin_addr, bytes):
            origin_addr = Address(origin_addr)

        message_data = {
            "contract_address": contract_addr,
            "sender_address": sender_addr,
            "origin_address": origin_addr,
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }

        encoded = calldata.encode(message_data)

        fd, path = tempfile.mkstemp()
        try:
            os.write(fd, encoded)
            os.lseek(fd, 0, os.SEEK_SET)
            original_stdin = os.dup(0)
            vm._original_stdin_fd = original_stdin
            os.dup2(fd, 0)
        finally:
            os.close(fd)
            if sys.platform != "win32":
                os.unlink(path)

    _loader._inject_message_to_fd0 = _patched_inject_message_to_fd0
