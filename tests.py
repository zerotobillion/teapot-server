import unittest
import time
import threading
import multiprocessing
import asyncio

import requests
from aiohttp import ClientSession
import dotenv
import psutil


dotenv.load_dotenv('.env.test', override=True)


import server


def sleep_to_next_second():
    time_left_to_next_second = int(time.time() + 1) - time.time()
    time.sleep(time_left_to_next_second)


class FakeRequest:
    def __init__(self, remote_addr, endpoint):
        self.remote_addr = remote_addr
        self.match_dict = {'endpoint': endpoint}


class TestTrafficCounter(unittest.TestCase):
    def create_processes_to_increase_traffic(self, processes_count, request_ip, tea_variant, results_list):
        return [
            multiprocessing.Process(
                target=lambda: results_list.append(
                    server.increase_traffic_by_request(FakeRequest(request_ip, tea_variant))
                )
            )
            for _ in range(processes_count)
        ]

    def run_processes_with_next_second(self, processes):
        sleep_to_next_second()
        [t.start() for t in processes]
        [t.join() for t in processes]

    def test_increase_by_single_client_single_variant(self):
        processes_count = 10
        results = multiprocessing.Manager().list()

        processes = self.create_processes_to_increase_traffic(processes_count, '127.0.0.1', 'earl-gray', results)
        self.run_processes_with_next_second(processes)

        self.assertEqual(
            len(results),
            processes_count
        )
        self.assertEqual(
            sorted(results),
            list(range(1, processes_count + 1))
        )
        self.assertEqual(
            len(server.TRAFFIC),
            1
        )

    def test_increase_by_single_client_many_variants(self):
        processes_count = 10
        results = multiprocessing.Manager().list()

        processes = [
            *self.create_processes_to_increase_traffic(processes_count, '127.0.0.1', 'earl-gray', results),
            *self.create_processes_to_increase_traffic(processes_count, '127.0.0.1', 'english-breakfast', results)
        ]

        self.run_processes_with_next_second(processes)

        self.assertEqual(
            len(results),
            processes_count*2
        )
        self.assertEqual(
            set(results),
            set(range(1, processes_count + 1))
        )
        self.assertEqual(
            len(server.TRAFFIC),
            1
        )

    def test_increase_by_many_clients_single_variant(self):
        processes_count = 10
        results = multiprocessing.Manager().list()

        processes = [
            *self.create_processes_to_increase_traffic(processes_count, '127.0.0.1', 'earl-gray', results),
            *self.create_processes_to_increase_traffic(processes_count, '127.0.0.2', 'earl-gray', results)
        ]

        self.run_processes_with_next_second(processes)

        self.assertEqual(
            len(results),
            processes_count*2
        )
        self.assertEqual(
            set(results),
            set(range(1, processes_count + 1))
        )
        self.assertEqual(
            len(server.TRAFFIC),
            1
        )

    def test_increase_deletes_old_seconds(self):
        results = multiprocessing.Manager().list()

        for _ in range(3):
            time.sleep(1)

            self.create_processes_to_increase_traffic(1, '127.0.0.1', 'earl-gray', results)

            # Only 1 second in traffic recorded
            self.assertEqual(
                len(server.TRAFFIC),
                1
            )

            traffic_key = next(iter(server.TRAFFIC.keys()))

            # Only 1 variant in given second
            self.assertEqual(
                len(server.TRAFFIC[traffic_key]),
                1
            )


class TestPotsState(unittest.TestCase):
    def setUp(self):
        self.earl_grey_request = FakeRequest('127.0.0.1', 'earl-grey')
        self.earl_grey_another_request = FakeRequest('127.0.0.2', 'earl-grey')
        self.english_breakfast_request = FakeRequest('127.0.0.1', 'english-breakfast')

    def test_initial_state(self):
        self.assertEqual(
            server.get_brewing_state(self.earl_grey_request),
            False
        )
        self.assertEqual(
            server.get_brewing_state(self.earl_grey_another_request),
            False
        )
        self.assertEqual(
            server.get_brewing_state(self.english_breakfast_request),
            False
        )

    def test_start_brewing(self):
        server.set_brewing_state(self.earl_grey_request, True)

        self.assertEqual(
            server.get_brewing_state(self.earl_grey_request),
            True
        )
        self.assertEqual(
            server.get_brewing_state(self.earl_grey_another_request),
            False
        )
        self.assertEqual(
            server.get_brewing_state(self.english_breakfast_request),
            False
        )

    def test_stop_brewing(self):
        server.set_brewing_state(self.earl_grey_request, True)
        server.set_brewing_state(self.earl_grey_request, False)

        self.assertEqual(
            server.get_brewing_state(self.earl_grey_request),
            False
        )
        self.assertEqual(
            server.get_brewing_state(self.earl_grey_another_request),
            False
        )
        self.assertEqual(
            server.get_brewing_state(self.english_breakfast_request),
            False
        )


