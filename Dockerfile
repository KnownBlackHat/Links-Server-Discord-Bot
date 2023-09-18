FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=false

WORKDIR /app

RUN apt update && apt install ffmpeg -y

COPY . .

RUN pip install -r requirements.txt

ENTRYPOINT ["python3", "bot.py"]
