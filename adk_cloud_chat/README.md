# ADK Cloud Chat

Minimal chat UI for a deployed Vertex AI Reasoning Engine.
This is separate from the local ADK Web UI and does not modify the agent code.

## Setup

```bash
cd adk_cloud_chat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

gcloud auth application-default login
```

## Run

```bash
export REASONING_ENGINE="projects/PROJECT/locations/REGION/reasoningEngines/ENGINE_ID"
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501).

## Notes

- You can also paste the Reasoning Engine resource name directly in the UI.
- The app derives the region from the resource name. If it can’t, it defaults to `us-central1`.
