# InduVista Modbus simulator (Phase 1 dev tool).
# The tag config is mounted at /app/config/gas_flow_computer.json at runtime —
# NOT copied into the image, so config edits don't require a rebuild.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY simulator.py ./

EXPOSE 5020

CMD ["python", "simulator.py"]
