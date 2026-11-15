FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir .
# The official Anthropic CLI for «Войти через Anthropic» in the assistant: pinned version, SHA-256 checked.
COPY docker/install_ant.py /tmp/install_ant.py
RUN python /tmp/install_ant.py && rm /tmp/install_ant.py

EXPOSE 8000
# Apply migrations, then start the API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
