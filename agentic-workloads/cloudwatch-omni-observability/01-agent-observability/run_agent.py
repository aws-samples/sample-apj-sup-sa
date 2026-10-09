"""Drive the support agent with sample conversations so traces reach Omni.

Run through ./run.sh, which wraps this script with `opentelemetry-instrument`.

    ./run.sh                          # 10 sessions, prompt v1
    ./run.sh --sessions 40 --loop     # keep generating traffic
    PROMPT_VERSION=v2 ./run.sh        # the fixed prompt
    ./run.sh --kind off_topic         # only off-topic conversations
"""

import argparse
import json
import pathlib
import random
import sys
import time
import uuid

from opentelemetry import trace

from agent.support_agent import build_agent

HERE = pathlib.Path(__file__).resolve().parent


def load_conversations(path, kind):
    conversations = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if kind:
        conversations = [c for c in conversations if c["kind"] == kind]
    if not conversations:
        sys.exit(f"No conversations of kind {kind!r} in {path}")
    return conversations


def run_session(conversation):
    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    agent = build_agent(session_id)
    print(f"\n[{session_id}] ({conversation['kind']})")
    for turn in conversation["turns"]:
        print(f"  user : {turn}")
        try:
            reply = str(agent(turn)).strip().replace("\n", " ")
        except Exception as exc:  # keep generating traffic; the failure is on the trace
            reply = f"<agent error: {exc}>"
        print(f"  agent: {reply[:160]}{'…' if len(reply) > 160 else ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=pathlib.Path, default=HERE / "prompts.jsonl")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--kind", choices=["in_scope", "off_topic", "mixed"])
    parser.add_argument("--loop", action="store_true", help="repeat until Ctrl-C")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between sessions")
    args = parser.parse_args()

    conversations = load_conversations(args.prompts, args.kind)
    try:
        while True:
            for _ in range(args.sessions):
                run_session(random.choice(conversations))  # nosec B311 - selects synthetic demo input
                time.sleep(args.sleep)
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        # Make sure buffered spans are exported before the process exits.
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()


if __name__ == "__main__":
    main()
