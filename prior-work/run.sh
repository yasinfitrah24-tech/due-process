#!/usr/bin/env bash
# Detonation-container probe. Usage: ./run.sh [all|setup|sandbox|visits|isolation|measure|parallel|clean]
#   all = setup -> sandbox -> visits (steps 1-4); isolation = step 5; measure + parallel = step 6.
# Host needs: docker, python3, curl. The runner itself never gets internet access.
# Windows/Git Bash: export MSYS_NO_PATHCONV=1, and put a python3 shim on PATH if python3 is the Store stub.
set -euo pipefail
cd "$(dirname "$0")"

NET=dp-net
OUT=out
mkdir -p "$OUT"

# Constraints from step 4, applied to every runner container. No -e/--env-file: nothing from the host env.
LIMITS=(--network "$NET" --user pwuser --read-only --tmpfs /tmp:rw,size=256m
        --cap-drop ALL --security-opt no-new-privileges
        --memory 1g --memory-swap 1g --pids-limit 256 --shm-size 512m --cpus 1 --rm)

log() { printf '\n== %s\n' "$*" >&2; }

latest_tag() {
  curl -fsSL https://mcr.microsoft.com/v2/playwright/python/tags/list | python3 -c '
import json, re, sys
tags = [t for t in json.load(sys.stdin)["tags"] if re.fullmatch(r"v\d+\.\d+\.\d+-noble", t)]
print(max(tags, key=lambda t: tuple(int(x) for x in t[1:-6].split("."))))'
}

# Seccomp profiles live in the repo (seccomp/), per Playwright version:
#   playwright-<ver>.json         upstream utils/docker/seccomp_profile.json, verbatim (mode B)
#   playwright-<ver>-chroot.json  same + chroot allowed without CAP_SYS_CHROOT (mode B3, seccomp/patch_chroot.py)
profiles() {
  # shellcheck disable=SC1091
  source "$OUT/image.env"
  local ver=${PW_TAG%-*}
  SC_B=seccomp/playwright-$ver.json
  SC_B3=seccomp/playwright-$ver-chroot.json
}

setup() {
  PW_TAG=${PW_TAG:-$(latest_tag)}
  echo "PW_TAG=$PW_TAG" > "$OUT/image.env"
  log "runner image mcr.microsoft.com/playwright/python:$PW_TAG"

  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET"
  docker network inspect -f 'internal={{.Internal}}' "$NET"

  docker build -q -t dp-testsite testsite
  docker build -q -t dp-echo echo
  docker build -q --build-arg PW_TAG="$PW_TAG" -t dp-runner runner
  docker image inspect -f '{{index .RepoDigests 0}}' "mcr.microsoft.com/playwright/python:$PW_TAG" \
    > "$OUT/image.digest" 2>/dev/null || true

  docker rm -f dp-testsite dp-echo >/dev/null 2>&1 || true
  docker run -d --name dp-testsite --network "$NET" --network-alias testsite dp-testsite >/dev/null
  docker run -d --name dp-echo --network "$NET" --network-alias echo.test dp-echo >/dev/null

  # Only for a Playwright version that has no profile in the repo yet (fetched on the host, not by the runner).
  profiles
  if [[ ! -f $SC_B ]]; then
    curl -fsSL --retry 3 "https://raw.githubusercontent.com/microsoft/playwright/${PW_TAG%-*}/utils/docker/seccomp_profile.json" \
      -o "$SC_B.tmp" && mv "$SC_B.tmp" "$SC_B" && echo "fetched $SC_B: commit it" >&2 \
      || { echo "could not fetch seccomp profile for ${PW_TAG%-*}: modes B/B3 untested" >&2; rm -f "$SC_B.tmp"; }
  fi
  if [[ -f $SC_B && ! -f $SC_B3 ]]; then
    python3 seccomp/patch_chroot.py "$SC_B" "$SC_B3" && echo "generated $SC_B3: commit it" >&2
  fi
}

