FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN set -ex &&\
    apt-get -y update && \
    apt-get install -y tzdata locales && \
    apt-get install -y curl wget unzip && \
    apt-get install -y dnsutils netcat net-tools iputils-ping iproute2 && \
    apt-get install -y nginx python3 python3-pip && \
    apt-get clean -y && apt-get autoremove -y && \
    rm /etc/update-motd.d/* && \
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen

RUN set -ex\
    && pip3 install psutil pyTelegramBotAPI \
    && pip3 install python-telegram-bot --pre \
    && pip3 install you-get \
    && pip3 install youtube_dl

ENV LC_ALL=en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US.UTF-8

COPY entrypoint.sh /entrypoint.sh
COPY main.py /main.py

RUN chmod +x /entrypoint.sh \
    && chmod +x /main.py

CMD /entrypoint.sh
