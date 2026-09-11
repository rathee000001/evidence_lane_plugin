"""Fixed PDF codec child. Input arrives only after its parent assigns ownership."""

import json
import os
import sys


def main():
    raw = sys.stdin.buffer.read(33_554_433)
    if len(raw) > 33_554_432:
        raise ValueError("input")
    request = json.loads(raw)
    if os.name != "nt":
        import resource

        memory = 6_442_450_944 if request["operation"] == "enrich" else 2_147_483_648
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    sys.path = request.pop("engine_import_roots") + sys.path

    def audit(event, arguments):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.bind"}:
            raise PermissionError("PDF codec network access is disabled")

    sys.addaudithook(audit)
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.pdf_workers import perform

    try:
        result = perform(request["operation"], request["arguments"])
        response = {"status": "ok", "result": result}
    except LaneError as error:
        response = {"status": "error", "code": error.code}
    except Exception:  # noqa: BLE001 - vendor text and input content never cross this boundary
        response = {"status": "error", "code": "PDF_NATIVE_OPERATION_FAILED"}
    output = json.dumps(
        response, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(output) > 67_108_864:
        output = b'{"status":"error","code":"PDF_OUTPUT_BUDGET"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
