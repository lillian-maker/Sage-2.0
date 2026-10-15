FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY build.py manage.py /app/
COPY src /app/src
COPY assets /app/assets
COPY backend /app/backend
RUN python build.py --release && SAGE_ENV=test /opt/venv/bin/python manage.py collectstatic --noinput

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS app
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 sage && useradd --uid 10001 --gid sage --no-create-home sage && mkdir /app/media && chown sage:sage /app/media && chmod 700 /app/media
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/backend /app/backend
COPY --from=build /app/manage.py /app/manage.py
COPY --from=build /app/src/pages/billing-catalog.json /app/src/pages/billing-catalog.json
COPY drafts/restricted-deploy/docker-entrypoint.py drafts/restricted-deploy/docker-gunicorn.conf.py /app/deploy/
USER 10001:10001
ENTRYPOINT ["python", "/app/deploy/docker-entrypoint.py"]
CMD ["gunicorn", "backend.wsgi:application", "--config", "/app/deploy/docker-gunicorn.conf.py"]

FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236 AS web
COPY --from=build /app/dist /srv/sage/dist
COPY --from=build /app/tmp/admin-static /srv/sage/static
COPY drafts/restricted-deploy/docker-nginx.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
