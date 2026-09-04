# /// script
# requires-python = ">=3.12"
# dependencies = ["jsonschema==4.25.1", "openai==3.8.0"]
# ///
"""Validate HTTP fixture shapes and their interpretation by an unmodified client."""

import argparse
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
FIELDS = json.loads((ROOT / "fields.json").read_text())
CASES = json.loads((ROOT / "cases.json").read_text())


def valid_shape(name, body):
    schema = {**FIELDS, "$ref": f"#/$defs/{name}"}
    return Draft202012Validator(schema).is_valid(body)


def stream_content(frames):
    if not frames:
        raise ValueError("The stream has no terminal response.")
    identity = None
    content = []
    stopped = False
    failed = False
    for index, frame in enumerate(frames):
        if frame == "[DONE]":
            if index != len(frames) - 1 or not stopped or failed:
                raise ValueError("[DONE] must follow a successful finish chunk.")
            return "".join(content)
        if stopped or failed:
            raise ValueError("A response followed the terminal response.")
        if isinstance(frame, dict) and "error" in frame:
            if not valid_shape("error", frame) or index != len(frames) - 1:
                raise ValueError("An error must be the final response.")
            failed = True
            continue
        if not valid_shape("chunk", frame):
            raise ValueError("A chunk violates the registered field set.")
        current = (frame["id"], frame["created"], frame["model"])
        if identity is not None and current != identity:
            raise ValueError("Chunk identity changed during the response.")
        identity = current
        choice = frame["choices"][0]
        if choice["finish_reason"] == "stop":
            stopped = True
        else:
            content.append(choice["delta"]["content"])
    if failed:
        return None
    raise ValueError("The stream ended without a terminal response and [DONE].")


def check_projection(frames, reply):
    text = stream_content(frames)
    expected = ["".join(part["text"] for part in parts) for parts in reply["deltas"]]
    actual = [
        frame["choices"][0]["delta"]["content"]
        for frame in frames
        if isinstance(frame, dict)
        and "choices" in frame
        and "content" in frame["choices"][0]["delta"]
    ]
    if actual != expected:
        raise ValueError("Reply projection merged, split, or reordered output events.")
    if text != "".join(part["text"] for part in reply["terminal"]["content"]):
        raise ValueError("Streamed content differs from the terminal reply.")


def check_fixtures():
    Draft202012Validator.check_schema(FIELDS)
    request_ids = set()
    for case in CASES["requests"]:
        assert case["id"] not in request_ids, case["id"]
        request_ids.add(case["id"])
        assert valid_shape("request", case["body"]) == case["valid"], case["id"]
        if case["valid"]:
            messages = case["body"]["messages"]
            expected = {
                "content": [],
                "history": [
                    {
                        "role": message["role"],
                        "content": (
                            [{"type": "text", "text": message["content"]}]
                            if isinstance(message["content"], str)
                            else message["content"]
                        ),
                    }
                    for message in messages
                ],
            }
            assert case["projection"] == expected, case["id"]
    for name, body in CASES["responses"].items():
        assert valid_shape("error" if name.endswith("_error") else name, body), name

    expected_text = CASES["responses"]["completion"]["choices"][0]["message"]["content"]
    assert stream_content(CASES["streams"]["success"]) == expected_text
    assert stream_content(CASES["streams"]["failure"]) is None
    check_projection(CASES["streams"]["success"], CASES["reply"])

    merged = copy.deepcopy(CASES["streams"]["success"])
    merged[0]["choices"][0]["delta"]["content"] = "Hello world"
    del merged[1]
    split = copy.deepcopy(CASES["streams"]["success"])
    split[0]["choices"][0]["delta"]["content"] = "Hello"
    extra = copy.deepcopy(split[1])
    extra["choices"][0]["delta"]["content"] = " "
    split.insert(1, extra)
    for frames in (merged, split):
        assert stream_content(frames) == expected_text
        try:
            check_projection(frames, CASES["reply"])
        except ValueError:
            continue
        raise AssertionError("Projection accepted changed output-event boundaries")

    broken_completion = copy.deepcopy(CASES["responses"]["completion"])
    broken_completion["usage"] = {"total_tokens": 0}
    assert not valid_shape("completion", broken_completion), "extra response field"
    broken_error = copy.deepcopy(CASES["responses"]["provider_error"])
    del broken_error["error"]["code"]
    assert not valid_shape("error", broken_error), "missing error code"
    extended_error = copy.deepcopy(CASES["responses"]["provider_error"])
    extended_error["error"]["code"] = "org.example.unavailable"
    assert valid_shape("error", extended_error), "owned error code"
    extended_error["error"]["code"] = "com.1password.unavailable"
    assert valid_shape("error", extended_error), (
        "owned error code with numeric-start domain label"
    )
    extended_error["error"]["code"] = "rpc_failed"
    assert not valid_shape("error", extended_error), (
        "unregistered unqualified error code"
    )

    streams = CASES["streams"]
    changed_id = copy.deepcopy(streams["success"])
    changed_id[1]["id"] = "chatcmpl-other"
    missing_chunk_fields = copy.deepcopy(streams["success"])
    del missing_chunk_fields[0]["created"]
    broken_streams = {
        "missing-terminal": streams["success"][:2],
        "missing-finish": streams["success"][:2] + ["[DONE]"],
        "missing-done": streams["success"][:-1],
        "changed-id": changed_id,
        "incomplete-chunk": missing_chunk_fields,
        "success-after-error": streams["failure"] + streams["success"][-2:],
        "text-after-finish": streams["success"][:-1]
        + streams["success"][:1]
        + ["[DONE]"],
    }
    for name, frames in broken_streams.items():
        try:
            stream_content(frames)
        except ValueError:
            continue
        raise AssertionError(f"Broken stream accepted: {name}")
    return {
        "requests": len(request_ids),
        "responses": len(CASES["responses"]),
        "streams": len(streams),
        "response_mutants": 3 + len(broken_streams),
        "projection_mutants": 2,
    }


