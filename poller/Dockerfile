# --- tests: the image only builds if they pass -------------------------------------
FROM --platform=$BUILDPLATFORM python:3.12-slim AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY *.py pytest.ini ruff.toml ./
COPY tests ./tests
RUN ruff check . && python -m pytest -q -p no:cacheprovider && touch /tested

# --- runtime ------------------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY --from=test /tested /tmp/tested
ENV PYTHONUNBUFFERED=1
# Liveness only: the poll loop keeps turning and the control API answers. An unreachable inverter never fails it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('CONTROL_PORT', '8780'), timeout=4).status == 200 else 1)"
CMD ["python", "poller.py"]
