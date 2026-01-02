from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, Tuple


@dataclass(frozen=True)
class FramedMessage:
	headers: Dict[str, str]
	body: Dict[str, Any]


class StdioFraming:
	"""
	Implements MCP/LSP-style framing:
	  Content-Length: N\r\n
	  \r\n
	  <N bytes of JSON>
	"""

	def __init__(self, reader: BinaryIO | None = None, writer: BinaryIO | None = None) -> None:
		self._reader = reader or sys.stdin.buffer
		self._writer = writer or sys.stdout.buffer

	def read_message(self) -> FramedMessage | None:
		headers = self._read_headers()
		if headers is None:
			return None

		content_length = headers.get("content-length")
		if content_length is None:
			raise ValueError("Missing Content-Length header")
		try:
			length = int(content_length)
		except ValueError as exc:
			raise ValueError(f"Invalid Content-Length header: {content_length!r}") from exc

		raw = self._reader.read(length)
		if not raw:
			return None

		try:
			body = json.loads(raw.decode("utf-8"))
		except Exception as exc:
			raise ValueError("Invalid JSON body") from exc

		if not isinstance(body, dict):
			raise ValueError("JSON body must be an object")

		return FramedMessage(headers=headers, body=body)

	def write_message(self, message: Dict[str, Any]) -> None:
		raw = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
		header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
		self._writer.write(header)
		self._writer.write(raw)
		self._writer.flush()

	def _read_headers(self) -> Dict[str, str] | None:
		header_bytes = b""
		while True:
			line = self._reader.readline()
			if line == b"":
				return None
			if line in (b"\n", b"\r\n"):
				break
			header_bytes += line

		headers: Dict[str, str] = {}
		for raw_line in header_bytes.splitlines():
			if not raw_line.strip():
				continue
			key, value = self._split_header(raw_line)
			headers[key.lower()] = value
		return headers

	@staticmethod
	def _split_header(raw_line: bytes) -> Tuple[str, str]:
		try:
			key_b, value_b = raw_line.split(b":", 1)
		except ValueError as exc:
			raise ValueError(f"Invalid header line: {raw_line!r}") from exc
		return key_b.decode("ascii").strip(), value_b.decode("ascii").strip()

