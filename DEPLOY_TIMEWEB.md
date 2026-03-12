# Деплой MiroFish в Timeweb Cloud

## Что уже подготовлено

- `docker-compose.timeweb.yml` — production compose для Timeweb App Platform
- `docker/timeweb/backend.Dockerfile` — backend на Flask + Gunicorn
- `docker/timeweb/frontend.Dockerfile` — сборка Vue и раздача через Nginx
- `docker/timeweb/nginx.conf` — reverse proxy для `/api`

## Важно про ограничения Timeweb App Platform

Согласно документации Timeweb App Platform:

- для Docker Compose нельзя использовать директиву `volumes`;
- нельзя использовать host-порты `80` и `443`;
- на основной домен проксируется только **первый** сервис в `docker-compose.yml`.

Поэтому в `docker-compose.timeweb.yml`:

- первым сервисом идёт `frontend`;
- наружу пробрасывается `8080:80`;
- `backend` доступен только внутри docker-сети;
- файлы загрузок хранятся внутри контейнера backend.

## Что нужно настроить

1. Создайте файл `.env` в корне проекта по образцу `.env.example`
2. Обязательно заполните:
   - `LLM_API_KEY`
   - `LLM_BASE_URL`
   - `LLM_MODEL_NAME`
   - `ZEP_API_KEY`
3. Для production рекомендуется добавить:
   - `FLASK_DEBUG=False`
   - `FLASK_HOST=0.0.0.0`
   - `FLASK_PORT=5001`
   - `SECRET_KEY=<случайная_строка>`

## Локальная проверка перед загрузкой

```bash
docker compose -f docker-compose.timeweb.yml up --build
```

После запуска:

- frontend: `http://localhost:8080`
- backend healthcheck: `http://localhost:8080/health`

## Деплой в Timeweb Cloud через GitHub

1. Загрузите форк в GitHub
2. В панели Timeweb откройте `App Platform` → `Создать`
3. Выберите тип `Docker Compose`
4. Подключите GitHub-репозиторий
5. Выберите ветку `main`
6. Оставьте включённым автодеплой по последнему коммиту
7. На шаге переменных добавьте значения из `.env`
8. Запустите деплой

После первого деплоя Timeweb выдаст технический домен и SSL.

## Если репозиторий приватный

Можно подключить его по HTTPS URL, но для GitHub нужен Personal Access Token с доступом к репозиторию.

## Следующий этап русификации

Сейчас в проект добавлена база для `vue-i18n`, а главная страница переведена на русский через словари `frontend/src/locales`.
Остальные экраны (`Process`, `Simulation`, `Report`, `Interaction` и крупные компоненты шагов) всё ещё содержат много захардкоженных китайских строк и требуют поэтапного переноса в словари.
