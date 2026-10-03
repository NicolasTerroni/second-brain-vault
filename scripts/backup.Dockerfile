# Backup runtime: rclone pinned by version and digest, plus zip (history archives) and tzdata (local log times).
FROM rclone/rclone:1.75.1@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5

RUN apk add --no-cache zip tzdata
