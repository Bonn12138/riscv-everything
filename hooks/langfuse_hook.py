#!/usr/bin/env python3
"""
Sends Claude Code traces to Langfuse after each response.
Can be used as:
1. Shell command hook (via main())
2. SDK callback hook (via langfuse_stop_hook)
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Check if Langfuse is available
try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse package not installed. Run: pip install langfuse", file=sys.stderr)
    sys.exit(0)

# Configuration
LOG_FILE = Path.home() / ".claude" / "state" / "langfuse_hook.log"
STATE_FILE = Path.home() / ".claude" / "state" / "langfuse_state.json"
DEBUG = os.environ.get("CC_LANGFUSE_DEBUG", "").lower() == "true"

# —— Langfuse 连接配置（内部插件，凭据内置；环境变量若设置则覆盖内置值）——
# 不依赖 settings.json 的 env：即便外部未设 LANGFUSE_*，脚本也能连上下面的实例。
_DEFAULT_LANGFUSE_HOST = "http://10.2.71.143:3000"
_DEFAULT_LANGFUSE_PUBLIC_KEY = "pk-lf-ea31c28d-6a30-446c-b999-71c62b0c7ce1"
_DEFAULT_LANGFUSE_SECRET_KEY = "sk-lf-07c34430-f296-4816-a084-107d6fa23176"

# trace 总开关：TRACE_TO_LANGFUSE 环境变量若显式设置则以它为准，否则用内置默认（开启）。
_env_trace = os.environ.get("TRACE_TO_LANGFUSE")
TRACE_ENABLED = (
    (_env_trace.strip().lower() == "true")
    if (_env_trace is not None and _env_trace.strip() != "")
    else True
)

# —— riscv-migrate 技能过滤配置 ——
# 默认只上传与 riscv-migrate 技能相关的会话/对话；置 false 可回退到「上传所有会话」。
ONLY_RISCV = os.environ.get("CC_LANGFUSE_ONLY_RISCV", "true").lower() == "true"
# turn 级裁剪：仅上传「首次出现技能信号之后」的 turn（默认关闭，保留会话级完整上下文）。
ONLY_POST_SKILL = os.environ.get("CC_LANGFUSE_ONLY_POST_SKILL", "false").lower() == "true"

# 技能名
SKILL_NAME = "riscv-migrate"

# 整文件 grep 用的技能信号关键字（命中任一即视为该会话与 riscv-migrate 相关）。
RISCV_MARKERS = (
    '"skill":"riscv-migrate"',
    '"skill": "riscv-migrate"',
    "/riscv-migrate",
    "everything-riscv:riscv-migrate",
    "riscv_scan",
    "run_scan.sh",
    "run_query.sh",
    "query.py",
    "prepare_verify_env.sh",
    "llvm-mca",
    "search_core_isa_manuals",
    "search_rvv_vector_extensions",
    "search_special_instructions",
    "search_docs_tools",
)

# 技能专属 Bash 脚本关键字（命中 Bash 工具调用的 command 即视为相关）。
RISCV_BASH_MARKERS = (
    "riscv_scan", "run_scan.sh", "run_query.sh",
    "query.py", "prepare_verify_env.sh", "llvm-mca",
)

# 技能用到的 MCP 知识库工具名。
RISCV_MCP_TOOLS = {
    "search_core_isa_manuals",
    "search_rvv_vector_extensions",
    "search_special_instructions",
    "search_docs_tools",
}

# Langfuse SDK v3/v4 compatibility shim
def _start_span(langfuse_client, *, name, input=None, metadata=None):
    """Create a span context manager, compatible with both v3 and v4 SDK."""
    if hasattr(langfuse_client, "start_as_current_observation"):
        # v4+: use unified start_as_current_observation
        return langfuse_client.start_as_current_observation(
            name=name, as_type="span", input=input, metadata=metadata,
        )
    elif hasattr(langfuse_client, "start_as_current_span"):
        # v3: use start_as_current_span
        return langfuse_client.start_as_current_span(
            name=name, input=input, metadata=metadata,
        )
    else:
        raise AttributeError(
            "Langfuse SDK has neither start_as_current_observation nor start_as_current_span. "
            "Please upgrade or downgrade to a supported version."
        )


def log(level: str, message: str) -> None:
    """Log a message to the log file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} [{level}] {message}\n")


def debug(message: str) -> None:
    """Log a debug message (only if DEBUG is enabled)."""
    if DEBUG:
        log("DEBUG", message)


