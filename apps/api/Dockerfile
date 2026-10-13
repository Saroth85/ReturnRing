FROM python:3.12-slim
WORKDIR /app
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt && useradd --create-home app
COPY policy policy
COPY llm llm
COPY contracts contracts
COPY pipeline pipeline
COPY apps apps
USER app
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
