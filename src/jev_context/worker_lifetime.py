"""Tie model workers, including venv launcher descendants, to their owning engine."""

import os
import secrets
import threading
import time


def create_job():
    if os.name != "nt":
        return None, ""
    import win32job

    name = "Local\\jev-worker-" + secrets.token_hex(16)
    job = win32job.CreateJobObject(None, name)
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    return job, name


def bind_owner():
    """Run before importing model libraries: never leave orphaned weights after host death."""
    if os.name == "nt":
        name = os.environ.get("JEV_WORKER_JOB")
        if name:
            # The dedicated model venv need not install pywin32; use the stable Windows API.
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.OpenJobObjectW.restype = wintypes.HANDLE
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            job = kernel.OpenJobObjectW(0x0001, False, name)  # JOB_OBJECT_ASSIGN_PROCESS
            if not job:
                raise OSError(ctypes.get_last_error(), "Model owner Job is no longer available")
            try:
                if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
                    raise OSError(ctypes.get_last_error(), "Cannot bind model to its owner Job")
            finally:
                # Only the engine retains a handle, so its death kills this real interpreter.
                kernel.CloseHandle(job)
        return
    owner = int(os.environ.get("JEV_OWNER_PID", "0"))
    if not owner:
        return

    def watch():
        while True:
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                os._exit(0)
            time.sleep(0.2)

    threading.Thread(target=watch, daemon=True).start()
