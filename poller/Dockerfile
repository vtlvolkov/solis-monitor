# --- tests: the image only builds if they pass -------------------------------------
FROM --platform=$BUILDPLATFORM python:3.12-slim AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY *.py pytest.ini ./
COPY tests ./tests
RUN python -m pytest -q -p no:cacheprovider && touch /tested

# --- runtime ------------------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY poller.py control.py device.py faults.py notify.py ./
COPY --from=test /tested /tmp/tested
ENV PYTHONUNBUFFERED=1
CMD ["python", "poller.py"]
