#!/usr/bin/env python3
"""Seventh Somnis MTA 1.6 protocol-neutral client stability pass.

Bounds repeated GTA streaming waits after persistent I/O failures so one bad
asset/model load cannot multiply into a long apparent client freeze. This is
client-only and does not change network protocol or server compatibility.
"""
from pathlib import Path

changed = []
skipped = []
already = []


def replace_exact(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    if not p.exists():
        skipped.append((label, path, "file missing"))
        return
    text = p.read_text(encoding="utf-8")
    if new in text:
        already.append((label, path))
        return
    if old not in text:
        skipped.append((label, path, "source pattern not found"))
        return
    p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    changed.append((label, path))


# CModelInfoSA::Request can re-enter LoadAllRequestedModels repeatedly on a
# persistent disk/streaming error. Cap the total blocking window instead of
# letting each retry add another multi-second stall.
replace_exact(
    "Client/game_sa/CModelInfoSA.cpp",
    '''    if (requestType == BLOCKING)\n    {\n        pGame->GetStreaming()->RequestModel(m_dwModelID, 0x16);\n        pGame->GetStreaming()->LoadAllRequestedModels(true, szTag);\n        if (!IsLoaded())\n        {\n            // Try 3 more times, final time without high priority flag\n            int iCount = 0;\n            while (iCount++ < 10 && !IsLoaded())\n            {\n                bool bOnlyPriorityModels = (iCount < 3 || iCount & 1);\n                pGame->GetStreaming()->LoadAllRequestedModels(bOnlyPriorityModels, szTag);\n            }\n''',
    '''    if (requestType == BLOCKING)\n    {\n        pGame->GetStreaming()->RequestModel(m_dwModelID, 0x16);\n        const uint32_t blockingStartTick = SharedUtil::GetTickCount32();\n        pGame->GetStreaming()->LoadAllRequestedModels(true, szTag);\n        if (!IsLoaded())\n        {\n            // Retry, but never let a persistent streaming I/O failure multiply\n            // into an arbitrarily long client freeze.\n            int iCount = 0;\n            while (iCount++ < 10 && !IsLoaded())\n            {\n                if ((SharedUtil::GetTickCount32() - blockingStartTick) > 10000)\n                    break;\n\n                bool bOnlyPriorityModels = (iCount < 3 || iCount & 1);\n                pGame->GetStreaming()->LoadAllRequestedModels(bOnlyPriorityModels, szTag);\n            }\n''',
    "cap total blocking model-load time",
)


# GTA's CdStreamSync wait can itself time out and then immediately be called again
# by outer loading loops. Keep the first diagnostic-sized wait, but shorten repeats
# for the same persistent failure so stalls do not accumulate into minutes.
replace_exact(
    "Client/multiplayer_sa/CMultiplayerSA_CrashFixHacks.cpp",
    '''void _cdecl DoWait(HANDLE hHandle)\n{\n    DWORD dwWait = 4000;\n    DWORD dwResult = WaitForSingleObject(hHandle, dwWait);\n    if (dwResult == WAIT_TIMEOUT)\n    {\n        AddReportLog(6211, SString("WaitForSingleObject timed out with %08x and %dms", hHandle, dwWait));\n''',
    '''void _cdecl DoWait(HANDLE hHandle)\n{\n    static DWORD s_consecutiveTimeouts = 0;\n    static DWORD s_lastCallTick = 0;\n\n    const DWORD now = SharedUtil::GetTickCount32();\n    if (s_lastCallTick != 0 && (now - s_lastCallTick) > 10000)\n        s_consecutiveTimeouts = 0;\n    s_lastCallTick = now;\n\n    const DWORD dwWait = (s_consecutiveTimeouts >= 1) ? 100 : 4000;\n    DWORD       dwResult = WaitForSingleObject(hHandle, dwWait);\n    if (dwResult == WAIT_TIMEOUT)\n    {\n        ++s_consecutiveTimeouts;\n        AddReportLog(6211, SString("WaitForSingleObject timed out with %08x and %dms (consecutive: %u)", hHandle, dwWait, s_consecutiveTimeouts));\n''',
    "shorten repeated CdStreamSync waits",
)

replace_exact(
    "Client/multiplayer_sa/CMultiplayerSA_CrashFixHacks.cpp",
    '''#endif\n        dwResult = WaitForSingleObject(hHandle, 1000);\n    }\n}\n''',
    '''#endif\n        if (dwWait >= 4000)\n        {\n            dwResult = WaitForSingleObject(hHandle, 1000);\n            if (dwResult != WAIT_TIMEOUT)\n                s_consecutiveTimeouts = 0;\n        }\n    }\n    else\n    {\n        s_consecutiveTimeouts = 0;\n    }\n}\n''',
    "reset adaptive streaming wait after recovery",
)

print("Somnis safe client backports pass 7:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
