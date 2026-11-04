"""Plan or invoke the shared CLI on Colab/Kaggle/Linux; never download model weights."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--action", choices=("plan", "setup", "preflight", "run"), default="plan")
    parser.add_argument("--profile", default="config/model_profiles/ornith_1_5_9b_vllm.json")
    for name in ("suite", "split-root", "state-root", "output-dir"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args(argv)
    run_options = ("suite", "split_root", "state_root", "output_dir")
    supplied = [getattr(args, name) is not None for name in run_options]
    if (any(supplied) and not all(supplied)) or (args.action == "run" and not all(supplied)):
        parser.error("run requires --suite, --split-root, --state-root, and --output-dir together")

    shared = ["uv", "run", "--frozen", "python", "-m", "datamate"]
    commands = {
        "setup": ["uv", "sync", "--frozen"],
        "preflight": [*shared, "preflight", "--profile", args.profile],
    }
    if all(supplied):
        command = [*shared, "run", "--profile", args.profile]
        for name in run_options:
            command.extend([f"--{name.replace('_', '-')}", getattr(args, name)])
        commands["run"] = command

    report = {
        "action": args.action,
        "repo_root": str(args.repo_root.resolve()),
        "bootstrap_python": sys.version.split()[0],
        "uv_available": shutil.which("uv") is not None,
        "commands": commands,
        "qualification": "unverified; shared preflight checks capacity only",
    }
    print(json.dumps(report, indent=2), flush=True)
    if args.action == "plan":
        return 0
    if not args.repo_root.is_dir():
        parser.error("--repo-root must be an existing checkout")
    if not shutil.which("uv"):
        parser.error("uv is missing; install uv explicitly, then rerun")
    try:
        result = subprocess.run(commands[args.action], cwd=args.repo_root, check=False)
    except OSError as error:
        parser.exit(1, f"cannot invoke shared command: {error}\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
