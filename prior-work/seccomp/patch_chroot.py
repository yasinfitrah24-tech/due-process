"""Derive the B3 seccomp profile from Playwright's: allow chroot without CAP_SYS_CHROOT. Nothing else changes.

Chromium's namespace sandbox chroots into /proc/self/fdinfo/ inside the new user namespace it creates, where it
holds CAP_SYS_CHROOT. Docker evaluates a rule's `includes.caps` against the container's capabilities (all
dropped), so the upstream cap-gated rule never applies and the chroot is refused by seccomp. Opening the syscall
does not widen what pwuser can do: outside its own user namespace the kernel still refuses chroot (no cap).

usage: python3 seccomp/patch_chroot.py <playwright profile> <out>
"""
import json
import sys


def main():
    src, dst = sys.argv[1:]
    with open(src) as f:
        p = json.load(f)
    rules = []
    for r in p["syscalls"]:
        if "chroot" in r["names"]:
            r = {**r, "names": [n for n in r["names"] if n != "chroot"]}
            if not r["names"]:
                continue
        rules.append(r)
    if len(rules) == len(p["syscalls"]) and all("chroot" not in r["names"] for r in p["syscalls"]):
        sys.exit(f"{src}: no chroot rule found, refusing to guess")
    rules.append({"names": ["chroot"], "action": "SCMP_ACT_ALLOW"})
    p["syscalls"] = rules
    with open(dst, "w", newline="\n") as f:
        json.dump(p, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
