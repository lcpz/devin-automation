FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir '.[postgres]'
RUN mkdir -p /out && chown -R app:app /app /out
USER app

ENTRYPOINT ["devin-automation"]