def check_client():
    from openai import APIError, APIStatusError, OpenAI, __version__

    requests = []
    mode = "success"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, streaming=False):
            if streaming:
                payload = "".join(
                    "data: "
                    + (frame if isinstance(frame, str) else json.dumps(frame))
                    + "\n\n"
                    for frame in body
                ).encode()
            else:
                payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header(
                "Content-Type", "text/event-stream" if streaming else "application/json"
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            if self.headers.get("Authorization") == "Bearer fixture-token":
                return True
            self.respond(401, CASES["responses"]["auth_error"])
            return False

        def do_GET(self):
            if self.authorized():
                if self.path == "/v1/models":
                    self.respond(200, CASES["responses"]["models"])
                else:
                    self.respond(404, CASES["responses"]["selection_error"])

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/v1/chat/completions":
                self.respond(404, CASES["responses"]["selection_error"])
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if not valid_shape("request", body):
                self.respond(400, CASES["responses"]["input_error"])
            elif body["model"] != "amplifier":
                self.respond(404, CASES["responses"]["selection_error"])
            elif body.get("stream", False):
                self.respond(200, CASES["streams"][mode], streaming=True)
            elif mode == "failure":
                self.respond(502, CASES["responses"]["provider_error"])
            else:
                self.respond(200, CASES["responses"]["completion"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    messages = [{"role": "user", "content": "Hello"}]
    try:
        with OpenAI(
            base_url=base_url, api_key="fixture-token", max_retries=0, timeout=5
        ) as client:
            reply = client.chat.completions.create(model="amplifier", messages=messages)
            assert requests[-1].get("stream", False) is False
            assert reply.choices[0].message.content == "Hello world"
            assert reply.choices[0].finish_reason == "stop"
            assert [model.id for model in client.models.list()] == ["amplifier"]
            stream = client.chat.completions.create(
                model="amplifier", messages=messages, stream=True
            )
            with stream:
                chunks = list(stream)
            assert (
                "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
                == reply.choices[0].message.content
            )
            assert chunks[-1].choices[0].finish_reason == "stop"

            for kwargs, status, code in [
                ({"model": "unknown"}, 404, "selector_rejected"),
                ({"model": "amplifier", "temperature": 0}, 400, "invalid_input"),
            ]:
                try:
                    client.chat.completions.create(messages=messages, **kwargs)
                except APIStatusError as error:
                    assert error.status_code == status and error.code == code
                    assert (
                        error.body
                        == CASES["responses"][
                            "selection_error" if status == 404 else "input_error"
                        ]["error"]
                    )
                else:
                    raise AssertionError(f"Client accepted HTTP {status} failure")

            mode = "failure"
            try:
                client.chat.completions.create(model="amplifier", messages=messages)
            except APIStatusError as error:
                assert error.status_code == 502 and error.code == "provider_failed"
                assert "Check provider availability before retrying." in error.message
            else:
                raise AssertionError("Client accepted provider failure")
            partial = []
            try:
                with client.chat.completions.create(
                    model="amplifier", messages=messages, stream=True
                ) as stream:
                    for chunk in stream:
                        partial.append(chunk.choices[0].delta.content or "")
            except APIError as error:
                assert error.code == "provider_failed"
                assert error.body == CASES["responses"]["provider_error"]["error"]
                assert partial == ["Partial"]
            else:
                raise AssertionError(
                    "Client accepted failed stream as a completed response"
                )
        with OpenAI(
            base_url=base_url, api_key="wrong-token", max_retries=0, timeout=5
        ) as client:
            try:
                client.models.list()
            except APIStatusError as error:
                assert error.status_code == 401
                assert error.body == CASES["responses"]["auth_error"]["error"]
            else:
                raise AssertionError("Client accepted an invalid token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return {
        "openai_version": __version__,
        "checks": 8,
        "network": "loopback fixture server",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client",
        action="store_true",
        help="Exercise fixtures through the OpenAI client over loopback HTTP.",
    )
    args = parser.parse_args()
    result = {"fixture_validation": check_fixtures()}
    if args.client:
        result["client_compatibility"] = check_client()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
