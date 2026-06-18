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
import re
import shlex
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

# —— riscv-migrate 技能过滤配置 ——
# turn 级判定：只上传「该 turn 真正触发 riscv-migrate 技能」的 turn（见 _turn_has_skill_signal）。
# 即便一个会话里有技能活动，其中夹杂的闲聊 turn（如“今天星期几”）也不上传。
# 置 false 可回退到「上传所有 turn」（不做任何技能过滤）。
ONLY_RISCV = os.environ.get("CC_LANGFUSE_ONLY_RISCV", "true").lower() == "true"

# 技能名
SKILL_NAME = "riscv-migrate"

# 技能专属脚本/可执行文件：当它们在 Bash 命令里被「真正调用」时（程序名命中），
# 视为该会话触发了 riscv-migrate 技能（见 _bash_invokes_skill_script）。
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


def _is_real_user_prompt(msg: dict) -> bool:
    """是否为用户真实输入的 prompt（排除 tool_result、注入元信息、attachment 等）。

    用于精准判定斜杠命令：只有真实用户输入里的 /riscv-migrate 才算技能信号；
    assistant 回复文本或 tool_result（如读取本脚本、grep 输出）里出现的技能名不算。
    """
    if not isinstance(msg, dict):
        return False
    role = msg.get("type") or (msg.get("message", {}).get("role"))
    if role != "user":
        return False
    if is_tool_result(msg):
        return False
    return True


# —— Bash 命令解析辅助（判定是否「真正调用」了技能脚本）——
_ENVASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_INTERPRETER_RE = re.compile(r"^(?:bash|sh|dash|zsh|python\d*(?:\.\d+)?)$")
_RUNNERS_WITH_ARG = {"timeout", "chrt", "taskset", "ionice"}  # 各吃 1 个参数再执行真命令
_RUNNERS_NO_ARG = {"sudo", "nice", "time", "stdbuf", "nohup", "command", "exec"}


def _program_of_simple_command(seg: str) -> str | None:
    """给定一段「简单命令」（已按 shell 操作符切开），返回它真正执行的程序名。

    逐个剥除前缀：环境变量赋值（NAME=val）、不带参 runner（sudo/nice/time/...）、
    带一个参数的 runner（timeout/chrt/taskset/ionice，其参数如时长/优先级会被跳过）、
    env（及其后若干 NAME=val）、解释器（bash/sh/python*）、以及路径前缀（取 basename）。
    """
    try:
        tokens = shlex.split(seg, posix=True)
    except ValueError:
        tokens = seg.split()  # 引号不匹配等异常时降级
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if _ENVASSIGN_RE.match(t):               # NAME=val 前缀
            i += 1
            continue
        tl = t.lower()
        if tl in _RUNNERS_WITH_ARG and i + 1 < n:  # timeout DURATION cmd
            i += 2
            continue
        if tl == "env":                           # env [NAME=val ...] cmd
            i += 1
            while i < n and _ENVASSIGN_RE.match(tokens[i]):
                i += 1
            continue
        if tl in _RUNNERS_NO_ARG:                 # sudo / nice / time / ...
            i += 1
            continue
        if _INTERPRETER_RE.match(tl):             # bash / sh / python3 ...
            i += 1
            continue
        return t.rsplit("/", 1)[-1]               # 剥路径前缀，取程序名
    return None


def _bash_invokes_skill_script(cmd: str) -> bool:
    """Bash 命令是否『真正调用』了 riscv-migrate 技能的专属脚本。

    按 shell 操作符（; & | 换行）切成若干简单命令，逐段用
    _program_of_simple_command 取「真正执行的程序名」，命中 RISCV_BASH_MARKERS 即算。

    这样只认"被当作程序执行"的脚本名：引号内的同名串、grep/echo/find/cat 的参数、
    for 列表里的字符串、heredoc/Python 字符串里的样例都不会被当成程序，从而排查、grep、
    文档里"提到"这些名字不会误判为相关。相比裸子串或纯正则，能正确处理
    timeout/env/sudo/解释器 等前缀（如 ``timeout 60 python3 query.py ...``）。
    """
    for seg in re.split(r"[;\|&\n]+", cmd):
        prog = _program_of_simple_command(seg)
        if prog and prog in RISCV_BASH_MARKERS:
            return True
    return False


