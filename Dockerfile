# The dashboard image (v3 slice 4, PRD FR21; notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md). Base pinned by tag AND
# index digest; `make lock-dashboard` compiles requirements-dashboard.txt inside this same image. Only the hash-checked,
# binary-only pip install reaches the network. The package runs from source (PYTHONPATH, F2): pip cannot hash-check a local
# directory. No `# syntax=` line: it would pull an unpinned frontend image.
FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app
COPY requirements-dashboard.txt ./
RUN pip install --require-hashes --only-binary=:all: --no-deps -r requirements-dashboard.txt
COPY src/readmission_risk ./src/readmission_risk
COPY scripts/dashboard_app.py scripts/dashboard_image_check.py ./scripts/
COPY .streamlit/config.toml ./.streamlit/config.toml
ARG GIT_COMMIT
ENV PYTHONPATH=/app/src READMISSION_RISK_GIT_COMMIT=${GIT_COMMIT}
USER 10001
EXPOSE 8080
CMD ["sh", "-c", "exec streamlit run scripts/dashboard_app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]
