# Based on https://tools.ietf.org/html/rfc7168
import os
import time
import multiprocessing
import traceback

from japronto import Application
import click

import emailhelper

__version__ = '19.8.10'  # Year / Month / Day


# Configuration (load .env file if variables aren't present)
for _ in range(2):
    try:
        MIN_REQUESTS_COUNT = int(os.environ['MIN_REQUESTS_COUNT'])
        SERVER_HOST = os.environ['SERVER_HOST']
        SERVER_PORT = os.environ['SERVER_PORT']
        SERVER_WORKER_NUM = int(os.environ['SERVER_WORKER_NUM'])
        SMTP_USER, SMTP_PASS, SMTP_SERVER, SMTP_PORT = os.environ['EMAIL_CREDS'].split(':')
        SMTP_PORT = int(SMTP_PORT)
        EMAIL_RECEIVER = [e for e in os.environ['EMAIL_RECEIVER'].split(';') if e]
    except KeyError:
        import dotenv
        dotenv.load_dotenv('.env', override=True)
    else:
        break


TEA_CONTENT_TYPE = 'message/teapot'
TEA_VARIANTS = [
    'english-breakfast',
    'earl-grey',
]
HIGH_TRAFFIC_VARIANT = 'earl-grey'

with open('home.html') as home_html_file:
    HOME_HTML_CONTENT = home_html_file.read()


def create_alternates():
    return ', '.join(
        f'{{"/{variant}" {{type {TEA_CONTENT_TYPE}}}}}'
        for variant in TEA_VARIANTS
    )


TEA_ALTERNATES = create_alternates()

email_client = emailhelper.GmailSender(SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASS)

# Runtime variables
mp_manager = multiprocessing.Manager()

POTS_BREWING = mp_manager.dict()

TRAFFIC = mp_manager.dict()
TRAFFIC_LOCK_INCREASE = mp_manager.Lock()
TRAFFIC_LOCK_ADD_SECOND = mp_manager.Lock()
TRAFFIC_LOCK_DEL_SECOND = mp_manager.Lock()


def get_request_key(request):
    endpoint = request.match_dict.get('endpoint', '')
    return f'{request.remote_addr}/{endpoint}'


def set_brewing_state(request, brewing_state):
    POTS_BREWING[get_request_key(request)] = brewing_state


def get_brewing_state(request):
    return POTS_BREWING.get(get_request_key(request), False)


def increase_or_set(lock, dict_obj, key, default):
    lock.acquire()

    if key in dict_obj:
        value = dict_obj[key]
        value += 1
    else:
        value = default

    dict_obj[key] = value
    lock.release()
    return value


def increase_traffic_by_request(request):
    cur_second_int = int(time.time())
    request_key = get_request_key(request)

    # Clear old seconds (only if it's not already being cleared)
    if TRAFFIC_LOCK_DEL_SECOND.acquire():
        for second in TRAFFIC.keys():
            if second < cur_second_int:
                del TRAFFIC[second]

        TRAFFIC_LOCK_DEL_SECOND.release()

    TRAFFIC_LOCK_ADD_SECOND.acquire()
    # First time handling current second
    if cur_second_int not in TRAFFIC:
        cur_second_counter = mp_manager.dict()
        TRAFFIC[cur_second_int] = cur_second_counter
    # Another time handling current second
    else:
        cur_second_counter = TRAFFIC[cur_second_int]

    TRAFFIC_LOCK_ADD_SECOND.release()

    request_traffic = increase_or_set(TRAFFIC_LOCK_INCREASE, cur_second_counter, request_key, 1)

    # print(f'Increasing {request_key!r} from value {request_traffic} (second {cur_second_int})')

    return request_traffic


def slash(request):
    """
    :type request:
    """

    endpoint = request.match_dict.get('endpoint', '')

    if request.method == 'GET':
        return request.Response(
            code=200,
            text=HOME_HTML_CONTENT,
            headers={'Content-Type': 'text/html'}
        )

    if request.method == 'BREW':

        if endpoint == '':
            return request.Response(
                code=300,
                headers={'Alternates': TEA_ALTERNATES}
            )

        # Some pot
        elif endpoint in TEA_VARIANTS:

            # Wrong Content-Type
            if request.headers.get('Content-Type', '') != TEA_CONTENT_TYPE:
                return request.Response(
                    code=400,
                    headers={'Alternates': TEA_ALTERNATES}
                )

            is_brewing = get_brewing_state(request)

            # Start brewing
            if request.body == b'start':

                # Pot is busy - already brewing
                if is_brewing:
                    return request.Response(
                        code=503,
                        text='Pot is busy'
                    )

                # Make sure there is enough traffic for high traffic pot
                if endpoint == HIGH_TRAFFIC_VARIANT:
                    traffic = increase_traffic_by_request(request)

                    if traffic < MIN_REQUESTS_COUNT:
                        # FIXME: uvloop is unable to return status code 424
                        #        see https://github.com/squeaky-pl/japronto/issues/131
                        return request.Response(
                            code=424,
                            text=f'Traffic too low to brew "{endpoint}" tea: {traffic}/{MIN_REQUESTS_COUNT}'
                        )

                # Successfully start brewing
                set_brewing_state(request, True)

                return request.Response(
                    code=202,
                    text='Brewing'
                )

            # Stop brewing
            if request.body == b'stop':

                if not is_brewing:
                    return request.Response(
                        code=400,
                        text='No beverage is being brewed by this pot',
                    )

                client_email = request.headers.get('Email', '')

                if not client_email:
                    return request.Response(
                        code=400,
                        text='Please set "Email" header in your request to your email address'
                    )

                try:
                    email_client.send(
                        addr_from=SMTP_USER,
                        addr_to=EMAIL_RECEIVER,
                        subject=f'Someone has completed recruitment task v{__version__} - {client_email}',
                        message=f'Candidate has successfully brewed tea {endpoint!r} from IP {request.remote_addr}, '
                                f'using mail {client_email!r} and host {request.headers.get("Host", "Unknown")!r}.'
                    )
                except:
                    print(traceback.format_exc())
                    return request.Response(
                        code=500,
                        text='Something went wrong'
                    )

                # Successfully stop brewing
                set_brewing_state(request, False)

                return request.Response(
                    code=201,
                    text='Finished',
                )

            return request.Response(
                code=400
            )

        # Unknown pot
        else:
            return request.Response(
                code=503,
                text=f'"{endpoint}" is not supported for this pot'
            )

    else:
        return request.Response(code=405)


app = Application()
r = app.router

r.add_route('/', slash)
r.add_route('/{endpoint}', slash)


@click.command()
@click.option('--host', default=SERVER_HOST)
@click.option('--port', default=SERVER_PORT)
@click.option('--worker-num', default=SERVER_WORKER_NUM)
@click.option('--debug', default=False, is_flag=True)
def cli(host, port, worker_num, debug):
    click.echo('Starting server with following configuration:')
    click.echo('Host: %r' % host)
    click.echo('Port: %r' % port)
    click.echo('Worker number: %r' % worker_num)
    click.echo('Debug: %r' % debug)

    app.run(
        host=host,
        port=int(port),
        worker_num=int(worker_num) if worker_num else None,
        debug=debug
    )


if __name__ == '__main__':
    cli()
