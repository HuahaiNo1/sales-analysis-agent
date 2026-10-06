#!/usr/bin/env python3
"""One-use loopback form. Only the human browser user enters the API key."""

import hmac
import html
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
TOKEN = secrets.token_urlsafe(32)
PORT = 8766
ORIGIN = f"http://127.0.0.1:{PORT}"
PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>手动配置本地 .env</title><style>body{font:16px system-ui;background:#f5f6f8;color:#17212b;padding:8vh 20px}main{max-width:520px;margin:auto;background:white;padding:36px;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:14px;font:inherit;margin:12px 0;border:1px solid #ccd3d8;border-radius:8px}button{background:#12615b;color:white}p{line-height:1.7;color:#52626a}</style><main><h1>手动配置 DeepSeek</h1><p>密钥只由你在此页输入，保存到当前项目的 .env（权限 0600）。不会显示在聊天中，也不会加入 Git。保存后仍为 mock 模式，待确认后再启用真实调用。</p><form method="post" action="/save" autocomplete="off"><input type="hidden" name="csrf" value="TOKEN"><label>DEEPSEEK_API_KEY<input type="password" name="key" required minlength="12" maxlength="256" autocomplete="new-password" autofocus></label><p>LangSmith 追踪强制关闭，无需 LangSmith 密钥</p><button type="submit">保存到本地 .env</button></form></main></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, content):
        body = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}" or self.path != "/":
            return self.reply(404, "Not found")
        self.reply(200, PAGE.replace("TOKEN", html.escape(TOKEN)))

    def do_POST(self):
        if (
            self.path != "/save"
            or self.headers.get("Origin") != ORIGIN
            or self.headers.get("Host") != f"127.0.0.1:{PORT}"
        ):
            return self.reply(403, "Forbidden")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 1024:
            return self.reply(400, "Invalid request")
        data = parse_qs(self.rfile.read(size).decode("utf-8"), strict_parsing=True)
        if set(data) != {"csrf", "key"} or not hmac.compare_digest(data.get("csrf", [""])[0], TOKEN):
            return self.reply(403, "Forbidden")
        key = data["key"][0].strip()
        if not 12 <= len(key) <= 256 or any(c.isspace() or c in "\"'\\" for c in key):
            return self.reply(400, "密钥格式无效，请返回重新输入")
        path = ROOT / ".env"
        # Preserve unrelated local settings without ever printing any contents.
        values = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    values[k.strip()] = v
        values.update(
            DEEPSEEK_API_KEY=key, AGENT_MODE="mock", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false"
        )
        values.pop("LANGSMITH_API_KEY", None)
        temp = ROOT / ".env.pending"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        self.reply(
            200,
            '<meta charset="utf-8"><h2>已保存</h2><p>此一次性入口现已关闭。当前仍为 mock 模式，请回到聊天确认继续。</p>',
        )
        threading.Thread(target=self.server.shutdown, daemon=True).start()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Manual setup ready: {ORIGIN}", flush=True)
    server.serve_forever()
    server.server_close()
    print("Manual setup saved; write endpoint closed. No live call made.", flush=True)
