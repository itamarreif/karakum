#!/usr/bin/env python3
"""Stand-in for Bedrock's OpenAI-compatible endpoint. Stdlib only.

Mimics the two things that actually differ from api.openai.com:
  - base path is /openai/v1, not /v1
  - bearer-token auth (AWS_BEARER_TOKEN_BEDROCK)

MOCK_MODELS=0 makes GET /openai/v1/models return 404, to reproduce the
failure mode we can't confirm real Bedrock avoids.
"""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("MOCK_TOKEN", "test-token")
SERVE_MODELS = os.environ.get("MOCK_MODELS", "1") != "0"
MODEL_IDS = ["openai.gpt-5.6-terra", "openai.gpt-5.6-sol"]
REPLY = "Hello from the fake Bedrock endpoint. Streaming works."


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload, ctype="application/json"):
        body = json.dumps(payload).encode() if ctype == "application/json" else payload
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if self.headers.get("Authorization") == f"Bearer {TOKEN}":
            return True
        self._send(401, {"error": {"message": "invalid bearer token"}})
        return False

    def do_GET(self):
        if self.path.rstrip("/") != "/openai/v1/models":
            return self._send(404, {"error": {"message": f"no route {self.path}"}})
        if not self._authed():
            return
        if not SERVE_MODELS:
            return self._send(404, {"error": {"message": "models listing not available"}})
        self._send(200, {"object": "list", "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "bedrock"} for m in MODEL_IDS]})

    def do_POST(self):
        if self.path.rstrip("/") != "/openai/v1/chat/completions":
            return self._send(404, {"error": {"message": f"no route {self.path}"}})
        if not self._authed():
            return
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        model = req.get("model", MODEL_IDS[0])
        if not req.get("stream"):
            return self._send(200, {
                "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 9, "total_tokens": 10}})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for word in REPLY.split(" "):
            chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": model,
                     "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        done = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, fmt, *a):
        print(f"{self.command} {self.path} -> {a[1] if len(a) > 1 else ''}", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"fake bedrock on :{port}  models={'on' if SERVE_MODELS else 'OFF (404)'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
