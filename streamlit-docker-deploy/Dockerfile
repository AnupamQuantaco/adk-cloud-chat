FROM python:3.9-slim

WORKDIR /app

COPY streamlit-docker-deploy/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY streamlit-docker-deploy/ /app

EXPOSE 8080

CMD ["sh", "-c", "streamlit run streamlit_app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]
