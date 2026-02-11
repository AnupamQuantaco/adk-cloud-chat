import json
import os
import tempfile
import uuid
from typing import Generator, Optional

import google.auth
import google.auth.transport.requests
import requests


def _maybe_write_creds_from_env() -> None:
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw:
        return
    try:
        creds_dict = json.loads(raw)
    except json.JSONDecodeError:
        return
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        json.dump(creds_dict, handle)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = handle.name


def _get_access_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds.token


def _extract_text(event: dict) -> Optional[str]:
    content = event.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return None
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if text:
            texts.append(text)
    if texts:
        return "".join(texts)
    return None


def _infer_region(engine: str) -> str:
    marker = "/locations/"
    if marker in engine:
        rest = engine.split(marker, 1)[1]
        region = rest.split("/", 1)[0]
        if region:
            return region
    return "us-central1"


def _stream_query(engine: str, message: str, user_id: str) -> Generator[str, None, None]:
    region = _infer_region(engine)
    url = f"https://{region}-aiplatform.googleapis.com/v1/{engine}:streamQuery"
    payload = {
        "classMethod": "stream_query",
        "input": {"message": message, "user_id": user_id},
    }

    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    final_text = ""
    with requests.post(
        url, headers=headers, data=json.dumps(payload), stream=True, timeout=300
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _extract_text(event)
            if text:
                final_text = text
                yield final_text

    if not final_text:
        yield "No text response returned."

def _query(engine: str, message: str, user_id: str) -> str:
    final_text = ""
    for partial in _stream_query(engine, message, user_id):
        final_text = partial
    return final_text or "No text response returned."


def _json_response(start_response, status: str, payload: dict) -> list[bytes]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    start_response(status, headers)
    return [body]


def _html_response(start_response, status: str, html: str) -> list[bytes]:
    body = html.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    start_response(status, headers)
    return [body]


def _read_json_body(environ) -> dict:
    try:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        content_length = 0
    raw = environ["wsgi.input"].read(content_length) if content_length > 0 else b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _render_index() -> str:
    default_engine = os.getenv("REASONING_ENGINE", "")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>ADK Cloud Chat</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f3ef;
        --panel: #ffffff;
        --ink: #1f2430;
        --muted: #5a6172;
        --accent: #2e6bf6;
        --border: #e3dfd8;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
        background: radial-gradient(circle at top, #ffffff 0%, var(--bg) 50%, #efe9e2 100%);
        color: var(--ink);
      }}
      header {{
        padding: 32px 24px 12px;
        text-align: center;
      }}
      h1 {{
        margin: 0 0 8px;
        font-size: 28px;
      }}
      p {{
        margin: 0;
        color: var(--muted);
      }}
      main {{
        max-width: 900px;
        margin: 0 auto;
        padding: 12px 24px 48px;
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 20px;
        box-shadow: 0 10px 24px rgba(16, 24, 40, 0.08);
      }}
      label {{
        display: block;
        font-size: 13px;
        color: var(--muted);
        margin-bottom: 6px;
      }}
      input, textarea {{
        width: 100%;
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid var(--border);
        font-size: 14px;
      }}
      textarea {{
        min-height: 110px;
        resize: vertical;
      }}
      button {{
        margin-top: 12px;
        background: var(--accent);
        color: #fff;
        border: none;
        padding: 10px 16px;
        border-radius: 10px;
        font-weight: 600;
        cursor: pointer;
      }}
      button:disabled {{
        opacity: 0.6;
        cursor: not-allowed;
      }}
      .chat {{
        margin-top: 20px;
        display: grid;
        gap: 12px;
      }}
      .bubble {{
        padding: 12px 14px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: #f9f8f6;
      }}
      .bubble.user {{
        background: #e8efff;
        border-color: #d1ddff;
      }}
      .meta {{
        font-size: 12px;
        color: var(--muted);
        margin-bottom: 6px;
      }}
      .error {{
        color: #b42318;
        font-size: 13px;
        margin-top: 8px;
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>ADK Cloud Chat</h1>
      <p>Chat with a deployed Vertex AI Reasoning Engine.</p>
    </header>
    <main>
      <div class="panel">
        <label for="engine">Reasoning Engine resource name</label>
        <input id="engine" placeholder="projects/PROJECT/locations/REGION/reasoningEngines/ENGINE_ID" value="{default_engine}"/>
        <label for="message" style="margin-top: 12px;">Message</label>
        <textarea id="message" placeholder="Ask a question"></textarea>
        <button id="send">Send</button>
        <div id="error" class="error" style="display:none;"></div>
        <div class="chat" id="chat"></div>
      </div>
    </main>
    <script>
      const chatEl = document.getElementById("chat");
      const sendBtn = document.getElementById("send");
      const msgEl = document.getElementById("message");
      const engineEl = document.getElementById("engine");
      const errorEl = document.getElementById("error");
      const userKey = "adk_user_id";
      const userId = localStorage.getItem(userKey) || Math.random().toString(36).slice(2, 10);
      localStorage.setItem(userKey, userId);

      function addBubble(role, text) {{
        const wrapper = document.createElement("div");
        wrapper.className = "bubble " + role;
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = role === "user" ? "You" : "Assistant";
        const body = document.createElement("div");
        body.textContent = text;
        wrapper.appendChild(meta);
        wrapper.appendChild(body);
        chatEl.appendChild(wrapper);
        wrapper.scrollIntoView({{behavior: "smooth", block: "end"}});
      }}

      function setError(message) {{
        if (!message) {{
          errorEl.style.display = "none";
          errorEl.textContent = "";
          return;
        }}
        errorEl.style.display = "block";
        errorEl.textContent = message;
      }}

      sendBtn.addEventListener("click", async () => {{
        setError("");
        const message = msgEl.value.trim();
        const engine = engineEl.value.trim();
        if (!message) {{
          setError("Please enter a message.");
          return;
        }}
        if (!engine) {{
          setError("Please enter the Reasoning Engine resource name.");
          return;
        }}
        addBubble("user", message);
        msgEl.value = "";
        sendBtn.disabled = true;
        try {{
          const resp = await fetch("/chat", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{ message, engine, user_id: userId }})
          }});
          const data = await resp.json();
          if (!resp.ok) {{
            throw new Error(data.error || "Request failed.");
          }}
          addBubble("assistant", data.response || "No response.");
        }} catch (err) {{
          setError(err.message);
        }} finally {{
          sendBtn.disabled = false;
        }}
      }});
    </script>
  </body>
