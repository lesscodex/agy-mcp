"""FastMCP server implementation for Antigravity CLI."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("agy-mcp")


DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("AGY_MCP_TIMEOUT_SECONDS", "1200"))
AGY_BIN = os.environ.get("AGY_BIN", "agy")
DEFAULT_AGY_DATA_DIR = Path.home() / ".gemini" / "antigravity-cli"
CONVERSATION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def build_agy_command(
    *,
    prompt: str,
    cwd: str,
    SESSION_ID: str,
    timeout_seconds: int,
    sandbox: bool,
    model: str,
) -> list[str]:
    cmd = [
        shutil.which(AGY_BIN) or AGY_BIN,
        "--dangerously-skip-permissions",
        "--add-dir",
        cwd,
        "--print-timeout",
        f"{timeout_seconds}s",
    ]

    if model:
        cmd.extend(["--model", model])

    if SESSION_ID:
        cmd.append(f"--conversation={SESSION_ID}")

    if sandbox:
        cmd.append("--sandbox")

    cmd.extend(["--print", prompt])
    return cmd


def run_agy_command(cmd: list[str], cwd: str, timeout_seconds: int) -> dict[str, Any]:
    process = subprocess.Popen(
        cmd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        start_new_session=os.name != "nt",
    )

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process(process)
        stdout, stderr = process.communicate()

    return {
        "stdout": stdout or "",
        "stderr": stderr or "",
        "exit_code": process.returncode,
        "timed_out": timed_out,
    }


def kill_process(process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        process.kill()


def is_conversation_id(value: str) -> bool:
    return bool(CONVERSATION_ID_RE.fullmatch(value))


def snapshot_conversations(agy_data_dir: Path = DEFAULT_AGY_DATA_DIR) -> dict[str, float]:
    conversations: dict[str, float] = {}
    for path in conversation_state_paths(agy_data_dir):
        conversation_id = path.stem if path.suffix == ".db" else path.name
        if not is_conversation_id(conversation_id):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        conversations[conversation_id] = max(mtime, conversations.get(conversation_id, 0.0))
    return conversations


def conversation_state_paths(agy_data_dir: Path) -> list[Path]:
    paths: list[Path] = []
    brain_dir = agy_data_dir / "brain"
    conversations_dir = agy_data_dir / "conversations"

    try:
        paths.extend(path for path in brain_dir.iterdir() if path.is_dir())
    except OSError:
        pass

    try:
        paths.extend(path for path in conversations_dir.glob("*.db") if path.is_file())
    except OSError:
        pass

    return paths


def discover_conversation_id(
    before: dict[str, float],
    after: dict[str, float],
    started_at: float,
) -> Optional[str]:
    candidates = [
        (mtime, conversation_id)
        for conversation_id, mtime in after.items()
        if conversation_id not in before
    ]
    if not candidates:
        candidates = [
            (mtime, conversation_id)
            for conversation_id, mtime in after.items()
            if mtime >= started_at - 2 and mtime > before.get(conversation_id, 0.0)
        ]
    if not candidates:
        return None
    return max(candidates)[1]


def conversation_missing(run_result: dict[str, Any], SESSION_ID: str) -> bool:
    needle = f"Conversation {SESSION_ID} not found"
    return needle in run_result["stdout"] or needle in run_result["stderr"]


@mcp.tool(
    name="agy",
    description="""
    Invokes Antigravity CLI (`agy`) to execute AI-driven tasks, returning text output
    and a `SESSION_ID` for conversation continuity.

    **Return structure:**
        - `success`: boolean indicating process success
        - `SESSION_ID`: stable identifier for resuming this Antigravity session
        - `agent_messages`: stdout text from agy
        - `all_messages`: optional MCP-friendly event list when `return_all_messages=True`

    **Session model:**
        - New calls return the real Antigravity conversation UUID
        - Calls with `SESSION_ID` pass `--conversation=<SESSION_ID>`
        - `SESSION_ID` is the agy conversation id, not a local MCP id
    """,
    meta={"version": "0.1.0", "author": "local"},
)
async def agy(
    PROMPT: Annotated[str, "Instruction for the task to send to Antigravity CLI."],
    cd: Annotated[Path, "Set the workspace root for agy before executing the task."],
    sandbox: Annotated[
        bool,
        Field(description="Run in sandbox mode. Defaults to `False`."),
    ] = False,
    SESSION_ID: Annotated[
        str,
        "Resume the specified agy conversation id. Defaults to empty string, start a new session.",
    ] = "",
    return_all_messages: Annotated[
        bool,
        "Return MCP-friendly event records. Set to `False` by default.",
    ] = False,
    model: Annotated[
        str,
        "The model to use for the agy session. This parameter is strictly prohibited unless explicitly specified by the user.",
    ] = "",
    timeout_seconds: Annotated[
        int,
        "Hard timeout for the agy process. Defaults to AGY_MCP_TIMEOUT_SECONDS.",
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute an Antigravity CLI session and return the results."""

    if not cd.exists() or not cd.is_dir():
        return {
            "success": False,
            "error": f"The workspace root directory `{cd.absolute().as_posix()}` does not exist or is not a directory.",
        }

    requested_cwd = cd.absolute()
    conversation_id = SESSION_ID

    if SESSION_ID:
        if not is_conversation_id(SESSION_ID):
            return {"success": False, "error": f"Invalid SESSION_ID: {SESSION_ID}"}

    before = snapshot_conversations() if not SESSION_ID else {}
    started_at = time.time()
    cmd = build_agy_command(
        prompt=PROMPT,
        cwd=requested_cwd.as_posix(),
        SESSION_ID=conversation_id,
        timeout_seconds=timeout_seconds,
        sandbox=sandbox,
        model=model,
    )
    run_result = run_agy_command(cmd, cwd=requested_cwd.as_posix(), timeout_seconds=timeout_seconds + 30)

    if not SESSION_ID:
        after = snapshot_conversations()
        conversation_id = discover_conversation_id(before, after, started_at) or ""

    success = run_result["exit_code"] == 0 and not run_result["timed_out"]
    if SESSION_ID and conversation_missing(run_result, SESSION_ID):
        success = False

    if success and conversation_id:
        result: Dict[str, Any] = {
            "success": True,
            "SESSION_ID": conversation_id,
            "agent_messages": run_result["stdout"].strip(),
        }
    else:
        error = "agy failed or timed out."
        if not conversation_id:
            error = "Failed to get SESSION_ID from the agy conversation state."
        stderr = run_result["stderr"].strip()
        if stderr:
            error = f"{error}\n\n{stderr}"
        result = {"success": False, "error": error}
        if conversation_id:
            result["SESSION_ID"] = conversation_id

    if return_all_messages:
        result["all_messages"] = [
            {
                "type": "message",
                "role": "assistant",
                "content": run_result["stdout"],
            },
            {
                "type": "stderr",
                "content": run_result["stderr"],
            },
        ]

    return result


def run() -> None:
    """Start the MCP server over stdio transport."""
    mcp.run(transport="stdio")
