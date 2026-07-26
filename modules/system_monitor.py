"""
modules/system_monitor.py
=========================
System telemetry for Jarvis: CPU, RAM, disk, GPU, network and processes.

Uses psutil for cross-platform stats. GPU stats are best-effort: it tries
GPUtil first, then nvidia-smi, and finally reports "unavailable" gracefully.
All methods return plain dicts so the HUD can render them, plus a human-readable
`report()` for speech / text output.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from typing import Any, Dict, List

try:
    import psutil  # type: ignore
    _PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    _PSUTIL = False


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


class SystemMonitor:
    def __init__(self) -> None:
        self._last_net = None
        self._last_net_time = None
        if _PSUTIL:
            # Prime cpu_percent so the first real call is meaningful.
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        return _PSUTIL

    # ------------------------------------------------------------------ #
    # Individual metrics
    # ------------------------------------------------------------------ #
    def cpu(self) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        freq = None
        try:
            f = psutil.cpu_freq()
            freq = round(f.current, 0) if f else None
        except Exception:
            freq = None
        return {
            "percent": psutil.cpu_percent(interval=0.3),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "freq_mhz": freq,
            "per_core": psutil.cpu_percent(interval=0.0, percpu=True),
        }

    def ram(self) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "percent": vm.percent,
            "total_h": _fmt_bytes(vm.total),
            "used_h": _fmt_bytes(vm.used),
            "available_h": _fmt_bytes(vm.available),
            "swap_percent": sw.percent,
        }

    def disk(self) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        partitions: List[Dict[str, Any]] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            partitions.append({
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
                "total_h": _fmt_bytes(usage.total),
                "used_h": _fmt_bytes(usage.used),
                "free_h": _fmt_bytes(usage.free),
            })
        return {"partitions": partitions}

    def gpu(self) -> Dict[str, Any]:
        # Try GPUtil first.
        try:
            import GPUtil  # type: ignore
            gpus = GPUtil.getGPUs()
            if gpus:
                return {
                    "gpus": [
                        {
                            "name": g.name,
                            "load_percent": round(g.load * 100, 1),
                            "mem_used": g.memoryUsed,
                            "mem_total": g.memoryTotal,
                            "mem_percent": round((g.memoryUsed / g.memoryTotal) * 100, 1) if g.memoryTotal else 0,
                            "temp": g.temperature,
                        }
                        for g in gpus
                    ]
                }
        except Exception:
            pass

        # Fall back to nvidia-smi.
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                ).decode("utf-8", "ignore")
                gpus = []
                for line in out.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        name, load, mu, mt, temp = parts[:5]
                        mem_total = float(mt) if mt.replace(".", "").isdigit() else 0
                        mem_used = float(mu) if mu.replace(".", "").isdigit() else 0
                        gpus.append({
                            "name": name,
                            "load_percent": float(load) if load.replace(".", "").isdigit() else 0,
                            "mem_used": mem_used,
                            "mem_total": mem_total,
                            "mem_percent": round((mem_used / mem_total) * 100, 1) if mem_total else 0,
                            "temp": float(temp) if temp.replace(".", "").isdigit() else None,
                        })
                if gpus:
                    return {"gpus": gpus}
            except Exception:
                pass

        return {"gpus": [], "note": "No dedicated GPU telemetry available."}

    def network(self) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        io = psutil.net_io_counters()
        now = time.time()
        up_rate = down_rate = 0.0
        if self._last_net is not None and self._last_net_time is not None:
            dt = max(now - self._last_net_time, 1e-6)
            up_rate = (io.bytes_sent - self._last_net.bytes_sent) / dt
            down_rate = (io.bytes_recv - self._last_net.bytes_recv) / dt
        self._last_net = io
        self._last_net_time = now

        return {
            "bytes_sent": io.bytes_sent,
            "bytes_recv": io.bytes_recv,
            "sent_h": _fmt_bytes(io.bytes_sent),
            "recv_h": _fmt_bytes(io.bytes_recv),
            "up_rate_h": _fmt_bytes(up_rate) + "/s",
            "down_rate_h": _fmt_bytes(down_rate) + "/s",
        }

    def processes(self, limit: int = 8) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        procs: List[Dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                procs.append({
                    "pid": info["pid"],
                    "name": info.get("name") or "?",
                    "cpu": round(info.get("cpu_percent") or 0.0, 1),
                    "mem": round(info.get("memory_percent") or 0.0, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        return {"top": procs[:limit], "count": len(procs)}

    def uptime(self) -> Dict[str, Any]:
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        boot = psutil.boot_time()
        secs = int(time.time() - boot)
        days, rem = divmod(secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        return {"seconds": secs, "human": f"{days}d {hours}h {mins}m"}

    # ------------------------------------------------------------------ #
    # Aggregate snapshot for the HUD
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        """Compact snapshot used to drive HUD panels each refresh tick."""
        if not _PSUTIL:
            return {"error": "psutil not installed"}
        cpu = self.cpu()
        ram = self.ram()
        net = self.network()
        gpu = self.gpu()
        gpu_load = gpu["gpus"][0]["load_percent"] if gpu.get("gpus") else None
        return {
            "cpu_percent": cpu.get("percent"),
            "ram_percent": ram.get("percent"),
            "ram_used_h": ram.get("used_h"),
            "ram_total_h": ram.get("total_h"),
            "gpu_percent": gpu_load,
            "net_down": net.get("down_rate_h"),
            "net_up": net.get("up_rate_h"),
            "uptime": self.uptime().get("human"),
        }

    # ------------------------------------------------------------------ #
    # Human-readable report (speech / text)
    # ------------------------------------------------------------------ #
    def report(self, metric: str = "all") -> str:
        metric = (metric or "all").lower()
        if not _PSUTIL:
            return "System monitoring is unavailable (psutil not installed)."

        if metric == "cpu":
            c = self.cpu()
            return (f"CPU load is {c['percent']}% across {c['cores_logical']} logical cores"
                    + (f" at {c['freq_mhz']} MHz." if c.get("freq_mhz") else "."))
        if metric == "ram":
            r = self.ram()
            return f"Memory: {r['used_h']} of {r['total_h']} used ({r['percent']}%)."
        if metric == "disk":
            d = self.disk()
            lines = ["Disk usage:"]
            for p in d["partitions"]:
                lines.append(f"  {p['device']} — {p['used_h']}/{p['total_h']} ({p['percent']}%)")
            return "\n".join(lines)
        if metric == "gpu":
            g = self.gpu()
            if not g.get("gpus"):
                return g.get("note", "No GPU telemetry available.")
            lines = ["GPU:"]
            for gg in g["gpus"]:
                temp = f", {gg['temp']}°C" if gg.get("temp") is not None else ""
                lines.append(f"  {gg['name']} — {gg['load_percent']}% load, "
                             f"{gg['mem_percent']}% VRAM{temp}")
            return "\n".join(lines)
        if metric == "network":
            n = self.network()
            return (f"Network: down {n['down_rate_h']}, up {n['up_rate_h']}. "
                    f"Session total {n['recv_h']} received, {n['sent_h']} sent.")
        if metric == "processes":
            p = self.processes()
            lines = [f"Top processes (of {p['count']}):"]
            for pr in p["top"]:
                lines.append(f"  {pr['name']} (pid {pr['pid']}) — {pr['cpu']}% CPU, {pr['mem']}% RAM")
            return "\n".join(lines)

        # all
        c = self.cpu()
        r = self.ram()
        u = self.uptime()
        g = self.gpu()
        gpu_line = ""
        if g.get("gpus"):
            gg = g["gpus"][0]
            gpu_line = f" GPU {gg['name']} at {gg['load_percent']}%."
        return (f"System status: CPU {c['percent']}%, RAM {r['used_h']}/{r['total_h']} "
                f"({r['percent']}%), uptime {u['human']}.{gpu_line}")
