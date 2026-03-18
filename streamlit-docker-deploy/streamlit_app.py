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
    return json.loads(_strip_code_fences(text))


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def _try_parse_json_response(text: str) -> Optional[dict]:
    try:
        return json.loads(_strip_code_fences(text))
    except json.JSONDecodeError:
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


def _build_folder_batch_prompt(
    gs_prefix: str, batch_count: int, random_sample: bool, url_1: str, url_2: str
) -> str:
    random_value = "true" if random_sample else "false"
    return (
        "Use compare_invoices_gcs_folder for a folder batch comparison.\n"
        "Call compare_invoices_gcs_folder with:\n"
        f"gcs_prefix: {gs_prefix}\n"
        f"batch_file_count: {batch_count}\n"
        f"random_sample: {random_value}\n"
        f"url_1: {url_1}\n"
        f"url_2: {url_2}\n\n"
        "Return only the batch summary JSON."
    )


def _build_report_analysis_prompt(
    report_gcs_uri: str, question: str, filename: Optional[str] = None
) -> str:
    lines = [
        "Use analyze_invoice_comparison_report with:",
        f"report_gcs_uri: {report_gcs_uri}",
    ]
    if filename:
        lines.append(f"filename: {filename}")
    lines.append(f"question: {question}")
    lines.append("")
    lines.append("Return a human-readable answer.")
    return "\n".join(lines)


