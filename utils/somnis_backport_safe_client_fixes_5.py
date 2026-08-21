#!/usr/bin/env python3
"""Fifth Somnis MTA 1.6 protocol-neutral client stability pass.

Adds small defensive GUI/CEGUI guards selected from newer client fixes without
backporting the large rendering rewrite. No network/protocol code is touched.
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


def replace_all(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    if not p.exists():
        skipped.append((label, path, "file missing"))
        return
    text = p.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            already.append((label, path))
        else:
            skipped.append((label, path, "source pattern not found"))
        return
    count = text.count(old)
    p.write_text(text.replace(old, new), encoding="utf-8", newline="")
    changed.append((f"{label} ({count} occurrence(s))", path))


# Core settings can be applied while GUI windows are being rebuilt after a skin/
# locale change. Do not dereference a temporarily missing console.
replace_exact(
    "Client/core/CCore.cpp",
    '''void CCore::ApplyConsoleSettings()\n{\n    CVector2D vec;\n    CConsole* pConsole = m_pLocalGUI->GetConsole();\n\n    CVARS_GET("console_pos", vec);\n''',
    '''void CCore::ApplyConsoleSettings()\n{\n    CConsole* pConsole = m_pLocalGUI ? m_pLocalGUI->GetConsole() : nullptr;\n    if (!pConsole)\n        return;\n\n    CVector2D vec;\n    CVARS_GET("console_pos", vec);\n''',
    "guard console settings during GUI rebuild",
)

# MessageBox pumps the Windows message loop. A failed skin load can therefore
# re-enter SetSkin while its windows are half-destroyed. Block re-entrancy.
replace_exact(
    "Client/core/CGUI.cpp",
    '''void CLocalGUI::SetSkin(const char* szName)\n{\n    CVector2D consolePos, consoleSize;\n''',
    '''void CLocalGUI::SetSkin(const char* szName)\n{\n    static bool s_bInSetSkin = false;\n    if (s_bInSetSkin)\n        return;\n\n    struct SetSkinGuard\n    {\n        explicit SetSkinGuard(bool& value) : flag(value) { flag = true; }\n        ~SetSkinGuard() { flag = false; }\n        bool& flag;\n    } guard(s_bInSetSkin);\n\n    CVector2D consolePos, consoleSize;\n''',
    "prevent re-entrant skin rebuild",
)

# Locale changes need a live console because the existing code saves/restores its
# geometry. If windows are in teardown, defer by simply leaving this invocation.
replace_exact(
    "Client/core/CGUI.cpp",
    '''void CLocalGUI::ChangeLocale(const char* szName)\n{\n    bool guiWasLoaded = m_pMainMenu != NULL;\n''',
    '''void CLocalGUI::ChangeLocale(const char* szName)\n{\n    if (!m_pConsole)\n        return;\n\n    bool guiWasLoaded = m_pMainMenu != NULL;\n''',
    "guard locale change during GUI teardown",
)

replace_exact(
    "Client/core/CGUI.cpp",
    '''    if (CCore::GetSingleton().GetModManager()->IsLoaded())\n    {\n        CCore::GetSingleton().GetConsole()->Printf("Please disconnect before changing language");\n''',
    '''    if (CCore::GetSingleton().GetModManager()->IsLoaded())\n    {\n        if (CConsoleInterface* pConsole = CCore::GetSingleton().GetConsole())\n            pConsole->Printf("Please disconnect before changing language");\n''',
    "guard locale warning console access",
)

replace_exact(
    "Client/core/CGUI.cpp",
    '''            else\n            {\n                CCore::GetSingleton().GetConsole()->Printf("Please disconnect before changing skin");\n                cvars->Set("current_skin", m_LastSkinName);\n''',
    '''            else\n            {\n                if (CConsoleInterface* pConsole = CCore::GetSingleton().GetConsole())\n                    pConsole->Printf("Please disconnect before changing skin");\n                cvars->Set("current_skin", m_LastSkinName);\n''',
    "guard skin warning console access",
)

# Draw can run from a re-entrant Windows message pump while DestroyWindows has
# temporarily removed the GUI objects. Skip that frame instead of dereferencing them.
replace_exact(
    "Client/core/CGUI.cpp",
    '''    CGame*      pGame = CCore::GetSingleton().GetGame();\n    SystemState systemState = pGame->GetSystemState();\n    CGUI*       pGUI = CCore::GetSingleton().GetGUI();\n\n    // Update mainmenu stuff\n    m_pMainMenu->Update();\n''',
    '''    CGame*      pGame = CCore::GetSingleton().GetGame();\n    SystemState systemState = pGame->GetSystemState();\n    CGUI*       pGUI = CCore::GetSingleton().GetGUI();\n\n    if (!m_pMainMenu || !m_pChat || !m_pDebugView)\n        return;\n\n    // Update mainmenu stuff\n    m_pMainMenu->Update();\n''',
    "skip GUI draw while windows are half-destroyed",
)

# Catch the CEGUI base exception by const reference: derived font-loading errors are
# handled too and exception objects are not sliced/copied.
replace_all(
    "Client/gui/CGUI_Impl.cpp",
    '''catch (CEGUI::InvalidRequestException e)''',
    '''catch (const CEGUI::Exception& e)''',
    "broaden font-loading CEGUI exception handling",
)

replace_all(
    "Client/gui/CGUI_Impl.cpp",
    '''catch (CEGUI::RendererException& exception)''',
    '''catch (const CEGUI::Exception& exception)''',
    "broaden renderer restore CEGUI exception handling",
)

print("Somnis safe client backports pass 5:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
