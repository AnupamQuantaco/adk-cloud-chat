import json
import os
import random
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Generator, Optional

import google.auth
import google.auth.transport.requests
import requests
import streamlit as st
from google.cloud import storage


APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = str(APP_DIR / "assets" / "Qlogo.jpeg")
LABEL_PATH = str(APP_DIR / "assets" / "Quantacolabel.jpeg")


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
        result = event.get("result")
        if isinstance(result, dict):
            content = result.get("content")
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
            continue
        function_response = part.get("functionResponse")
        if isinstance(function_response, dict):
            response_payload = function_response.get("response")
            if isinstance(response_payload, dict):
                for key in ("output", "result", "text", "message"):
                    value = response_payload.get(key)
                    if isinstance(value, str) and value:
                        texts.append(value)
                        break
                else:
                    texts.append(json.dumps(response_payload))
    if texts:
        return "".join(texts)
    return None


def _parse_stream_event_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line or line.startswith("event:"):
        return None
    if line.startswith("data:"):
        line = line[len("data:"):].strip()
    if line == "[DONE]":
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _parse_json_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return json.loads(cleaned)


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
            event = _parse_stream_event_line(line)
            if event is None:
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


def _query_text(engine: str, message: str, user_id: str) -> str:
    return "".join(_stream_query(engine, message, user_id)) or "No text response returned."


def _list_pdf_gcs_uris(gs_prefix: str) -> list[str]:
    if not gs_prefix.startswith("gs://"):
        raise ValueError("GCS prefix must start with gs://")

    prefix_body = gs_prefix[5:]
    bucket_name, _, blob_prefix = prefix_body.partition("/")
    if not bucket_name:
        raise ValueError("Invalid GCS prefix")

    normalized_prefix = blob_prefix.rstrip("/")
    if normalized_prefix:
        normalized_prefix = f"{normalized_prefix}/"

    blobs = storage.Client().list_blobs(bucket_name, prefix=normalized_prefix)
    pdf_uris = []
    for blob in blobs:
        if blob.name.endswith("/") or not blob.name.lower().endswith(".pdf"):
            continue
        pdf_uris.append(f"gs://{bucket_name}/{blob.name}")
    return sorted(pdf_uris)


def _build_single_file_prompt(gs_uri: str, filename: str, url_1: str, url_2: str) -> str:
    return (
        "Use compare_invoices_gcs for exactly one invoice file.\n"
        "Do not compare a folder or multiple files.\n"
        "Call compare_invoices_gcs with:\n"
        f"gs_uri: {gs_uri}\n"
        f"filename: {filename}\n"
        f"url_1: {url_1}\n"
        f"url_2: {url_2}\n\n"
        "Return only the JSON report for that single file."
    )


def _summarize_batch_results(results: dict, failures: list[str]) -> str:
    matched_files = []
    mismatched_files = []
    field_counts: Counter[str] = Counter()

    for filename, report in results.items():
        comparison_results = report.get("comparison_results", [])
        mismatches = []
        for item in comparison_results:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip().lower()
            if "mismatch" not in status:
                continue
            key = item.get("key") or item.get("field") or "unknown_field"
            mismatches.append(str(key))
        if mismatches:
            mismatched_files.append((filename, mismatches))
            field_counts.update(mismatches)
        else:
            matched_files.append(filename)

    processed_count = len(results) + len(failures)
    lines = [
        "### Batch Comparison Summary",
        f"Processed file count: {processed_count}",
        f"Matched files: {len(matched_files)}",
        f"Mismatched files: {len(mismatched_files)}",
        f"Failures: {len(failures)}",
    ]

    if mismatched_files:
        lines.append("")
        lines.append("Mismatched files by field:")
        for filename, mismatches in mismatched_files:
            lines.append(f"- {filename}: {', '.join(mismatches)}")

    if field_counts:
        lines.append("")
        lines.append("Most common mismatched fields overall:")
        for field, count in field_counts.most_common():
            lines.append(f"- {field}: {count}")
    elif results:
        lines.append("")
        lines.append("No mismatches found in this batch.")

    if failures:
        lines.append("")
        lines.append("Failures:")
        for failure in failures:
            lines.append(f"- {failure}")

    return "\n".join(lines)