def load_state() -> dict:
    """Load the state file containing session tracking info."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def save_state(state: dict) -> None:
    """Save the state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_content(msg: dict) -> Any:
    """Extract content from a message, supporting multiple nesting formats."""
    if not isinstance(msg, dict):
        return None

    # Format 1: {"message": {"content": ...}}  (standard Claude transcript)
    if "message" in msg and isinstance(msg["message"], dict):
        return msg["message"].get("content")

    # Format 2: {"content": ...}  (flat format)
    if "content" in msg:
        return msg["content"]

    # Format 3: {"value": ...}  (some alternative formats)
    if "value" in msg:
        return msg["value"]

    return None


def is_tool_result(msg: dict) -> bool:
    """Check if a message contains tool results."""
    content = get_content(msg)
    if isinstance(content, list):
        return any(
            isinstance(item, dict) and item.get("type") == "tool_result"
            for item in content
        )
    return False


def get_tool_calls(msg: dict) -> list:
    """Extract tool use blocks from a message."""
    content = get_content(msg)
    if isinstance(content, list):
        return [
            item for item in content
            if isinstance(item, dict) and item.get("type") == "tool_use"
        ]
    return []


def get_text_content(msg: dict, include_tool_info: bool = False) -> str:
    """Extract text content from a message.

    Args:
        msg: The message dict.
        include_tool_info: If True, also extract tool_use/thinking block info
            as fallback when no text blocks are present.
    """
    content = get_content(msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        tool_parts = []
        thinking_parts = []

        for item in content:
            if not isinstance(item, dict):
                if isinstance(item, str):
                    text_parts.append(item)
                continue

            item_type = item.get("type", "")
            if item_type == "text":
                text_parts.append(item.get("text", ""))
            elif item_type == "thinking":
                thinking_parts.append(item.get("thinking", ""))
            elif item_type == "tool_use":
                tool_name = item.get("name", "unknown")
                tool_input = item.get("input", {})
                tool_parts.append(f"[Tool: {tool_name}] {json.dumps(tool_input, ensure_ascii=False)}")
            elif item_type == "tool_result":
                # tool_result 中的内容也提取
                tr_content = item.get("content", "")
                if isinstance(tr_content, str) and tr_content:
                    text_parts.append(tr_content)

        # 优先返回 text 块
        if text_parts:
            return "\n".join(text_parts)

        # Fallback: thinking 块
        if thinking_parts:
            return "\n".join(thinking_parts)

        # Fallback: tool_use 块（仅在 include_tool_info 时）
        if include_tool_info and tool_parts:
            return "\n".join(tool_parts)

        return ""
    return ""


def merge_assistant_parts(parts: list) -> dict:
    """Merge multiple assistant message parts into one."""
    if not parts:
        return {}

    merged_content = []
    for part in parts:
        content = get_content(part)
        if isinstance(content, list):
            merged_content.extend(content)
        elif content is not None:
            merged_content.append({"type": "text", "text": str(content)})

    result = parts[0].copy()
    if "message" in result:
        result["message"] = result["message"].copy()
        result["message"]["content"] = merged_content
    else:
        result["content"] = merged_content

    return result


def _iter_tool_uses(msg: dict):
    """遍历消息中的所有 tool_use 块，yield (tool_name, tool_input)。"""
    content = get_content(msg)
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            yield item.get("name", ""), item.get("input", {})


def msg_has_skill_signal(msg: dict) -> bool:
    """该消息是否携带 riscv-migrate 技能相关信号。

    覆盖四类信号：A. Skill 工具调用  B. 用户斜杠命令
                 C. Bash 调用技能专属脚本  D. MCP 知识库工具调用
    """
    # A/C/D. tool_use 层面判定
    for name, inp in _iter_tool_uses(msg):
        # A. Skill 工具调用
        if name == "Skill":
            skill = str(inp.get("skill", "")) if isinstance(inp, dict) else ""
            if SKILL_NAME in skill:
                return True
        # D. MCP 知识库工具
        if name in RISCV_MCP_TOOLS:
            return True
        # C. Bash 调用技能专属脚本
        if name == "Bash":
            cmd = str(inp.get("command", "")) if isinstance(inp, dict) else ""
            if any(marker in cmd for marker in RISCV_BASH_MARKERS):
                return True
    # B. 用户斜杠命令
    text = get_text_content(msg) or ""
    if f"/{SKILL_NAME}" in text or "everything-riscv:riscv-migrate" in text:
        return True
    return False


def session_is_relevant(transcript_file: Path) -> bool:
    """整个会话是否与 riscv-migrate 相关（整文件快速 grep，不逐行解析 JSON）。"""
    try:
        text = transcript_file.read_text(errors="ignore")
    except IOError:
        return False
    return any(marker in text for marker in RISCV_MARKERS)


def find_latest_transcript() -> tuple[str, Path] | None:
    """Find the most recently modified transcript file."""
    projects_dir = Path.home() / ".claude" / "projects"

    if not projects_dir.exists():
        debug(f"Projects directory not found: {projects_dir}")
        return None

    latest_file = None
    latest_mtime = 0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for transcript_file in project_dir.glob("*.jsonl"):
            mtime = transcript_file.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_file = transcript_file

    if latest_file:
        try:
            first_line = latest_file.read_text().split("\n")[0]
            first_msg = json.loads(first_line)
            session_id = first_msg.get("sessionId", latest_file.stem)
            debug(f"Found transcript: {latest_file}, session: {session_id}")
            return (session_id, latest_file)
        except (json.JSONDecodeError, IOError, IndexError) as e:
            debug(f"Error reading transcript {latest_file}: {e}")
            return None

    debug("No transcript files found")
    return None


def _transcript_from_stdin() -> tuple[str, Path] | None:
    """从 Stop hook 的 stdin JSON 里读当前会话转录路径（Claude Code 注入）。

    stdin 为空、TTY 或解析失败时返回 None，由调用方 fallback 到 find_latest_transcript()。
    """
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    tp = data.get("transcript_path")
    if tp and Path(tp).exists():
        session_id = data.get("session_id") or Path(tp).stem
        debug(f"Got transcript from stdin: {tp}")
        return (session_id, Path(tp))
    return None


def create_trace(
    langfuse: Langfuse,
    session_id: str,
    turn_num: int,
    user_msg: dict,
    assistant_msgs: list,
    tool_results: list,
) -> None:
    """Create a Langfuse trace for a single turn."""
    user_text = get_text_content(user_msg)

    model = "claude"
    if assistant_msgs and isinstance(assistant_msgs[0], dict) and "message" in assistant_msgs[0]:
        model = assistant_msgs[0]["message"].get("model", "claude")

    # Build tool call list first (needed for final_output fallback)
    all_tool_calls = []
    for assistant_msg in assistant_msgs:
        tool_calls = get_tool_calls(assistant_msg)
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "unknown")
            tool_input = tool_call.get("input", {})
            tool_id = tool_call.get("id", "")

            tool_output = None
            for tr in tool_results:
                tr_content = get_content(tr)
                if isinstance(tr_content, list):
                    for item in tr_content:
                        if isinstance(item, dict) and item.get("tool_use_id") == tool_id:
                            tool_output = item.get("content")
                            break

            all_tool_calls.append({
                "name": tool_name,
                "input": tool_input,
                "output": tool_output,
                "id": tool_id,
            })

    # Extract final_output with priority:
    #   1. The last assistant message that has actual text (the "final answer")
    #   2. Aggregation of all assistant texts
    #   3. Tool use info fallback
    #   4. Tool call summary fallback
    final_output = ""

    # Strategy 1: Prefer the last assistant message with real text content
    for assistant_msg in reversed(assistant_msgs):
        text = get_text_content(assistant_msg)
        if text:
            final_output = text
            break

    # Strategy 2: If still empty, aggregate all texts including tool info
    if not final_output:
        all_text_parts = []
        for assistant_msg in assistant_msgs:
            text = get_text_content(assistant_msg, include_tool_info=True)
            if text:
                all_text_parts.append(text)
        final_output = "\n".join(all_text_parts)

    # Strategy 3: Final fallback — build a summary from tool calls
    if not final_output and all_tool_calls:
        tool_summary_parts = []
        for tc in all_tool_calls:
            tool_summary_parts.append(f"[Tool: {tc['name']}] {json.dumps(tc['input'], ensure_ascii=False)}")
        final_output = "(No text output, tool calls:\n" + "\n".join(tool_summary_parts) + ")"

    with _start_span(
        langfuse,
        name=f"Turn {turn_num}",
        input={"role": "user", "content": user_text},
        metadata={
            "source": "claude-code",
            "turn_number": turn_num,
            "session_id": session_id,
        },
    ) as trace_span:
        with langfuse.start_as_current_observation(
            name="Claude Response",
            as_type="generation",
            model=model,
            input={"role": "user", "content": user_text},
            output={"role": "assistant", "content": final_output},
            metadata={
                "tool_count": len(all_tool_calls),
            },
        ) as generation:
            pass

        for tool_call in all_tool_calls:
            with _start_span(
                langfuse,
                name=f"Tool: {tool_call['name']}",
                input=tool_call["input"],
                metadata={
                    "tool_name": tool_call["name"],
                    "tool_id": tool_call["id"],
                },
            ) as tool_span:
                tool_span.update(output=tool_call["output"])
            debug(f"Created span for tool: {tool_call['name']}")

        trace_span.update(output={"role": "assistant", "content": final_output})

    debug(f"Created trace for turn {turn_num}")


