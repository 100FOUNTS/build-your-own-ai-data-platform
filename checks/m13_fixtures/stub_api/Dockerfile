FROM python:3.12-slim
ARG MODE=good
ENV STUB_MODE=${MODE} PYTHONUNBUFFERED=1
COPY server.py /app/server.py
USER 10001
EXPOSE 8000
CMD ["python", "/app/server.py"]
