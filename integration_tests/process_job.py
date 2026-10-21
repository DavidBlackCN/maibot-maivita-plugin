"""Windows process-tree containment for the disposable Host/Runner pair."""
import ctypes
from ctypes import wintypes
import os


class ProcessJob:
    """Fail closed if a kill-on-close job cannot be established."""
    def __init__(self):
        if os.name!='nt':
            raise RuntimeError('I0 process containment currently requires Windows')
        class BasicLimits(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                ('flags',wintypes.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),
                ('active_limit',wintypes.DWORD),('affinity',ctypes.c_size_t),
                ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('reads','writes','other','read_bytes','write_bytes','other_bytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_=[('basic',BasicLimits),('io',IO),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        limits=ExtendedLimits(); limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.get_last_error(); self.close(); raise ctypes.WinError(error)

    def assign(self,process):
        if not self.api.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle); self.handle=None

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
