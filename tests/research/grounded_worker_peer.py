"""Trusted test entrypoint: double only remote DNS/TLS I/O inside spawn.

No production test flags or transports. The real provider transport,
adapters, budget, workflow, receipt and repository remain in the path.
"""

import io
import json
import os
from pathlib import Path


def run_peer_stage(invocation, checkpoint_ref, publish_checkpoint):
    from finahinking.research.application_service import run_local_research_stage
    from finahinking.research.provider_transport import ProviderHttpTransport
    from tests.research.test_grounded_driver import ModelPeer

    root = Path(os.environ["FINATHINK_QUEUE_PATH"]).parent
    peer = ModelPeer()
    os.environ["FINAHINK_TEST_KEY"] = "test-only-model-value"

    class Connection:
        sock = None

        def request(self, method, path, body, headers):
            assert headers["Authorization"] == "Bearer test-only-model-value"
            payload = json.loads(body)
            with (root / "peer-wire.jsonl").open("a") as handle:
                handle.write(json.dumps(payload) + "\n")
            result = peer.request(method, path, headers=headers, json=payload, timeout=15)
            self.body = json.dumps(result["json"]).encode()

        def getresponse(self):
            response = io.BytesIO(self.body)
            response.status = 200
            response.headers = {}
            return response

        def close(self):
            pass

    ProviderHttpTransport._resolve = lambda self, host: ("1.1.1.1",)
    ProviderHttpTransport._connect = lambda self, host, address, timeout: Connection()
    try:
        return run_local_research_stage(invocation, checkpoint_ref, publish_checkpoint)
    except Exception:
        import traceback

        (root / "peer-error.log").write_text(traceback.format_exc())
        raise


def install_peer(service):
    from finahinking.research.runtime_service import ResearchRuntimeService

    service.runtime = ResearchRuntimeService(service.queue, stage_runners={"local_research": run_peer_stage}, autostart=False)
