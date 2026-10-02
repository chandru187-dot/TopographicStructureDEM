FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY railmodel ./railmodel
RUN pip install --no-cache-dir .
RUN useradd -u 10001 -m modeluser
USER modeluser
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "railmodel.api:app", "--host", "0.0.0.0", "--port", "8080"]
