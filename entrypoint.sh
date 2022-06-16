#!/bin/bash

cat <<-EOF > /etc/nginx/conf.d/web.conf
server {
    listen       ${PORT};
    listen       [::]:${PORT};
    client_max_body_size 2G;
    charset utf-8;

    location / {
        alias /data/Downloads/;
        autoindex on;
    }
}
EOF

cat <<-EOF > /bot.cfg
{
    "token":"",
    "work_dir": "/data/Downloads",
    "chat_id": [],
    "public": true
}
EOF

wget https://github.com/yt-dlp/yt-dlp/releases/download/2022.05.18/yt-dlp -O /usr/bin/yt-dlp
chmod +x /usr/bin/yt-dlp

cat /etc/nginx/conf.d/web.conf
rm -rf /etc/nginx/sites-enabled/default
nginx

python3 /main.py
