from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, ErrorData, JSONRPCMessage, TextContent, Tool, ToolAnnotations

from .common import DomainError, digest, dumps, failed, strict_loads
from .service import CONTRACT, CONTRACT_V2, Service

DESCRIPTIONS = {
    "workspace_status": "프로젝트와 작업 목록, 수집 및 모델 상태 조회",
    "work_open": "저장 작업 복원 또는 내부 작업 생성 (Codex 대화 생성 아님)",
    "work_record": "목표 범위·결정·근거·진행 기록, optimistic revision 필요",
    "source_sync": "설치 허용 파일 또는 출처 있는 발췌 등록",
    "context_prepare": "한국어 근거 검색과 제약·알려진 충돌을 보존하는 문맥 구성",
    "source_read": "고정 revision의 원문을 완전한 행 단위로 조회",
    "work_inspect": "작업의 이력·결정·충돌·근거·mutation 조회",
    "data_forget": "사용자가 요청한 source 또는 work 논리 삭제; 원본 파일 유지",
    "capability_recommend": "현재 도구·스킬·worker 목록에서 구성된 모델로 후보 추천; 권한 부여나 실행 아님",
    "handoff_prepare": "목표·범위·근거·회차를 고정한 읽기 작업 전달 묶음 준비; 실행은 호스트 책임",
}


@asynccontextmanager
async def strict_stdio(contract=CONTRACT):
    """Bounded UTF-8 frames and duplicate-key rejection before SDK deserialization."""
    incoming_send, incoming = anyio.create_memory_object_stream(0)
    outgoing, outgoing_receive = anyio.create_memory_object_stream(0)
    output_lock = anyio.Lock()

    async def write(value):
        async with output_lock:
            await anyio.to_thread.run_sync(sys.stdout.buffer.write, (dumps(value) + "\n").encode())
            await anyio.to_thread.run_sync(sys.stdout.buffer.flush)

    async def reader():
        async with incoming_send:
            while True:
                line = await anyio.to_thread.run_sync(
                    sys.stdin.buffer.readline, 524289, abandon_on_cancel=True
                )
                if not line:
                    break
                try:
                    if len(line) > 524288:
                        # Drain the rejected frame without retaining an unbounded body.
                        while not line.endswith(b"\n"):
                            line = await anyio.to_thread.run_sync(
                                sys.stdin.buffer.readline, 524289, abandon_on_cancel=True
                            )
                            if not line:
                                break
                        raise ValueError("oversized frame")
                    value = strict_loads(line.decode("utf-8"))
                    message = JSONRPCMessage.model_validate(value)
                except (ValueError, UnicodeError, RecursionError):
                    await write(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Invalid JSON-RPC frame"},
                        }
                    )
                    continue
                if (
                    value.get("method") == "tools/call"
                    and value.get("params", {}).get("name") not in contract["tools"]
                ):
                    if "id" in value:
                        await write(
                            {
                                "jsonrpc": "2.0",
                                "id": value["id"],
                                "error": {"code": -32601, "message": "Unknown tool"},
                            }
                        )
                    continue
                await incoming_send.send(SessionMessage(message))

    async def writer():
        async with outgoing_receive:
            async for value in outgoing_receive:
                await write(value.message.model_dump(mode="json", by_alias=True, exclude_none=True))

    async with anyio.create_task_group() as group:
        group.start_soon(reader)
        group.start_soon(writer)
        try:
            yield incoming, outgoing
        finally:
            group.cancel_scope.cancel()


async def serve(config, engine=None, config_path=None):
    loaded_hash = digest(Path(config_path).read_bytes()) if config_path else None
    contract = CONTRACT_V2 if config.contract_version == "2.0" else CONTRACT
    server = Server("jev-context", version="0.2.0")
    limiter = anyio.CapacityLimiter(1)

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                description=DESCRIPTIONS[name],
                inputSchema=schema,
                outputSchema=contract["output"],
                annotations=ToolAnnotations(
                    readOnlyHint=name in {"workspace_status", "source_read", "work_inspect"},
                    destructiveHint=name == "data_forget",
                    openWorldHint=(
                        engine is not None
                        and engine.profile.get("family") == "openjev_modal"
                        and name in {"context_prepare", "capability_recommend", "handoff_prepare"}
                    ),
                    idempotentHint=name != "context_prepare",
                ),
            )
            for name, schema in contract["tools"].items()
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        if name not in contract["tools"]:
            raise McpError(ErrorData(code=-32601, message="Unknown tool"))

        def invoke():
            service = None
            try:
                service = Service(config, engine=engine)
                return service.call(name, arguments)
            except DomainError as exc:
                result = failed(arguments.get("request_id", "invalid"), exc)
                result["contract_version"] = config.contract_version
                return result
            finally:
                if service:
                    service.close()

        result = await anyio.to_thread.run_sync(invoke, limiter=limiter)
        if config_path and name == "workspace_status":
            from .onboarding import observe_confirmation

            client = server.request_context.session.client_params
            client_name = client.clientInfo.name if client else ""
            await anyio.to_thread.run_sync(
                observe_confirmation,
                config,
                name,
                arguments,
                result,
                client_name,
                config_path,
                loaded_hash,
                limiter=limiter,
            )
        return CallToolResult(
            structuredContent=result,
            content=[TextContent(type="text", text=dumps(result))],
            isError=result["outcome"] in {"error", "conflict"},
        )

    async with strict_stdio(contract) as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
