from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .framing import StdioFraming


JsonObject = Dict[str, Any]


@dataclass(frozen=True)
class Tool:
	name: str
	description: str
	input_schema: JsonObject
	handler: Callable[[JsonObject], Any]


class MCPServer:
	def __init__(
		self,
		*,
		name: str,
		version: str = "0.1.0",
		protocol_version: str = "2024-11-05",
		tools: Optional[List[Tool]] = None,
	) -> None:
		self.name = name
		self.version = version
		self.protocol_version = protocol_version
		self._tools = {tool.name: tool for tool in (tools or [])}

	def run_stdio(self) -> None:
		framing = StdioFraming()
		while True:
			framed = framing.read_message()
			if framed is None:
				return
			request = framed.body
			if not isinstance(request, dict):
				continue
			if "method" not in request:
				continue

			method = request.get("method")
			params = request.get("params") or {}
			request_id = request.get("id")

			if not isinstance(method, str):
				continue
			if not isinstance(params, dict):
				params = {}

			try:
				result = self._dispatch(method, params)
				if request_id is not None:
					framing.write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
			except Exception as exc:
				if request_id is None:
					continue
				framing.write_message(
					{
						"jsonrpc": "2.0",
						"id": request_id,
						"error": {
							"code": -32603,
							"message": str(exc) or "Internal error",
							"data": {"traceback": traceback.format_exc(limit=20)},
						},
					}
				)

	def _dispatch(self, method: str, params: JsonObject) -> JsonObject:
		if method == "initialize":
			return self._initialize(params)
		if method == "tools/list":
			return self._tools_list(params)
		if method == "tools/call":
			return self._tools_call(params)
		if method in ("shutdown", "exit"):
			return {}
		if method == "ping":
			return {}
		raise ValueError(f"Unknown method: {method}")

	def _initialize(self, params: JsonObject) -> JsonObject:
		return {
			"protocolVersion": self.protocol_version,
			"capabilities": {"tools": {}},
			"serverInfo": {"name": self.name, "version": self.version},
		}

	def _tools_list(self, params: JsonObject) -> JsonObject:
		_ = params  # cursor not implemented
		tools = []
		for tool in self._tools.values():
			tools.append(
				{
					"name": tool.name,
					"description": tool.description,
					"inputSchema": tool.input_schema,
				}
			)
		return {"tools": tools}

	def _tools_call(self, params: JsonObject) -> JsonObject:
		name = params.get("name")
		arguments = params.get("arguments") or {}
		if not isinstance(name, str) or not name:
			raise ValueError("tools/call: missing 'name'")
		if not isinstance(arguments, dict):
			raise ValueError("tools/call: 'arguments' must be an object")

		tool = self._tools.get(name)
		if tool is None:
			raise ValueError(f"Unknown tool: {name}")

		output = tool.handler(arguments)
		return {"content": self._to_content(output)}

	@staticmethod
	def _to_content(output: Any) -> List[JsonObject]:
		if isinstance(output, list) and all(isinstance(x, dict) and "type" in x for x in output):
			return output
		if isinstance(output, (dict, list)):
			return [{"type": "text", "text": str(output)}]
		if output is None:
			return [{"type": "text", "text": ""}]
		return [{"type": "text", "text": str(output)}]