</html>"""


def app(environ, start_response):
    _maybe_write_creds_from_env()
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")

    if path == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    if method == "GET" and path == "/":
        return _html_response(start_response, "200 OK", _render_index())

    if method == "POST" and path == "/chat":
        body = _read_json_body(environ)
        message = (body.get("message") or "").strip()
        engine = (body.get("engine") or "").strip()
        user_id = (body.get("user_id") or "").strip() or str(uuid.uuid4())[:8]
        if not engine or not message:
            return _json_response(
                start_response,
                "400 Bad Request",
                {"error": "Both 'engine' and 'message' are required."},
            )
        try:
            response_text = _query(engine, message, user_id)
        except requests.HTTPError as exc:
            return _json_response(
                start_response,
                "502 Bad Gateway",
                {"error": f"HTTP error: {exc}"},
            )
        except Exception as exc:
            return _json_response(
                start_response,
                "500 Internal Server Error",
                {"error": f"Error: {exc}"},
            )
        return _json_response(
            start_response,
            "200 OK",
            {"response": response_text},
        )

    return _json_response(
        start_response,
        "404 Not Found",
        {"error": "Not found."},
    )


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    port = int(os.getenv("PORT", "8000"))
    with make_server("", port, app) as server:
        print(f"Listening on http://0.0.0.0:{port}")
        server.serve_forever()
