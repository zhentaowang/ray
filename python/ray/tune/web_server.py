from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from ray.tune.error import TuneError, TuneManagerError
from ray.tune.variant_generator import generate_trials


class TuneClient(object):
    """Client to interact with ongoing Tune experiment.

    Requires server to have started running."""
    STOP = "STOP"
    ADD = "ADD"
    GET_LIST = "GET_LIST"
    GET_TRIAL = "GET_TRIAL"

    def __init__(self, tune_address):
        # TODO(rliaw): Better to specify address and port forward
        self._tune_address = tune_address
        self._path = "http://{}".format(tune_address)

    def get_all_trials(self):
        """Returns a list of all trials (trial_id, config, status)."""
        return self._get_response(
            {"command": TuneClient.GET_LIST})

    def get_trial(self, trial_id):
        """Returns the last result for queried trial."""
        return self._get_response(
            {"command": TuneClient.GET_TRIAL,
             "trial_id": trial_id})

    def add_trial(self, name, trial_spec):
        """Adds a trial of `name` with configurations."""
        # TODO(rliaw): have better way of specifying a new trial
        return self._get_response(
            {"command": TuneClient.ADD,
             "name": name,
             "spec": trial_spec})

    def stop_trial(self, trial_id):
        """Requests to stop trial."""
        return self._get_response(
            {"command": TuneClient.STOP,
             "trial_id": trial_id})

    def _get_response(self, data):
        payload = json.dumps(data).encode()
        response = requests.get(self._path, data=payload)
        parsed = response.json()
        return parsed


def RunnerHandler(runner):
    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):
            content_len = int(self.headers.get('Content-Length'), 0)
            raw_body = self.rfile.read(content_len)
            parsed_input = json.loads(raw_body.decode())
            status, response = self.execute_command(parsed_input)
            if status:
                self.send_response(200)
            else:
                self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps(
                response).encode())

        def trial_info(self, trial):
            if trial.last_result:
                result = trial.last_result._asdict()
            else:
                result = None
            info_dict = {
                "id": trial.trial_id,
                "trainable_name": trial.trainable_name,
                "config": trial.config,
                "status": trial.status,
                "result": result
            }
            return info_dict

        def execute_command(self, args):
            def get_trial():
                trial = runner.get_trial(args["trial_id"])
                if trial is None:
                    error = "Trial ({}) not found.".format(args["trial_id"])
                    raise TuneManagerError(error)
                else:
                    return trial

            command = args["command"]
            response = {}
            try:
                if command == TuneClient.GET_LIST:
                    response["trials"] = [self.trial_info(t)
                                          for t in runner.get_trials()]
                elif command == TuneClient.GET_TRIAL:
                    trial = get_trial()
                    response["trial_info"] = self.trial_info(trial)
                elif command == TuneClient.STOP:
                    trial = get_trial()
                    runner.request_stop_trial(trial)
                elif command == TuneClient.ADD:
                    name = args["name"]
                    spec = args["spec"]
                    for trial in generate_trials(spec, name):
                        runner.add_trial(trial)
                else:
                    raise TuneManagerError("Unknown command.")
                status = True
            except TuneError as e:
                status = False
                response["message"] = str(e)

            return status, response

    return Handler


class TuneServer(threading.Thread):

    DEFAULT_PORT = 4321

    def __init__(self, runner, port=None):

        threading.Thread.__init__(self)
        self._port = port if port else self.DEFAULT_PORT
        address = ('localhost', self._port)
        print("Starting Tune Server...")
        self._server = HTTPServer(
            address, RunnerHandler(runner))
        self.start()

    def run(self):
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()
