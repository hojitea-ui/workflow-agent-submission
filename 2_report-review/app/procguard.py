"""서버 프로세스가 어떻게 끝나든(강제 종료 포함) 서버가 띄운 하위 프로세스가 함께 끝나게 한다.

Windows: 서버 프로세스를 'Job Object'에 넣고 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE를 건다. 이 프로세스가 만든 하위 프로세스(claude와
그 자식들)는 자동으로 같은 작업에 속하고, 서버 프로세스가 사라져 작업 핸들이 닫히면 운영체제가 작업 안의 프로세스를 모두 종료한다.
서버 자신을 넣기 때문에 하위 프로세스를 만든 직후에 따로 붙이는 방식과 달리 시간 차이로 빠져나가는 프로세스가 없다.

그 밖의 운영체제: 아직 지원하지 않는다(정상 종료·취소·시간 초과 때의 정리는 claude_engine이 한다).
"""
import ctypes
import os
from ctypes import wintypes

STATUS: str | None = None  # None=아직 안 함, "job-object"=보호 중, 그 밖에는 이유
_job_handle = None  # 핸들을 닫지 않고 프로세스 수명 동안 들고 있어야 한다

_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def protect_current_process() -> str:
    """이 프로세스(와 앞으로 만들 하위 프로세스)를 서버 수명에 묶는다. 여러 번 불러도 한 번만 한다. 결과를 STATUS로도 남긴다."""
    global STATUS, _job_handle
    if STATUS is not None:
        return STATUS
    if os.name != "nt":
        STATUS = "unsupported-os"
        return STATUS
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        _job_handle = job
        STATUS = "job-object"
    except OSError as e:
        STATUS = f"failed: win32 error {e.args[0]} ({e.args[1] if len(e.args) > 1 else ''})"
    return STATUS