# launch-only attempt: name, chromium sandbox on/off, extra docker args...
attempt() {
  local name=$1 sb=$2; shift 2
  local rc=0
  docker run "${LIMITS[@]}" "$@" dp-runner --launch-only --sandbox "$sb" > "$OUT/sandbox-$name.json" 2> "$OUT/sandbox-$name.stderr" || rc=$?
  python3 - "$OUT/sandbox-$name.json" "$name" "$rc" "$*" <<'EOF'
import json, sys
path, name, rc, extra = sys.argv[1:]
try:
    r = json.load(open(path))
except Exception as e:
    print(f"{name}: rc={rc} no JSON ({e})"); sys.exit(1)
b = r["browser"]
if "launch_error" in b:
    lines = [l for l in b["launch_error"].splitlines() if l.strip()]
    print(f"{name}: FAIL rc={rc} docker+={extra!r} chromium_sandbox={b['sandbox_requested']}")
    for l in [l for l in lines if "FATAL" in l or "Check failed" in l or "sandbox" in l.lower()][:4] or lines[:4]:
        print(f"    {l[:220]}")
    sys.exit(1)
ev = b.get("sandbox_evidence") or {}
n, k = ev.get("chromium_processes", 0), ev.get("in_new_user_pid_net_ns", 0)
types = ",".join(sorted({p["type"] for p in ev.get("processes", []) if all(v for kk, v in p.items() if kk.startswith("new_"))}))
# With the sandbox requested, launching is not enough: some Chromium processes must sit in new namespaces.
ok = b["sandbox_requested"] == "off" or k > 0
print(f"{name}: {'OK' if ok else 'FAIL'} rc={rc} docker+={extra!r} chromium_sandbox={b['sandbox_requested']} {b['version']}")
print(f"    namespaces: {k}/{n} Chromium processes in new user+pid+net ns" + (f" ({types})" if types else ""))
sys.exit(0 if ok else 1)
EOF
}

sandbox() {
  log "sandbox matrix (least loosening first: A -> B -> B3 -> C; D diagnostic only)"
  profiles
  : > "$OUT/mode.env"
  local chosen=""
  {
    if attempt A-default-seccomp on; then chosen=A; fi
    if [[ -z $chosen && -f $SC_B ]] && \
       attempt B-playwright-seccomp on --security-opt "seccomp=$SC_B"; then chosen=B; fi
    if [[ -z $chosen && -f $SC_B3 ]] && \
       attempt B3-playwright-seccomp-chroot on --security-opt "seccomp=$SC_B3"; then chosen=B3; fi
    if [[ -z $chosen ]]; then
      # Diagnostic only, never chosen: tells whether seccomp alone is what blocks the sandbox.
      attempt D-unconfined-diagnostic on --security-opt seccomp=unconfined || true
      if attempt C-no-sandbox off; then chosen=C; fi
    fi
  } | tee "$OUT/sandbox-matrix.txt"
  # the subshell above cannot set $chosen; derive it from the matrix
  chosen=$(grep -m1 -oE '^(A|B3|B|C)-[a-z-]+: OK' "$OUT/sandbox-matrix.txt" | cut -d- -f1 || true)
  case $chosen in
    A) printf 'MODE=A\nSANDBOX=on\nEXTRA=()\n' > "$OUT/mode.env" ;;
    B) printf 'MODE=B\nSANDBOX=on\nEXTRA=(--security-opt seccomp=%s)\n' "$SC_B" > "$OUT/mode.env" ;;
    B3) printf 'MODE=B3\nSANDBOX=on\nEXTRA=(--security-opt seccomp=%s)\n' "$SC_B3" > "$OUT/mode.env" ;;
    C) printf 'MODE=C\nSANDBOX=off\nEXTRA=()\n' > "$OUT/mode.env" ;;
    *) echo "Chromium did not launch in any mode" >&2; return 1 ;;
  esac
  log "chosen mode: $chosen"; cat "$OUT/mode.env"
}

visit() {  # name url [visit.py args...]
  # shellcheck disable=SC1091
  source "$OUT/mode.env"
  docker run "${LIMITS[@]}" "${EXTRA[@]}" dp-runner --sandbox "$SANDBOX" "${@:3}" "$2" > "$OUT/$1.json"
}

