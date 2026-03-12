FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync

COPY backend ./backend
COPY .env.example ./

WORKDIR /app/backend

EXPOSE 5001

CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "300", "app:create_app()"]
