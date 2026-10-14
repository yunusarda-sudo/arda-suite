"""İzin katmanı — bir (araç, params) çağrısını auto | confirm | deny olarak sınıflar.

Policy is enforced centrally by the executor:
- auto: workspace-scoped tools explicitly registered as safe.
- confirm: writes, external data, and every shell command.
- deny: secrets and destructive patterns.

Dynamic secret-path and shell checks supplement the tool registry. Destructive
shell patterns use agent_tools.is_hard_denied as the shared policy source.
"""
from __future__ import annotations

import re
import shlex

from arda.agent_models import RiskLevel
# sensitive_path tek kaynak agent_tools'ta (alt katman) → araçlar + izin katmanı paylaşır,
# döngüsel import olmaz. Buradan yeniden ihraç: `from arda.agent_permission import sensitive_path` korunur.
from arda.agent_tools import REGISTRY, is_hard_denied, sensitive_path

# AppleScript `do shell script "<cmd>"` — gömülü kabuk komutunu yakala (kaçışlı tırnak dahil).
# Bu payload `classify_shell`'e verilir → sır-yolu/yıkıcı kalıplar AppleScript üzerinden de yakalanır.
_DO_SHELL_RE = re.compile(r'do\s+shell\s+script\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)


def classify_shell(command: str) -> RiskLevel:
    """Hard-deny destructive commands; require confirmation for every other shell call.

    The process runner does not provide an OS sandbox. Even apparently read-only
    commands can read outside the workspace or execute code through flags/plugins.
    """
    command = command or ""
    if is_hard_denied(command):
        return RiskLevel.DENY
    try:
        tokens = shlex.split(command)
    except ValueError:
        return RiskLevel.CONFIRM
    if not tokens:
        return RiskLevel.CONFIRM
    # Komut bir sır-yoluna dokunuyorsa (cat ~/.ssh/id_rsa) → reddet.
    if any(sensitive_path(t) for t in tokens[1:]):
        return RiskLevel.DENY
    return RiskLevel.CONFIRM


def decide(tool: str, params: dict | None) -> RiskLevel:
    """(araç, params) çağrısının risk seviyesi. executor bu kararla kapı tutar."""
    params = params or {}
    if tool == "safe_shell":
        return classify_shell(str(params.get("command", "")))
    if tool == "run_applescript":
        # AppleScript `do shell script` kabuk sandbox'ını ATLAR → gömülü komutu shell
        # sınıflandırıcıdan geçir: sır-yolu (cat ~/.ssh/id_rsa) ya da yıkıcı kalıp (rm -rf,
        # sudo) DENY döner → AppleScript bunları CONFIRM'a İNDİREMEZ. Komut literal değilse
        # (değişkenden kuruluysa) çıkaramayız; saf AppleScript gibi muhafazakâr CONFIRM kalır.
        script = str(params.get("script", ""))
        has_do_shell = "do shell script" in script.lower()
        # is_hard_denied SHELL kalıpları içindir (rm -rf, sudo, mail…) — saf AppleScript'e
        # uygulanırsa 'tell application "Mail"' gibi ifadeler yanlışlıkla eşleşir. Bu yüzden
        # yalnız gerçek `do shell script` varken (değişkenden kurulmuş komutları da kapsar).
        if has_do_shell and is_hard_denied(script):
            return RiskLevel.DENY
        # Literal gömülü komutları sır-yolu/sınıf için shell sınıflandırıcıdan geçir.
        for inner in _DO_SHELL_RE.findall(script):
            if classify_shell(inner) is RiskLevel.DENY:
                return RiskLevel.DENY
        return RiskLevel.CONFIRM
    # Sır-yolu yükseltmesi: path/file alanı bir credential'a işaret ediyorsa reddet.
    for key in ("path", "file"):
        if key in params and sensitive_path(params[key]):
            return RiskLevel.DENY
    spec = REGISTRY.get(tool)
    if spec is None:
        return RiskLevel.CONFIRM            # bilinmeyen araç → muhafazakâr
    return spec.risk
