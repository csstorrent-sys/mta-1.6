#!/usr/bin/env python3
"""Eighth Somnis MTA 1.6 protocol-neutral client stability pass.

Guards CEGUI Direct3D invalidation during device-loss transitions (Alt+Tab,
fullscreen switches, screensaver/device resets). No network/protocol code is
modified.
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


replace_exact(
    "Client/gui/CGUI_Impl.cpp",
    '''void CGUI_Impl::Invalidate()\n{\n    reinterpret_cast<CEGUI::DirectX9Renderer*>(m_pRenderer)->preD3DReset();\n}\n''',
    '''void CGUI_Impl::Invalidate()\n{\n    try\n    {\n        reinterpret_cast<CEGUI::DirectX9Renderer*>(m_pRenderer)->preD3DReset();\n    }\n    catch (const CEGUI::Exception& exception)\n    {\n        // Do not let a GUI renderer exception unwind through the Direct3D/COM\n        // device-loss path. This commonly runs during Alt+Tab/display resets.\n        MessageBox(0, exception.getMessage().c_str(), "CEGUI Exception", MB_OK | MB_ICONERROR | MB_TOPMOST);\n        TerminateProcess(GetCurrentProcess(), 1);\n    }\n}\n''',
    "guard CEGUI preD3DReset during device loss",
)

print("Somnis safe client backports pass 8:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
