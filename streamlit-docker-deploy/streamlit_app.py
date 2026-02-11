import json
import os
import time
from typing import Generator, Optional
import uuid

import google.auth
import google.auth.transport.requests
import requests
import streamlit as st


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
    last_text = ""
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
                if text.startswith(last_text):
                    delta = text[len(last_text) :]
                    if delta:
                        yield delta
                else:
                    yield text
                last_text = text
                final_text = text

    if not final_text:
        yield "No text response returned."


st.set_page_config(page_title="ADK Cloud Chat", page_icon="assets/Qlogo.jpeg")

st.title("ADK Cloud Chat")
st.caption("Quantaco AI assistant for Vertex AI Reasoning Engine.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_id" not in st.session_state:
    st.session_state.user_id = f"anon-{uuid.uuid4()}"

with st.sidebar:
    st.image("assets/Quantacolabel.jpeg", use_container_width=True)
    st.header("Configuration")
    default_engine = os.getenv("REASONING_ENGINE", "")
    engine = st.text_input(
        "Reasoning Engine resource name",
        value=default_engine,
        placeholder="projects/PROJECT/locations/REGION/reasoningEngines/ENGINE_ID",
    )
    user_id = st.text_input("User ID", value=st.session_state.user_id)
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Ask a question")
if prompt:
    if not engine.strip():
        st.error("Please enter the Reasoning Engine resource name.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant", avatar="assets/Qlogo.jpeg"):
            placeholder = st.empty()
            try:
                chunks = list(_stream_query(engine.strip(), prompt, user_id.strip()))
                if len(chunks) > 1:
                    def _gen():
                        for chunk in chunks:
                            yield chunk

                    response_text = st.write_stream(_gen())
                else:
                    response_text = "".join(chunks)
                    if not response_text:
                        response_text = "No text response returned."
                    step = 20
                    for i in range(0, len(response_text), step):
                        placeholder.markdown(response_text[: i + step])
                        time.sleep(0.02)
            except requests.HTTPError as exc:
                response_text = f"HTTP error: {exc}"
                placeholder.markdown(response_text)
            except Exception as exc:
                response_text = f"Error: {exc}"
                placeholder.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