class TestServer(unittest.TestCase):
    SERVER_EXE_PATH = 'server.py'
    SERVER_TEST_PORT = 10000

    def setUp(self, worker_num=None, debug=True):

        def non_op_func(*args, **kwargs):
            pass

        server.email_client.send = non_op_func

        self.host = server.SERVER_HOST
        self.port = self.SERVER_TEST_PORT
        self.__class__.SERVER_TEST_PORT += 1

        self.base_url = f'http://{self.host}:{self.port}'

        args = [
            'python',
            'server.py',
            f'--host={self.host}',
            f'--port={self.port}',
        ]

        if worker_num:
            args.append(f'--worker-num={worker_num}')

        if debug:
            args.append('--debug')

        server_process = psutil.Popen(args)

        self.server_process = server_process

        for _ in range(100):
            try:
                self.request('GET', '/')
            except requests.ConnectionError:
                time.sleep(0.05)
                continue
            else:
                break

    def tearDown(self):
        server_processes = [self.server_process]
        server_processes.extend(self.server_process.children(recursive=True))

        for process in server_processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                continue

        psutil.wait_procs(server_processes, timeout=5)

        for process in server_processes:
            if process.is_running():
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    continue

        psutil.wait_procs(server_processes, timeout=5)

        return

    def request(self, method, endpoint, **kwargs):
        url = f'{self.base_url}{endpoint}'
        return requests.request(method.upper(), url, timeout=None, **kwargs)

    def test_invalid_method(self):
        bad_requests = [
            # GET, PUT, HEAD, DELETE, OPTIONS, PATCH, and TRACE
            # methods are not acceptable HTCPCP verbs
            self.request('PUT', '/'),
            self.request('HEAD', '/'),
            self.request('DELETE', '/'),
            self.request('OPTIONS', '/'),
            self.request('PATCH', '/'),
            self.request('TRACE', '/'),
            # Missing body
            # self.request('POST', '/'),
            # self.request('BREW', '/'),
            # self.request('WHEN', '/'),
        ]
        for response in bad_requests:
            self.assertEqual(
                response.status_code,
                405
            )

    def test_get_returns_home_page(self):
        with open('home.html', 'rb') as home_html_file:
            expected_home_content = home_html_file.read()

        for endpoint in ('/', '/whatever-endpoint'):
            response = self.request(
                'GET',
                endpoint,
            )
            self.assertEqual(
                response.status_code,
                200
            )
            self.assertEqual(
                response.headers.get('Content-Type'),
                'text/plain; charset=utf-8, text/html'
            )
            self.assertEqual(
                response.content,
                expected_home_content
            )

    def test_brew_no_pot(self):
        response = self.request(
            'BREW',
            '/',
            data='start'
        )

        self.assertEqual(
            response.status_code,
            300
        )
        self.assertEqual(
            response.headers['Alternates'],
            '{"/english-breakfast" {type message/teapot}}, '
            '{"/earl-grey" {type message/teapot}}'
        )

    def test_start_brew_unsupported_tea(self):
        response = self.request(
            'BREW',
            '/unsupported-tea',
            data='start',
            headers={'Content-Type': 'message/teapot'}
        )

        self.assertEqual(
            response.status_code,
            503
        )
        self.assertEqual(
            response.content,
            b'"unsupported-tea" is not supported for this pot'
        )

    def test_start_brew_english_breakfast_successfully(self):
        response = self.request(
            'BREW',
            '/english-breakfast',
            data='start',
            headers={'Content-Type': 'message/teapot'}
        )

        self.assertEqual(
            response.status_code,
            202
        )
        self.assertEqual(
            response.content,
            b'Brewing'
        )

    def test_start_brew_english_breakfast_but_its_busy(self):
        for _ in range(2):
            response = self.request(
                'BREW',
                '/english-breakfast',
                data='start',
                headers={'Content-Type': 'message/teapot'}
            )

        self.assertEqual(
            response.status_code,
            503
        )
        self.assertEqual(
            response.content,
            b'Pot is busy'
        )

    def test_stop_brew_english_breakfast_successfully(self):
        self.request(
            'BREW',
            '/english-breakfast',
            data='start',
            headers={'Content-Type': 'message/teapot'}
        )

        response = self.request(
            'BREW',
            '/english-breakfast',
            data='stop',
            headers={'Content-Type': 'message/teapot', 'Email': 'unittest@email.com'}
        )

        self.assertEqual(
            response.status_code,
            201
        )
        self.assertEqual(
            response.content,
            b'Finished'
        )

    def test_stop_brew_english_breakfast_but_its_not_started(self):
        response = self.request(
            'BREW',
            '/english-breakfast',
            data='stop',
            headers={'Content-Type': 'message/teapot'}
        )

        self.assertEqual(
            response.status_code,
            400
        )
        self.assertEqual(
            response.content,
            b'No beverage is being brewed by this pot'
        )

    # Earl-grey
    def test_start_brew_earl_grey_successfully(self):
        responses = []
        start_brew = lambda: responses.append(
            self.request(
                'BREW',
                '/earl-grey',
                data='start',
                headers={'Content-Type': 'message/teapot'}
            )
        )
        threads = [threading.Thread(target=start_brew) for _ in range(server.MIN_REQUESTS_COUNT)]
        sleep_to_next_second()
        [t.start() for t in threads]
        [t.join() for t in threads]

        success_responses = list(filter(lambda r: r.status_code == 202, responses))

        self.assertEqual(
            len(success_responses),
            1
        )

        response = success_responses[0]

        self.assertEqual(
            response.content,
            b'Brewing'
        )

    def test_start_brew_earl_grey_but_its_busy(self):
        responses = []
        start_brew = lambda: responses.append(
            self.request(
                'BREW',
                '/earl-grey',
                data='start',
                headers={'Content-Type': 'message/teapot'}
            )
        )
        threads = [threading.Thread(target=start_brew) for _ in range(server.MIN_REQUESTS_COUNT)]
        sleep_to_next_second()
        [t.start() for t in threads]
        [t.join() for t in threads]

        response = self.request(
            'BREW',
            '/earl-grey',
            data='start',
            headers={'Content-Type': 'message/teapot'}
        )

        self.assertEqual(
            response.status_code,
            503
        )
        self.assertEqual(
            response.content,
            b'Pot is busy'
        )

    def test_start_brew_earl_grey_but_traffic_is_too_low(self):
        responses = []
        start_brew = lambda: responses.append(
            self.request(
                'BREW',
                '/earl-grey',
                data='start',
                headers={'Content-Type': 'message/teapot'}
            )
        )
        threads = [threading.Thread(target=start_brew) for _ in range(server.MIN_REQUESTS_COUNT - 1)]
        sleep_to_next_second()
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(
            len(responses),
            server.MIN_REQUESTS_COUNT - 1
        )

        expected_messages = [
            f'Traffic too low to brew "earl-grey" tea: {traffic}/{server.MIN_REQUESTS_COUNT}'
            for traffic in range(1, server.MIN_REQUESTS_COUNT)
        ]

        for response in responses:

            self.assertEqual(
                response.status_code,
                424
            )
            self.assertIn(
                response.text,
                expected_messages
            )

            expected_messages = list(filter(lambda msg: msg != response.text, expected_messages))

        self.assertEqual(
            expected_messages,
            []
        )

    def test_start_brew_earl_grey_stress_test(self):
        requests_count = 10000
        server_workers = server.SERVER_WORKER_NUM
        max_expected_duration = 10

        self.tearDown()
        time.sleep(0.5)
        self.setUp(worker_num=server_workers, debug=False)

        async def run():
            url = f"{self.base_url}/earl-gray"
            tasks = []

            async with ClientSession() as session:
                for _ in range(requests_count):
                    task = asyncio.ensure_future(session.request('BREW', url, data='start'))
                    tasks.append(task)

                responses = asyncio.gather(*tasks)
                await responses

        loop = asyncio.get_event_loop()
        future = asyncio.ensure_future(run())

        start_time = time.time()
        loop.run_until_complete(future)
        duration = time.time() - start_time

        request_per_second = requests_count / duration

        print(f'\n!!! Stress test made {requests_count} in {duration:.3f} seconds ({request_per_second:.3f} requests '
              f'per second, using server {server_workers} workers)')

        self.assertLess(
            duration,
            max_expected_duration
        )

    def test_stop_brew_earl_grey_successfully(self):
        start_brew = lambda: self.request(
            'BREW',
            '/earl-grey',
            data='start',
            headers={'Content-Type': 'message/teapot'}
        )

        threads = [threading.Thread(target=start_brew) for _ in range(server.MIN_REQUESTS_COUNT)]
        sleep_to_next_second()
        [t.start() for t in threads]
        [t.join() for t in threads]

        response = self.request(
            'BREW',
            '/earl-grey',
            data='stop',
            headers={'Content-Type': 'message/teapot', 'Email': 'patryk.stachurski@email.com'}
        )

        self.assertEqual(
            response.status_code,
            201
        )
        self.assertEqual(
            response.content,
            b'Finished'
        )

    def test_stop_brew_earl_grey_but_its_not_started(self):
        response = self.request(
            'BREW',
            '/earl-grey',
            data='stop',
            headers={'Content-Type': 'message/teapot'}
        )

        self.assertEqual(
            response.status_code,
            400
        )
        self.assertEqual(
            response.content,
            b'No beverage is being brewed by this pot'
        )
