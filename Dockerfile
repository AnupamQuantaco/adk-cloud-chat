FROM python:3.12-slim

ENV PYTHONUNBUFFERED True

WORKDIR /app
COPY adk_cloud_chat /app/adk_cloud_chat

RUN pip install --no-cache-dir -r /app/adk_cloud_chat/requirements.txt

# used to check that the cloudrun image was built with this dockerfile and not the default
ENV NAME "builtwithdocker"

WORKDIR /app/adk_cloud_chat
CMD ["gunicorn", "--bind", ":8000", "--workers", "1", "--threads", "4", "--timeout", "0", "app:app"]
