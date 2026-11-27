"""Contain one operator-side Codex invocation and every process it launches."""

import os
import signal
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path


class CodexProcessError(ValueError):
    """Public, fixed setup messages for the operator before the task is claimed."""


class _PosixProcess:
    def __init__(self, command, cwd, stdin, env):
        self.command = command
        self.options = {"cwd": cwd, "stdin": stdin, "stdout": subprocess.DEVNULL,
                            "stderr": subprocess.DEVNULL, "env": env, "start_new_session": True}
        self.process = None
        self.stopped = False
        self.lock = threading.Lock()

    def start(self):
        self.process = subprocess.Popen(self.command, **self.options)

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout)

    def stop(self):
        with self.lock:
            if self.process is None or self.stopped:
                return
            # An npm launcher can already have exited with other group members alive.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.returncode = self.process.wait()
            self.stopped = True


class _WindowsJob:
    """The unnamed, non-inheritable last handle owns the lifetime of the tree."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle, dword = wintypes.HANDLE, wintypes.DWORD
        signatures = {
            "CreateJobObjectW": (handle, [wintypes.LPVOID, wintypes.LPCWSTR]),
            "SetInformationJobObject": (wintypes.BOOL, [handle, ctypes.c_int, wintypes.LPVOID, dword]),
            "AssignProcessToJobObject": (wintypes.BOOL, [handle, handle]),
            "TerminateJobObject": (wintypes.BOOL, [handle, wintypes.UINT]),
            "ResumeThread": (dword, [handle]),
            "GetSystemDirectoryW": (wintypes.UINT, [wintypes.LPWSTR, wintypes.UINT]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(kernel, name)
            function.restype, function.argtypes = result, arguments
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, no breakaway.
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def system_cmd(self):
        import ctypes

        path = ctypes.create_unicode_buffer(32768)
        length = self.kernel.GetSystemDirectoryW(path, len(path))
        if not 0 < length < len(path):
            raise ctypes.WinError(ctypes.get_last_error())
        return str(Path(path.value) / "cmd.exe")

    def assign(self, process):
        import ctypes

        if not self.kernel.AssignProcessToJobObject(self.handle, process):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, thread):
        import ctypes

        if self.kernel.ResumeThread(thread) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        import ctypes

        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        import _winapi

        if self.handle:
            _winapi.CloseHandle(self.handle)
            self.handle = None


def _windows_command(command, job):
    image = str(Path(command[0]).resolve(strict=True))
    arguments = [image, *command[1:]]
    if Path(image).suffix.lower() in {".cmd", ".bat"}:
        # cmd expands %NAME% even inside quotes. Reject shell syntax in local
        # paths before claiming, including characters npm could reparse via %*.
        if any(any(char in value for char in "%!^&|<>()\r\n") for value in arguments):
            raise CodexProcessError("Codex 或临时目录的路径含 cmd 特殊字符，请使用原生 Codex exe 或更换目录。")
        image = job.system_cmd()
        arguments = subprocess.list2cmdline(arguments)
        return image, f'{subprocess.list2cmdline([image])} /d /v:off /s /c "{arguments}"'
    return image, subprocess.list2cmdline(arguments)


class _WindowsProcess:
    def __init__(self, command, cwd, stdin, env):
        import _winapi
        import msvcrt

        self.command = command
        self.lock = threading.Lock()
        self.process = self.thread = None
        self.returncode = None
        self.assigned = False
        self.job = _WindowsJob()
        try:
            image, line = _windows_command(command, self.job)
            with ExitStack() as handles:
                nul = handles.enter_context(open(os.devnull, "wb"))
                current = _winapi.GetCurrentProcess()

                def duplicate(stream):
                    handle = _winapi.DuplicateHandle(current, msvcrt.get_osfhandle(stream.fileno()),
                                                     current, 0, True, _winapi.DUPLICATE_SAME_ACCESS)
                    handles.callback(_winapi.CloseHandle, handle)
                    return handle

                reader, output = duplicate(stdin), duplicate(nul)
                info = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES,
                    hStdInput=reader, hStdOutput=output, hStdError=output,
                    lpAttributeList={"handle_list": [reader, output]})
                # Keep the primary thread handle. Popen closes it and cannot safely
                # resume a CREATE_SUSPENDED process after the job assignment.
                self.process, self.thread, _, _ = _winapi.CreateProcess(
                    image, line, None, None, True, 0x4 | subprocess.CREATE_NO_WINDOW, env, str(cwd), info)
            self.job.assign(self.process)
            self.assigned = True
        except BaseException:
            self.close()
            raise

    def start(self):
        self.job.resume(self.thread)

    def poll(self):
        import _winapi

        if _winapi.WaitForSingleObject(self.process, 0) == _winapi.WAIT_OBJECT_0:
            self.returncode = _winapi.GetExitCodeProcess(self.process)
        return self.returncode

    def wait(self, timeout=None):
        import _winapi
        import math

        milliseconds = _winapi.INFINITE if timeout is None else max(0, math.ceil(timeout * 1000))
        if _winapi.WaitForSingleObject(self.process, milliseconds) == _winapi.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = _winapi.GetExitCodeProcess(self.process)
        return self.returncode

    def stop(self):
        import _winapi

        with self.lock:
            if not self.job.handle:
                return
            try:
                if self.assigned:
                    self.job.terminate()
                elif self.process:
                    # Assignment failed. The primary thread is still suspended.
                    _winapi.TerminateProcess(self.process, 1)
            finally:
                self.job.close()
                if self.process:
                    self.wait()

    def close(self):
        import _winapi

        try:
            self.stop()
        finally:
            for handle in (self.thread, self.process):
                if handle:
                    _winapi.CloseHandle(handle)
            self.process = self.thread = None


@contextmanager
def prepared_process(command, *, cwd, stdin, env):
    """Prepare containment before claiming. start() is the only execution point."""
    if os.name == "posix":
        process = _PosixProcess(command, cwd, stdin, env)
        try:
            yield process
        finally:
            process.stop()
    elif os.name == "nt":
        try:
            process = _WindowsProcess(command, cwd, stdin, env)
        except (OSError, ImportError, AttributeError) as error:
            raise CodexProcessError("无法为 Codex 建立受控 Windows 进程树，请在支持作业对象的终端中重试。") from error
        try:
            yield process
        finally:
            process.close()
    else:
        raise CodexProcessError("此操作系统无法建立受控的 Codex 进程树。")