def _select_batch_files(
    pdf_uris: list[str], batch_count: int, random_sample: bool
) -> list[str]:
    if random_sample:
        return random.sample(pdf_uris, min(batch_count, len(pdf_uris)))
    return pdf_uris[:batch_count]


def _run_batch_comparison(
    engine: str,
    user_id: str,
    selected_uris: list[str],
    url_1: str,
    url_2: str,
    placeholder,
) -> str:
    reports = {}
    failures = []
    total = len(selected_uris)

    for index, gs_uri in enumerate(selected_uris, start=1):
        filename = os.path.basename(gs_uri)
        placeholder.markdown(f"Processing {index}/{total}: `{filename}`")
        prompt = _build_single_file_prompt(gs_uri, filename, url_1, url_2)
        try:
            final_text = _query_text(engine, prompt, user_id)
            reports[filename] = _parse_json_response(final_text)
        except Exception as exc:
            failures.append(f"{filename}: {exc}")

    return _summarize_batch_results(reports, failures)


st.set_page_config(page_title="ADK Cloud Chat", page_icon=LOGO_PATH)

st.title("ADK Cloud Chat")
st.caption("Quantaco AI assistant for Vertex AI Reasoning Engine.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_id" not in st.session_state:
    st.session_state.user_id = f"anon-{uuid.uuid4()}"

with st.sidebar:
    st.image(LABEL_PATH, use_container_width=True)
    st.header("Configuration")
    default_engine = os.getenv("REASONING_ENGINE", "")
    engine = st.text_input(
        "Reasoning Engine resource name",
        value=default_engine,
        placeholder="projects/PROJECT/locations/REGION/reasoningEngines/ENGINE_ID",
    )
    user_id = st.text_input("User ID", value=st.session_state.user_id)
    st.session_state.user_id = user_id
    st.subheader("Batch")
    gcs_prefix = st.text_input(
        "GCS folder/prefix",
        value=os.getenv("INVOICE_GCS_PREFIX", "gs://invoice-comparison/invoices/"),
        placeholder="gs://invoice-comparison/invoices/",
    )
    batch_count = st.number_input("Batch file count", min_value=1, max_value=50, value=5)
    batch_random = st.checkbox("Random sample", value=True)
    url_1 = st.text_input(
        "Extractor URL 1",
        value=os.getenv(
            "INVOICE_URL_1",
            "https://dev-ai-invoice-extractor-2-svc-602984880925.australia-southeast1.run.app/extract_iv3",
        ),
    )
    url_2 = st.text_input(
        "Extractor URL 2",
        value=os.getenv(
            "INVOICE_URL_2",
            "https://prod-ai-invoice-extr-2-svc-110995995347.australia-southeast1.run.app/extract_iv3",
        ),
    )
    run_batch = st.button("Run folder batch")
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if run_batch:
    if not engine.strip():
        st.error("Please enter the Reasoning Engine resource name.")
    elif not gcs_prefix.strip():
        st.error("Please enter a GCS folder/prefix.")
    else:
        summary = ""
        st.session_state.messages.append(
            {
                "role": "user",
                "content": f"Compare {batch_count} file(s) from {gcs_prefix.strip()}",
            }
        )
        with st.chat_message("user"):
            st.markdown(f"Compare `{batch_count}` file(s) from `{gcs_prefix.strip()}`")
        with st.chat_message("assistant", avatar=LOGO_PATH):
            placeholder = st.empty()
            try:
                pdf_uris = _list_pdf_gcs_uris(gcs_prefix.strip())
                if not pdf_uris:
                    summary = "No PDF files found in that GCS prefix."
                    placeholder.markdown(summary)
                else:
                    selected_uris = _select_batch_files(
                        pdf_uris, int(batch_count), batch_random
                    )
                    summary = _run_batch_comparison(
                        engine.strip(),
                        user_id.strip(),
                        selected_uris,
                        url_1.strip(),
                        url_2.strip(),
                        placeholder,
                    )
                    placeholder.markdown(summary)
            except requests.HTTPError as exc:
                summary = f"HTTP error: {exc}"
                placeholder.markdown(summary)
            except Exception as exc:
                summary = f"Error: {exc}"
                placeholder.markdown(summary)
        st.session_state.messages.append({"role": "assistant", "content": summary})

prompt = st.chat_input("Ask a question")
if prompt:
    if not engine.strip():
        st.error("Please enter the Reasoning Engine resource name.")
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant", avatar=LOGO_PATH):
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
