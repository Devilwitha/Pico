FROM alpine:3.20

RUN apk add --no-cache nginx jq

COPY nginx.conf /etc/nginx/picodesk.conf
COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh /etc/nginx/picodesk.conf && chmod a+x /run.sh

CMD [ "/run.sh" ]