visits() {
  log "visits"
  local rc=0
  visit start http://testsite/start
  python3 check.py start "$OUT/start.json" "$OUT/shots" || rc=1
  visit plain http://testsite/plain
  python3 check.py plain "$OUT/plain.json" "$OUT/shots" || rc=1
  echo "echo.test received $(docker logs dp-echo 2>&1 | grep -c . || true) request(s) (expected 0: nothing is submitted)"
  return $rc
}

# Step 5. Each check prints PASS/FAIL; non-zero exit if any fails.
isolation() {
  # shellcheck disable=SC1091
  source "$OUT/mode.env"
  log "isolation checks (mode $MODE)"
  local rc=0

  # 1. Clean env: a canary in the docker CLI's env must not reach the runner.
  DP_CANARY=probe-canary-not-a-secret visit iso-env http://testsite/plain --no-embed
  python3 - "$OUT/iso-env.json" <<'EOF' || rc=1
import json, sys
keys = json.load(open(sys.argv[1]))["env_keys"]
image = {"HOME", "HOSTNAME", "LANG", "LC_ALL", "PATH", "PLAYWRIGHT_BROWSERS_PATH", "PYTHONDONTWRITEBYTECODE"}
extra = sorted(set(keys) - image)
ok = not extra and "DP_CANARY" not in keys
print(f"  [{'PASS' if ok else 'FAIL'}] clean env: {keys}" + (f" unexpected={extra}" if extra else ""))
sys.exit(0 if ok else 1)
EOF

  # 2. No internet: raw sockets from the runner, and Chromium itself.
  docker run -i "${LIMITS[@]}" "${EXTRA[@]}" --entrypoint python3 dp-runner - < netprobe.py > "$OUT/iso-net.json"
  visit iso-net-chromium http://example.com/ --no-embed --timeout 30
  python3 - "$OUT/iso-net.json" "$OUT/iso-net-chromium.json" <<'EOF' || rc=1
import json, sys
ok = True
for r in json.load(open(sys.argv[1])):
    ok &= r["pass"]
    print(f"  [{'PASS' if r['pass'] else 'FAIL'}] net {r['target']} ({r['host']}:{r['port']}): {r['result'][:90]}")
v = json.load(open(sys.argv[2]))
got = [n for n in v.get("network", []) if n.get("status")]
c = bool(v["errors"]) and not got
ok &= c
msg = (v["errors"] or [{}])[0].get("message", "no error").splitlines()[0]
print(f"  [{'PASS' if c else 'FAIL'}] chromium http://example.com/: {msg[:90]}")
sys.exit(0 if ok else 1)
EOF

  # 3. /hang: the visit budget (60 s default) must end the visit, and the container with it.
  local t0 t1
  t0=$(date +%s.%N)
  visit iso-hang http://testsite/hang --no-embed
  t1=$(date +%s.%N)
  python3 - "$OUT/iso-hang.json" "$t0" "$t1" <<'EOF' || rc=1
import json, sys
r = json.load(open(sys.argv[1])); wall = float(sys.argv[3]) - float(sys.argv[2])
ok = r["timed_out"] and 55 <= r["timing"]["visit_s"] <= 65 and wall <= 75
print(f"  [{'PASS' if ok else 'FAIL'}] /hang: timed_out={r['timed_out']} visit_s={r['timing']['visit_s']} "
      f"total_s={r['timing']['total_s']} host_wall_s={wall:.1f} error={(r['errors'] or [{}])[0].get('message', '')[:80]!r}")
sys.exit(0 if ok else 1)
EOF

  # 4. No leftover runner containers (all of the above ran with --rm).
  local left
  left=$(docker ps -aq --filter ancestor=dp-runner | wc -l)
  if [[ $left -eq 0 ]]; then echo "  [PASS] no leftover dp-runner containers"; else
    echo "  [FAIL] $left leftover dp-runner container(s)"; docker ps -a --filter ancestor=dp-runner; rc=1; fi
  return $rc
}

