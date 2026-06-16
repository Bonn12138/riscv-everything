#!/usr/bin/env python3
"""
通过 Streamable HTTP 或 SSE 连接远端 MCP 服务并调用工具（如 riscv-doc-mcp）。

依赖：
  python3 -m pip install -r scripts/requirements-mcp.txt

默认使用 Streamable HTTP：`http://10.2.71.145:12306/mcp`（可用环境变量 RISCV_DOC_MCP_URL 覆盖）；
该路径下 httpx 不使用系统代理变量，便于直连内网 MCP。
若服务端仅提供 SSE，使用 `--transport sse` 且 URL 指向 SSE 入口（常见为 `http://<host>:<port>/sse`）；SSE 仍遵循系统代理，内网需配置 no_proxy。

示例：
  python3 scripts/query.py --list-tools
  python3 scripts/query.py -t search_core_isa_manuals -q "CSR mstatus"
  python3 scripts/query.py -t search_rvv_vector_extensions -q "vector crypto"
  python3 scripts/query.py -t search_special_instructions -q "Zba 有哪些指令"
  python3 scripts/query.py -t search_docs_tools -q "performance counter event"
  python3 scripts/query.py --transport sse --url http://10.2.71.145:12306/sse --list-tools
  RISCV_DOC_MCP_URL=http://其它主机:端口/mcp python3 scripts/query.py -t search_rvv_vector_extensions -q "__riscv_vsetvl"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

# 兼容旧环境变量名
_DEFAULT = "http://10.2.71.145:12306/mcp"
DEFAULT_MCP_URL = os.environ.get(
    "RISCV_DOC_MCP_URL",
    os.environ.get("RISCV_DOC_MCP_SSE_URL", _DEFAULT),
)


def _print_tool_result(result: CallToolResult) -> None:
    if result.isError:
        print("MCP tool error:", file=sys.stderr)
    if result.structuredContent is not None:
        print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))
        return
    if not result.content:
        print("(empty result)", file=sys.stderr)
        return
    for block in result.content:
        if isinstance(block, TextContent):
            print(block.text)
        else:
            print(block, file=sys.stderr)


async def _session_work(
    session: ClientSession,
    list_tools: bool,
    tool_name: str | None,
    tool_args: dict[str, Any],
) -> int:
    await session.initialize()
    if list_tools:
        listed = await session.list_tools()
        for t in listed.tools:
            print(t.name)
            if t.description:
                for line in t.description.strip().splitlines():
                    print(f"    {line}")
        return 0
    if not tool_name:
        print("请指定 --tool 或使用 --list-tools", file=sys.stderr)
        return 2
    out = await session.call_tool(tool_name, tool_args)
    _print_tool_result(out)
    return 1 if out.isError else 0


async def _run(
    transport: str,
    url: str,
    headers: dict[str, str] | None,
    sse_read_timeout: float,
    list_tools: bool,
    tool_name: str | None,
    tool_args: dict[str, Any],
) -> int:
    try:
        if transport == "sse":
            async with sse_client(
                url,
                headers=headers,
                sse_read_timeout=sse_read_timeout,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    return await _session_work(
                        session, list_tools, tool_name, tool_args
                    )
        if transport == "http":
            timeout = httpx.Timeout(30.0, read=sse_read_timeout)
            client_kw: dict[str, Any] = {
                "timeout": timeout,
                # 内网 MCP 直连；避免 http_proxy/all_proxy 在 no_proxy 通配不生效时把请求发到公司代理
                "trust_env": False,
            }
            if headers:
                client_kw["headers"] = headers
            async with httpx.AsyncClient(**client_kw) as http_client:
                async with streamable_http_client(
                    url, http_client=http_client
                ) as (read, write, _get_sid):
                    async with ClientSession(read, write) as session:
                        return await _session_work(
                            session, list_tools, tool_name, tool_args
                        )
        print(f"未知 --transport: {transport}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"连接失败 ({url}): {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        return 1


def main() -> None:
    p = argparse.ArgumentParser(
        description="查询远端 MCP 工具（默认 Streamable HTTP /mcp），riscv-doc 类服务"
    )
    p.add_argument(
        "--transport",
        choices=("sse", "http"),
        default=os.environ.get("RISCV_DOC_MCP_TRANSPORT", "http"),
        help="传输方式：http（Streamable HTTP，默认）或 sse（Server-Sent Events）",
    )
    p.add_argument(
        "--url",
        default=DEFAULT_MCP_URL,
        help=f"MCP 端点完整 URL（默认 env RISCV_DOC_MCP_URL 或 {_DEFAULT!r}）",
    )
    p.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY:VALUE",
        help="附加 HTTP 头，可重复（例：--header Authorization:Bearer\\ token）",
    )
    p.add_argument(
        "--sse-read-timeout",
        type=float,
        default=300.0,
        help="SSE 读超时秒数（长查询可调大，默认 300）",
    )
    p.add_argument("--list-tools", action="store_true", help="列出服务端工具后退出")
    p.add_argument(
        "-t",
        "--tool",
        help="工具名，如 search_core_isa_manuals、search_rvv_vector_extensions、search_special_instructions、search_docs_tools",
    )
    p.add_argument("-q", "--query", help="传给工具的 query 参数（ISA/RVV 检索类工具）")
    p.add_argument(
        "--args-json",
        help='工具参数 JSON 对象字符串（与 -q 互斥时优先；例：\'{"query":"add"}\'）',
    )
    args = p.parse_args()

    headers: dict[str, str] | None = None
    if args.header:
        headers = {}
        for h in args.header:
            if ":" not in h:
                print(f"无效 --header（需 KEY:VALUE）: {h}", file=sys.stderr)
                sys.exit(2)
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    tool_args: dict[str, Any] = {}
    if args.args_json:
        tool_args = json.loads(args.args_json)
    elif args.query is not None:
        tool_args = {"query": args.query}
    elif args.tool and not args.list_tools:
        print("指定了 --tool 但未提供 -q/--query 或 --args-json", file=sys.stderr)
        sys.exit(2)

    code = asyncio.run(
        _run(
            transport=args.transport,
            url=args.url,
            headers=headers,
            sse_read_timeout=args.sse_read_timeout,
            list_tools=args.list_tools,
            tool_name=args.tool,
            tool_args=tool_args,
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
