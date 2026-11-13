FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY specs ./specs
COPY .claude/agents ./.claude/agents
# 회의·녹음·분석 결과는 MAA_RUNTIME_DIR에 저장한다 (무료 배포는 임시 폴더, 영구 저장은 디스크를 연결해 덮어쓴다).
ENV MAA_RUNTIME_DIR=/tmp/maa MAA_MODE=live
EXPOSE 8000
CMD ["sh", "-c", "uvicorn meeting_action_agent.api.app:create_app_from_env --factory --host 0.0.0.0 --port ${PORT:-8000}"]