def msg_has_skill_signal(msg: dict) -> bool:
    """该消息是否『真正触发了』riscv-migrate 技能。

    认四类信号：
      A. Skill 工具调用（skill 名含 riscv-migrate）—— 技能被显式调用的入口
      B. MCP 知识库工具调用（search_core_isa_manuals 等）—— 按 tool_use 的 name 判定
      C. 用户真实输入的斜杠命令 /riscv-migrate（仅真实 user prompt）
      D. Bash 真正调用了技能专属脚本（query.py / run_scan.sh / riscv_scan …）
         —— 按"程序名"判定（见 _bash_invokes_skill_script），只认被执行的脚本，
         不认被 grep/echo/文档"提到"的同名串。

    A/B/C 是结构化、无法被命令文本伪造的信号；D 用于覆盖"通过 CLI 脚本查询知识库"
    这类不经 Skill/MCP 入口的真实技能活动（KB 尚未接成 MCP server 时的主要路径）。
    """
    # A/B/D. tool_use 层面判定
    for name, inp in _iter_tool_uses(msg):
        # A. Skill 工具调用
        if name == "Skill":
            skill = str(inp.get("skill", "")) if isinstance(inp, dict) else ""
            if SKILL_NAME in skill:
                return True
        # B. MCP 知识库工具
        if name in RISCV_MCP_TOOLS:
            return True
        # D. Bash 真正调用技能专属脚本
        if name == "Bash":
            cmd = str(inp.get("command", "")) if isinstance(inp, dict) else ""
            if _bash_invokes_skill_script(cmd):
                return True
    # C. 用户真实输入的斜杠命令（仅在真实 user prompt 中判定，
    #    排除 assistant 文本、tool_result、注入的 skill/agent 列表等——
    #    否则只要会话里“提到”技能名就会被误判为相关）。
    if _is_real_user_prompt(msg):
        text = get_text_content(msg) or ""
        if f"/{SKILL_NAME}" in text or "everything-riscv:riscv-migrate" in text:
            return True
    return False


def session_is_relevant(transcript_file: Path) -> bool:
    """整个会话是否真的触发过 riscv-migrate 技能。

    逐条解析 JSONL，仅当出现「真实技能调用信号」时才视为相关：
    Skill 工具调用 / MCP 知识库工具 / Bash 真正调用技能脚本 / 用户真实输入的斜杠命令
    （见 msg_has_skill_signal）。

    注意：不再对整文件做裸字符串 grep。旧实现会把会话开头注入的 skill/agent 列表
    （其中必然含 "llvm-mca"、"everything-riscv:riscv-migrate" 等关键字）、以及读取
    脚本/grep 输出里出现的技能名也误判为相关——导致只要装了本插件，任意闲聊会话
    （如“今天星期几”）都会被上传。现在逐消息判定：注入的 attachment 无 message/
    content 字段会被自动排除，assistant 文本与 tool_result 里的技能名也不计入。
    """
    try:
        with open(transcript_file, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg_has_skill_signal(msg):
                    return True
    except IOError:
        return False
    return False


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


def _turn_has_skill_signal(user_msg, assistant_msgs, tool_results) -> bool:
    """该 turn 是否包含 riscv-migrate 技能信号（用户消息/助手消息/工具结果任一命中即算）。

    用于 turn 级上传过滤：只有真正干 RISC-V 技能活的 turn 才上传；夹杂的闲聊 turn
    （如“今天星期几”、调试本 hook 的 turn）即便处于一个含技能活动的会话中也不上传。
    """
    for m in (user_msg, *assistant_msgs, *tool_results):
        if m and msg_has_skill_signal(m):
            return True
    return False


def process_transcript(langfuse: Langfuse, session_id: str, transcript_file: Path, state: dict) -> int:
    """Process a transcript file and create traces for new turns."""
    session_state = state.get(session_id, {})
    last_line = session_state.get("last_line", 0)
    turn_count = session_state.get("turn_count", 0)

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
                if _turn_has_skill_signal(current_user, current_assistants, current_tool_results):
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
        if _turn_has_skill_signal(current_user, current_assistants, current_tool_results):
            create_trace(langfuse, session_id, turn_num, current_user, current_assistants, current_tool_results)

    state[session_id] = {
        "last_line": total_lines,
        "turn_count": turn_count + turns,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    return turns


def _get_langfuse_client() -> Langfuse | None:
    """Initialize and return Langfuse client if credentials are available."""
    public_key = os.environ.get("CC_LANGFUSE_PUBLIC_KEY") or os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("CC_LANGFUSE_SECRET_KEY") or os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("CC_LANGFUSE_HOST") or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

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
    if os.environ.get("TRACE_TO_LANGFUSE", "").lower() != "true":
        debug("Tracing disabled (TRACE_TO_LANGFUSE != true)")
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

    if os.environ.get("TRACE_TO_LANGFUSE", "").lower() != "true":
        debug("Tracing disabled (TRACE_TO_LANGFUSE != true)")
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