# Step 6: N (default 5) sequential visits each of /start and /plain.
measure() {
  # shellcheck disable=SC1091
  source "$OUT/mode.env"
  local n=${N:-5} kind i t0 t1
  log "measure: $n runs each, mode $MODE"
  printf 'kind\trun\thost_wall_s\tcontainer_overhead_s\tlaunch_s\tvisit_s\ttotal_s\tmem_peak_mb\tcpu_s\n' > "$OUT/measure.tsv"
  for kind in start plain; do
    for i in $(seq 1 "$n"); do
      t0=$(date +%s.%N)
      visit "m-$kind-$i" "http://testsite/$kind" --no-embed
      t1=$(date +%s.%N)
      python3 - "$OUT/m-$kind-$i.json" "$kind" "$i" "$t0" "$t1" >> "$OUT/measure.tsv" <<'EOF'
import json, sys
path, kind, i, t0, t1 = sys.argv[1:]
r = json.load(open(path)); t = r["timing"]; wall = float(t1) - float(t0)
assert not r["errors"], r["errors"]
print("\t".join(map(str, [kind, i, round(wall, 3), round(wall - t["total_s"], 3), t["launch_s"], t["visit_s"],
                          t["total_s"], round(r["cgroup_memory_peak"]["bytes"] / 2**20, 1), round(r["cgroup_cpu_s"], 2)])))
EOF
    done
  done
  python3 - "$OUT/measure.tsv" <<'EOF'
import csv, statistics as st, sys
rows = list(csv.DictReader(open(sys.argv[1]), delimiter="\t"))
cols = ["host_wall_s", "container_overhead_s", "launch_s", "visit_s", "total_s", "mem_peak_mb", "cpu_s"]
print("kind   n  " + "  ".join(f"{c:>22}" for c in cols))
for kind in ("start", "plain"):
    rs = [r for r in rows if r["kind"] == kind]
    cells = []
    for c in cols:
        v = [float(r[c]) for r in rs]
        cells.append(f"{st.mean(v):8.2f} [{min(v):5.2f}-{max(v):5.2f}]")
    print(f"{kind:6} {len(rs)}  " + "  ".join(f"{x:>22}" for x in cells))
EOF
}

# Step 6 parallel check: K concurrent /start visits, all pinned to 2 CPUs (cpuset 0,1) to mimic a 2 vCPU VM.
parallel() {
  # shellcheck disable=SC1091
  source "$OUT/mode.env"
  local k i t0 t1
  log "parallel on cpuset 0,1, mode $MODE"
  printf 'k\tbatch_wall_s\tvisit_s_mean\tvisit_s_max\tmem_peak_mb_sum\tall_pass\n' > "$OUT/parallel.tsv"
  for k in ${KS:-1 2 3 4 6}; do
    t0=$(date +%s.%N)
    for i in $(seq 1 "$k"); do
      EXTRA_PAR=(--cpuset-cpus 0,1)
      docker run "${LIMITS[@]}" "${EXTRA[@]}" "${EXTRA_PAR[@]}" dp-runner --sandbox "$SANDBOX" --no-embed \
        http://testsite/start > "$OUT/p-$k-$i.json" &
    done
    wait
    t1=$(date +%s.%N)
    python3 - "$k" "$t0" "$t1" "$OUT" >> "$OUT/parallel.tsv" <<'EOF'
import json, subprocess, sys
k, t0, t1, out = int(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
rs = [json.load(open(f"{out}/p-{k}-{i}.json")) for i in range(1, k + 1)]
vs = [r["timing"]["visit_s"] for r in rs]
ok = all(subprocess.run([sys.executable, "check.py", "start", f"{out}/p-{k}-{i}.json"],
                        capture_output=True).returncode == 0 for i in range(1, k + 1))
mem = sum(r["cgroup_memory_peak"]["bytes"] for r in rs) / 2**20
print(f"{k}\t{t1 - t0:.2f}\t{sum(vs) / k:.2f}\t{max(vs):.2f}\t{mem:.0f}\t{ok}")
EOF
  done
  column -t "$OUT/parallel.tsv" 2>/dev/null || cat "$OUT/parallel.tsv"
}

clean() {
  docker rm -f dp-testsite dp-echo >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}

case ${1:-all} in
  setup) setup ;;
  sandbox) sandbox ;;
  visits) visits ;;
  isolation) isolation ;;
  measure) measure ;;
  parallel) parallel ;;
  clean) clean ;;
  all) setup; sandbox; visits ;;
  *) echo "usage: $0 [all|setup|sandbox|visits|isolation|measure|parallel|clean]" >&2; exit 64 ;;
esac
