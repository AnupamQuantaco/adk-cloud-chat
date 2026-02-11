# Streamlit Docker Deploy

This folder is a standalone Streamlit Docker setup based on the official tutorial.

## Build
Run from the repo root:

```bash
docker build -t streamlit-docker-deploy streamlit-docker-deploy
```

## Run

```bash
docker run --rm -p 8501:8501 streamlit-docker-deploy
```

Then open `http://localhost:8501`.

## Use your app
Replace `streamlit-docker-deploy/streamlit_app.py` with your Streamlit app.
