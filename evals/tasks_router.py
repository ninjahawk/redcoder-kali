"""Kali tool-router tasks: given a user's plain-English intent, does the model pick the RIGHT
tool and write a correct command? No setup, no files — the model outputs a command as text
(--no-shell means nothing runs). This is the 'identify the best tool + method' capability, and
the benchmark for the small-model experiment (how small a model still routes tools accurately).

Grading accepts any standard Kali tool that correctly serves the intent (graded on OUTCOME, not
one 'blessed' tool), plus the key argument/target — so a valid alternate tool still passes.
"""
from judge import matches

TGT = r"10\.66\.0\.20"


def _mk(pattern):
    def chk(r):
        ok = matches(r, pattern)
        return (1.0 if ok else 0.0, f"match:{ok}")
    return chk


TASKS = [
    dict(id="portscan", cat="router",
         prompt="I want to find the open ports and running service versions on the host 10.66.0.20. "
                "Give me the command.",
         check=_mk(r"\bnmap\b.*(-sV|-A|-sC).*" + TGT + r"|\bnmap\b.*" + TGT + r".*(-sV|-A|-sC)")),
    dict(id="dir_brute", cat="router",
         prompt="Brute-force the directories/paths on the web server at http://10.66.0.20. Command?",
         check=_mk(r"\b(gobuster|ffuf|dirb|dirbuster|feroxbuster|wfuzz)\b.*(10\.66\.0\.20|http)")),
    dict(id="hash_crack", cat="router",
         prompt="I have a file hashes.txt full of MD5 hashes. I want to crack them with a wordlist. "
                "Command?",
         check=_mk(r"\b(hashcat|john|johntheripper)\b.*hashes\.txt|\b(hashcat|john)\b.*(wordlist|rockyou|-m\s*0)")),
    dict(id="pcap_capture", cat="router",
         prompt="Capture network traffic on interface eth0 and save it to out.pcap. Command?",
         check=_mk(r"\b(tcpdump|tshark|dumpcap)\b.*(-w\s*out\.pcap|out\.pcap)")),
    dict(id="sqli", cat="router",
         prompt="Test the login form at http://10.66.0.20/login for SQL injection automatically. "
                "Command?",
         check=_mk(r"\bsqlmap\b.*(-u|--url).*(10\.66\.0\.20|http)")),
    dict(id="smb_enum", cat="router",
         prompt="Enumerate the SMB shares and users on the host 10.66.0.20. Command?",
         check=_mk(r"\b(enum4linux|smbclient|smbmap|crackmapexec|nxc|netexec|rpcclient)\b.*" + TGT)),
    dict(id="web_vuln", cat="router",
         prompt="Scan the web server at 10.66.0.20 for common web vulnerabilities and misconfigs. "
                "Command?",
         check=_mk(r"\b(nikto|wpscan|whatweb|nuclei)\b.*(10\.66\.0\.20|http|-h|-u|--url)")),
    dict(id="host_discovery", cat="router",
         prompt="Discover which hosts are alive on the 10.66.0.0/24 network. Command?",
         check=_mk(r"\b(nmap\s+-sn|arp-scan|netdiscover|fping)\b.*(10\.66\.0\.0/24|-l|--localnet)")),
    dict(id="listener", cat="router",
         prompt="Start a listener on port 4444 to catch an incoming reverse shell. Command?",
         check=_mk(r"\b(nc|ncat|netcat)\b.*-l.*4444|\b(nc|ncat)\b.*-l\w*p?\s*4444|\bmsfconsole\b|multi/handler")),
    dict(id="vuln_scan_nse", cat="router",
         prompt="Run nmap's default vulnerability-detection scripts against 10.66.0.20. Command?",
         check=_mk(r"\bnmap\b.*(--script|-sC).*" + TGT + r"|\bnmap\b.*" + TGT + r".*(--script|-sC)")),
    dict(id="ssh_brute", cat="router",
         prompt="Brute-force SSH login for user admin on 10.66.0.20 with a password list. Command?",
         check=_mk(r"\b(hydra|medusa|ncrack|patator)\b.*(ssh|10\.66\.0\.20)")),
    dict(id="extract_strings", cat="router",
         prompt="I have a suspicious binary called sample.bin. I want to see the readable ASCII "
                "strings inside it. Command?",
         check=_mk(r"\bstrings\b.*sample\.bin")),
]

# Establish the KALI context. Without this, a model running on Windows correctly (but unhelpfully
# for THIS test) answers with PowerShell equivalents (Get-SmbShare, Test-Connection, ...). On the
# real Kali stick the system prompt already carries this context (KALI_NOTES); here we make it
# explicit so the router test measures true Kali-tool knowledge regardless of the host OS.
_KALI = ("You are operating on Kali Linux in an isolated security lab. Answer with the exact "
         "Kali/Linux command-line tool and command (not PowerShell/Windows). ")
for _t in TASKS:
    _t["prompt"] = _KALI + _t["prompt"]
