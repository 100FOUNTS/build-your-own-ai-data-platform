FROM python:3.12-slim
RUN pip install --no-cache-dir confluent-kafka numpy
WORKDIR /sim
COPY physics.py sim.py ./
ENTRYPOINT ["python", "-u", "sim.py"]
