FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ---- System deps for OpenCV, numpy, PyTorch ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ---- Copy requirements ----
COPY requirements.txt /app/

# ---- Install Python deps ----
RUN pip install fastapi uvicorn python-multipart pydantic python-dotenv

# ---- Copy full project ----
COPY . /app

# Expose FastAPI port
EXPOSE 8000

# Start server (your app is in main.py)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]