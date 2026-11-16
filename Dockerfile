FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY analysis/incident_detector.py analysis/incident_detector.py
COPY dashboard/index.html dashboard/index.html

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
