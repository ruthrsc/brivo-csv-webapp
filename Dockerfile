FROM python:3.9-alpine

WORKDIR /python-docker
ENV TZ=America/Los_Angeles
ENV WRITABLE_BASE_PATH=/python-docker/writable
RUN apk add --no-cache bash

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY app ./app
RUN mkdir writable
RUN chown 50000:50000 writable
RUN chmod 700 writable
USER 50000:50000

EXPOSE 8080
#CMD [ "python3", "-m" , "flask", "--app", "webapp.py", "run", "--host=0.0.0.0", "--port=8080"]
CMD ["gunicorn", "app.webapp:gunicorn_app()", \
     "-w 4", "-b 0.0.0.0:8080", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output", \
     "--timeout", "300"]
