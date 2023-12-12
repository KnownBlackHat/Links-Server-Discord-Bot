FROM ubuntu:latest

ENV PIP_NO_CACHE_DIR=false

WORKDIR /app

RUN apt update && apt install ffmpeg python3.11 python3-pip -y

COPY . .

RUN pip3 install -r requirements.txt

ENTRYPOINT ["python3", "bot.py"]