def _extract_report_gcs_uri(payload: dict) -> Optional[str]:
    for key in ("report_gcs_uri", "reportUri", "report_uri"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_selected_files(payload: dict) -> list[str]:
    candidates = []
    for key in ("selected_files", "selected_filenames", "files", "filenames"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates = value
            break

    selected_files = []
    for item in candidates:
        if isinstance(item, str) and item.strip():
            selected_files.append(item.strip())
            continue
        if isinstance(item, dict):
            for key in ("filename", "name", "gs_uri", "uri"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    selected_files.append(os.path.basename(value.strip()))
                    break
    return selected_files


def _summarize_batch_json(payload: dict) -> str:
    selected_files = _extract_selected_files(payload)
    processed = payload.get("processed_file_count")
    if processed is None:
        processed = payload.get("processed_files")
    matched = payload.get("matched_file_count")
    mismatched = payload.get("mismatched_file_count")
    failures = payload.get("failure_count")
    report_gcs_uri = _extract_report_gcs_uri(payload)

    lines = ["### Batch Comparison Summary"]
    if processed is not None:
        lines.append(f"Processed file count: {processed}")
    if matched is not None:
        lines.append(f"Matched files: {matched}")
    if mismatched is not None:
        lines.append(f"Mismatched files: {mismatched}")
    if failures is not None:
        lines.append(f"Failures: {failures}")
    if selected_files:
        lines.append("")
        lines.append("Selected files:")
        for filename in selected_files:
            lines.append(f"- {filename}")
    if report_gcs_uri:
        lines.append("")
        lines.append(f"Saved report: `{report_gcs_uri}`")
    return "\n".join(lines)


def _store_batch_report(
    payload: dict,
    selected_files_hint: Optional[list[str]] = None,
    summary_text: Optional[str] = None,
) -> bool:
    report_gcs_uri = _extract_report_gcs_uri(payload)
    if not report_gcs_uri:
        return False

    selected_files = _extract_selected_files(payload)
    if not selected_files and selected_files_hint:
        selected_files = list(selected_files_hint)

    st.session_state.last_batch_report_gcs_uri = report_gcs_uri
    st.session_state.last_batch_result_json = payload
    st.session_state.last_batch_selected_files = selected_files
    st.session_state.last_batch_summary_text = summary_text or _summarize_batch_json(payload)
    return True


def _try_store_batch_report_from_text(
    text: str, selected_files_hint: Optional[list[str]] = None
) -> bool:
    payload = _try_parse_json_response(text)
    if not isinstance(payload, dict):
        return False
    return _store_batch_report(payload, selected_files_hint=selected_files_hint)


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
if "last_batch_report_gcs_uri" not in st.session_state:
    st.session_state.last_batch_report_gcs_uri = ""
if "last_batch_result_json" not in st.session_state:
    st.session_state.last_batch_result_json = {}
if "last_batch_selected_files" not in st.session_state:
    st.session_state.last_batch_selected_files = []
if "last_batch_summary_text" not in st.session_state:
    st.session_state.last_batch_summary_text = ""

with st.sidebar:
    st.image(LABEL_PATH, width="stretch")
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
    st.subheader("Batch Report")
    has_saved_report = bool(st.session_state.last_batch_report_gcs_uri)
    st.write(f"Available: {'Yes' if has_saved_report else 'No'}")
    if has_saved_report:
        st.code(st.session_state.last_batch_report_gcs_uri)
        if st.session_state.last_batch_selected_files:
            st.caption("Selected files")
            for filename in st.session_state.last_batch_selected_files:
                st.write(f"- {filename}")
    else:
        st.caption("No saved batch report yet.")

    report_file_options = ["All files"] + st.session_state.last_batch_selected_files
    report_followup_file = st.selectbox(
        "Report file filter",
        options=report_file_options,
        disabled=not has_saved_report,
    )
    report_followup_question = st.text_input(
        "Ask about last batch report",
        placeholder="Summarize the mismatches",
        disabled=not has_saved_report,
    )
    run_report_analysis = st.button(
        "Analyze last batch report", disabled=not has_saved_report
    )
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.session_state.last_batch_report_gcs_uri = ""
        st.session_state.last_batch_result_json = {}
        st.session_state.last_batch_selected_files = []
        st.session_state.last_batch_summary_text = ""
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
                    selected_files = [os.path.basename(gs_uri) for gs_uri in selected_uris]
                    batch_prompt = _build_folder_batch_prompt(
                        gcs_prefix.strip(),
                        int(batch_count),
                        batch_random,
                        url_1.strip(),
                        url_2.strip(),
                    )
                    placeholder.markdown("Running folder batch comparison...")
                    response_text = _query_text(engine.strip(), batch_prompt, user_id.strip())
                    payload = _try_parse_json_response(response_text)
                    if isinstance(payload, dict):
                        summary = _summarize_batch_json(payload)
                        _store_batch_report(
                            payload,
                            selected_files_hint=selected_files,
                            summary_text=summary,
                        )
                    else:
                        summary = response_text
                    placeholder.markdown(summary)
            except requests.HTTPError as exc:
                summary = f"HTTP error: {exc}"
                placeholder.markdown(summary)
            except Exception as exc:
                summary = f"Error: {exc}"
                placeholder.markdown(summary)
        st.session_state.messages.append({"role": "assistant", "content": summary})

if run_report_analysis:
    if not engine.strip():
        st.error("Please enter the Reasoning Engine resource name.")
    elif not report_followup_question.strip():
        st.error("Please enter a question for the saved batch report.")
    elif not st.session_state.last_batch_report_gcs_uri:
        st.error("No saved batch report is available.")
    else:
        selected_filename = None
        if report_followup_file != "All files":
            selected_filename = report_followup_file
        prompt_text = _build_report_analysis_prompt(
            st.session_state.last_batch_report_gcs_uri,
            report_followup_question.strip(),
            filename=selected_filename,
        )
        visible_question = report_followup_question.strip()
        if selected_filename:
            visible_question = f"{visible_question} (file: {selected_filename})"
        st.session_state.messages.append({"role": "user", "content": visible_question})
        with st.chat_message("user"):
            st.markdown(visible_question)
        with st.chat_message("assistant", avatar=LOGO_PATH):
            placeholder = st.empty()
            try:
                response_text = _query_text(engine.strip(), prompt_text, user_id.strip())
                placeholder.markdown(response_text)
            except requests.HTTPError as exc:
                response_text = f"HTTP error: {exc}"
                placeholder.markdown(response_text)
            except Exception as exc:
                response_text = f"Error: {exc}"
                placeholder.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})

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
        _try_store_batch_report_from_text(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
