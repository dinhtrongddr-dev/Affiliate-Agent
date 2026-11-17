"""Manage only this installation's Windows data-sync task; never start app workers."""
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

TASK_NAME = "Shopee Daily Data Sync"
TASK_PATH = "\\AffiliateAgent\\"


class ScheduleError(RuntimeError):
    pass


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _paths(settings):
    root = Path(settings.project_root).resolve()
    return root, root / ".venv" / "Scripts" / "pythonw.exe", root / "scripts" / "sync-shopee.py"


def _powershell(script):
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError("Không truy cập được lịch Windows; kiểm tra Task Scheduler.") from exc
    if result.returncode:
        # No raw command output: Windows errors may include local identity/config details.
        raise ScheduleError("Windows chưa lưu được lịch; tài khoản cần quyền tạo tác vụ của chính mình.")
    try:
        return json.loads(result.stdout.lstrip("\ufeff").strip() or "{}")
    except ValueError as exc:
        raise ScheduleError("Không đọc được trạng thái lịch Windows.") from exc


def _preamble(settings):
    root, python, runner = _paths(settings)
    return ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new();\n"
            f"$taskName={_literal(TASK_NAME)}; $taskPath={_literal(TASK_PATH)};\n"
            f"$python={_literal(python)}; $runner={_literal(runner)}; $root={_literal(root)};\n"
            "$task=Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue;\n"
            "$arguments='\"'+$runner+'\"';\n"
            "$managed=$false; if($task){$managed=($task.Actions.Count -eq 1 -and "
            "$task.Actions[0].Execute -eq $python -and $task.Actions[0].Arguments -eq $arguments "
            "-and $task.Actions[0].WorkingDirectory -eq $root)};\n")


_STATUS_CACHE: dict = {}
_REFRESHING: set[str] = set()
STATUS_TTL = 600.0


def status(settings, fresh: bool = False, wait: bool = True):
    """Task Scheduler is queried through PowerShell (3-5 s); pages reuse a snapshot.

    wait=False (pages): return the last snapshot at once, even an old one, and refresh it in the
    background; before the first snapshot exists the state is reported as still loading."""
    import threading
    import time
    key = str(getattr(settings, "project_root", ""))
    cached = _STATUS_CACHE.get(key)
    if not fresh and cached and time.monotonic() - cached[0] < STATUS_TTL:
        return dict(cached[1])
    if not wait and not fresh:
        if key not in _REFRESHING:
            _REFRESHING.add(key)

            def refresh():
                try:
                    _STATUS_CACHE[key] = (time.monotonic(), _status_uncached(settings))
                finally:
                    _REFRESHING.discard(key)
            threading.Thread(target=refresh, name="schedule-status", daemon=True).start()
        if cached:
            return dict(cached[1])
        return {"installed": None, "managed": None, "enabled": None, "loading": True, "task_name": TASK_NAME}
    result = _status_uncached(settings)
    _STATUS_CACHE[key] = (time.monotonic(), result)
    return dict(result)


def _status_uncached(settings):
    if sys.platform != "win32":
        return {"installed": False, "managed": False, "enabled": False,
                "task_name": TASK_NAME, "error": "Lịch ngoài app cần Windows Task Scheduler."}
    try:
        result = _powershell(_preamble(settings) + """
if(-not $task){ @{installed=$false;managed=$false;enabled=$false} | ConvertTo-Json -Compress; exit 0 }
$info=Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath;
@{installed=$true;managed=$managed;enabled=($task.State -ne 'Disabled');
  next_run_at=$info.NextRunTime.ToString('o');last_result=$info.LastTaskResult} | ConvertTo-Json -Compress
"""
        )
        return {**result, "task_name": TASK_NAME}
    except ScheduleError as exc:
        return {"installed": False, "managed": False, "enabled": False,
                "task_name": TASK_NAME, "error": str(exc)}


def install(settings, enabled=True, local_time="07:00"):
    _STATUS_CACHE.clear()
    if sys.platform != "win32":
        raise ScheduleError("Chạy khi app đóng cần Windows Task Scheduler.")
    if (not isinstance(enabled, bool) or not isinstance(local_time, str)
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time)):
        raise ScheduleError("Giờ đồng bộ cần có dạng HH:MM.")
    root, python, runner = _paths(settings)
    if not python.is_file() or not runner.is_file():
        raise ScheduleError("Thiếu Python hoặc chương trình đồng bộ của app.")
    script = _preamble(settings) + """
if($task -and -not $managed){ throw 'Existing task is owned by another installation' }
"""
    if not enabled:
        script += "if($task){Disable-ScheduledTask -TaskName $taskName -TaskPath $taskPath | Out-Null};\n"
        script += "@{installed=[bool]$task;managed=$managed;enabled=$false} | ConvertTo-Json -Compress;\n"
    else:
        script += f"$localTime={_literal(local_time)};\n" + """
$action=New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $root;
$trigger=New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($localTime,'HH:mm',$null));
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited;
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'AffiliateAgent: Shopee product data only; no AI, video or publishing.' -Force | Out-Null;
@{installed=$true;managed=$true;enabled=$true} | ConvertTo-Json -Compress;
"""
    return {**_powershell(script), "task_name": TASK_NAME, "local_time": local_time}
