"""POC-02：同梱の Python で hashlib.scrypt が使えることと、所要時間・メモリの確認。"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass

from crm_poc import settings

if sys.platform != "win32":
    import resource


@dataclass(frozen=True)
class ScryptMeasurement:
    elapsed_seconds: float
    peak_memory_before_bytes: int
    peak_memory_after_bytes: int
    digest_length: int


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def hash_passcode(passcode: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=settings.SCRYPT_N,
        r=settings.SCRYPT_R,
        p=settings.SCRYPT_P,
        maxmem=settings.SCRYPT_MAXMEM_BYTES,
        dklen=settings.SCRYPT_DKLEN,
    )


def verify_passcode(passcode: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(hash_passcode(passcode, salt), expected)


def peak_memory_bytes() -> int:
    """プロセスの最大の使用メモリ（Windows は最大のワーキングセット、他は最大の RSS）。"""
    if sys.platform == "win32":
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(process), ctypes.byref(counters), counters.cb
        )
        return int(counters.PeakWorkingSetSize)
    else:
        max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            return max_rss
        return max_rss * 1024


def measure() -> ScryptMeasurement:
    before = peak_memory_bytes()
    started = time.perf_counter()
    digest = hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)
    elapsed = time.perf_counter() - started
    return ScryptMeasurement(
        elapsed_seconds=elapsed,
        peak_memory_before_bytes=before,
        peak_memory_after_bytes=peak_memory_bytes(),
        digest_length=len(digest),
    )


def main() -> int:
    print(json.dumps(asdict(measure())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
