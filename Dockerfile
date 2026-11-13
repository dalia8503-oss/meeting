FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY specs ./specs
COPY .claude/agents ./.claude/agents
# 회의·녹음·분석 결과는 MAA_RUNTIME_DIR(영구 볼륨)에 저장한다.
ENV MAA_RUNTIME_DIR=/data MAA_MODE=live
EXPOSE 8000
CMD ["sh", "-c", "uvicorn meeting_action_agent.api.app:create_app_from_env --factory --host 0.0.0.0 --port ${PORT:-8000}"]
