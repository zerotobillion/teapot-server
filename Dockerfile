FROM python:3.6

WORKDIR /

COPY Pipfile .
COPY Pipfile.lock .
COPY .env .
COPY server.py .
COPY emailhelper.py .
COPY home.html .
COPY japronto .

RUN pip install pipenv
RUN pipenv install --deploy