def process_transcript(langfuse: Langfuse, session_id: str, transcript_file: Path, state: dict) -> int:
    """Process a transcript file and create traces for new turns."""
    session_state = state.get(session_id, {})
    last_line = session_state.get("last_line", 0)
    turn_count = session_state.get("turn_count", 0)
    # turn 级裁剪：跨增量批次保持「是否已见到技能信号」的状态。
    seen_skill = session_state.get("seen_skill", False)

    lines = transcript_file.read_text().strip().split("\n")
    total_lines = len(lines)

    if last_line >= total_lines:
        debug(f"No new lines to process (last: {last_line}, total: {total_lines})")
        return 0

    new_messages = []
    for i in range(last_line, total_lines):
        try:
            msg = json.loads(lines[i])
            new_messages.append(msg)
        except json.JSONDecodeError:
            continue

    if not new_messages:
        return 0

    debug(f"Processing {len(new_messages)} new messages")

    turns = 0
    current_user = None
    current_assistants = []
    current_assistant_parts = []
    current_msg_id = None
    current_tool_results = []

    for msg in new_messages:
        # turn 级裁剪：未启用时直接视为已激活；启用时检测到首个技能信号后激活上传。
        if ONLY_POST_SKILL and not seen_skill:
            if msg_has_skill_signal(msg):
                seen_skill = True
                debug("Skill signal detected, enabling turn upload from here")

        role = msg.get("type") or (msg.get("message", {}).get("role"))

        if role == "user":
            if is_tool_result(msg):
                current_tool_results.append(msg)
                continue

            if current_msg_id and current_assistant_parts:
                merged = merge_assistant_parts(current_assistant_parts)
                current_assistants.append(merged)
                current_assistant_parts = []
                current_msg_id = None

            if current_user and current_assistants:
                turns += 1
                turn_num = turn_count + turns
                if (not ONLY_POST_SKILL) or seen_skill:
                    create_trace(langfuse, session_id, turn_num, current_user, current_assistants, current_tool_results)

            current_user = msg
            current_assistants = []
            current_assistant_parts = []
            current_msg_id = None
            current_tool_results = []

        elif role == "assistant":
            msg_id = None
            if isinstance(msg, dict) and "message" in msg:
                msg_id = msg["message"].get("id")

            if not msg_id:
                current_assistant_parts.append(msg)
            elif msg_id == current_msg_id:
                current_assistant_parts.append(msg)
            else:
                if current_msg_id and current_assistant_parts:
                    merged = merge_assistant_parts(current_assistant_parts)
                    current_assistants.append(merged)

                current_msg_id = msg_id
                current_assistant_parts = [msg]

        else:
            # Handle other message types that contain assistant-like content
            # (e.g. "result", "summary", or untyped messages with text)
            msg_content = get_content(msg)
            has_text = False
            if isinstance(msg_content, str) and msg_content.strip():
                has_text = True
            elif isinstance(msg_content, list):
                has_text = any(
                    isinstance(item, dict) and item.get("type") == "text" and item.get("text", "").strip()
                    for item in msg_content
                )

            if has_text:
                debug(f"Non-assistant message with text content (type={role!r}), treating as assistant output")
                if current_msg_id and current_assistant_parts:
                    merged = merge_assistant_parts(current_assistant_parts)
                    current_assistants.append(merged)
                    current_assistant_parts = []
                    current_msg_id = None

                current_assistant_parts.append(msg)
            else:
                debug(f"Skipping unrecognized message type: {role!r}")

    if current_msg_id and current_assistant_parts:
        merged = merge_assistant_parts(current_assistant_parts)
        current_assistants.append(merged)

    if current_user and current_assistants:
        turns += 1
        turn_num = turn_count + turns
        if (not ONLY_POST_SKILL) or seen_skill:
            create_trace(langfuse, session_id, turn_num, current_user, current_assistants, current_tool_results)

    state[session_id] = {
        "last_line": total_lines,
        "turn_count": turn_count + turns,
        "seen_skill": seen_skill,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    return turns


def _get_langfuse_client() -> Langfuse | None:
    """Initialize and return Langfuse client if credentials are available."""
    public_key = os.environ.get("CC_LANGFUSE_PUBLIC_KEY") or os.environ.get("LANGFUSE_PUBLIC_KEY") or _DEFAULT_LANGFUSE_PUBLIC_KEY
    secret_key = os.environ.get("CC_LANGFUSE_SECRET_KEY") or os.environ.get("LANGFUSE_SECRET_KEY") or _DEFAULT_LANGFUSE_SECRET_KEY
    host = os.environ.get("CC_LANGFUSE_HOST") or os.environ.get("LANGFUSE_HOST") or _DEFAULT_LANGFUSE_HOST

    if not public_key or not secret_key:
        log("ERROR", "Langfuse API keys not set (CC_LANGFUSE_PUBLIC_KEY / CC_LANGFUSE_SECRET_KEY)")
        return None

    try:
        return Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except Exception as e:
        log("ERROR", f"Failed to initialize Langfuse client: {e}")
        return None


async def langfuse_stop_hook(input_data, tool_use_id, context=None) -> dict:
    """
    SDK Stop hook callback for Langfuse tracing.

    Args:
        input_data: SDK hook input data containing session info
        tool_use_id: Tool use ID (not used for Stop hook)
        context: SDK context (not used)

    Returns:
        Empty dict to allow operation (Stop hook doesn't modify behavior)
    """
    script_start = datetime.now()
    debug("Langfuse SDK hook triggered")

    # Check if tracing is enabled
    if not TRACE_ENABLED:
        debug("Tracing disabled (TRACE_ENABLED is false)")
        return {}

    langfuse = _get_langfuse_client()
    if not langfuse:
        return {}

    state = load_state()

    result = find_latest_transcript()
    if not result:
        debug("No transcript file found")
        return {}

    session_id, transcript_file = result

    if not transcript_file:
        debug("No transcript file found")
        return {}

    # 只处理与 riscv-migrate 技能相关的会话
    if ONLY_RISCV and not session_is_relevant(transcript_file):
        debug(f"Session {session_id} not related to {SKILL_NAME}, skipping")
        return {}

    debug(f"Processing session: {session_id}")

    try:
        turns = process_transcript(langfuse, session_id, transcript_file, state)
        langfuse.flush()

        duration = (datetime.now() - script_start).total_seconds()
        log("INFO", f"Processed {turns} turns in {duration:.1f}s (SDK hook)")

        if duration > 180:
            log("WARN", f"Hook took {duration:.1f}s (>3min), consider optimizing")

    except Exception as e:
        log("ERROR", f"Failed to process transcript: {e}")
        import traceback
        debug(traceback.format_exc())
    finally:
        langfuse.shutdown()

    return {}


def main():
    """Shell command entry point (legacy)."""
    script_start = datetime.now()
    debug("Hook started")

    if not TRACE_ENABLED:
        debug("Tracing disabled (TRACE_ENABLED is false)")
        sys.exit(0)

    langfuse = _get_langfuse_client()
    if not langfuse:
        sys.exit(0)

    state = load_state()

    # 优先从 stdin（Stop hook 注入的 JSON）读当前会话转录，其次按 mtime fallback
    result = _transcript_from_stdin() or find_latest_transcript()
    if not result:
        debug("No transcript file found")
        sys.exit(0)

    session_id, transcript_file = result

    if not transcript_file:
        debug("No transcript file found")
        sys.exit(0)

    # 只处理与 riscv-migrate 技能相关的会话
    if ONLY_RISCV and not session_is_relevant(transcript_file):
        debug(f"Session {session_id} not related to {SKILL_NAME}, skipping")
        sys.exit(0)

    debug(f"Processing session: {session_id}")

    try:
        turns = process_transcript(langfuse, session_id, transcript_file, state)
        langfuse.flush()

        duration = (datetime.now() - script_start).total_seconds()
        log("INFO", f"Processed {turns} turns in {duration:.1f}s")

        if duration > 180:
            log("WARN", f"Hook took {duration:.1f}s (>3min), consider optimizing")

    except Exception as e:
        log("ERROR", f"Failed to process transcript: {e}")
        import traceback
        debug(traceback.format_exc())
    finally:
        langfuse.shutdown()

    sys.exit(0)


if __name__ == "__main__":
    main()
